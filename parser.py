from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from typing import Optional

from PyPDF2 import PdfReader

# Excel support — openpyxl for .xlsx, xlrd for .xls
try:
    import openpyxl
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

# ── Shared constants ──────────────────────────────────────────────────────────
MONTH_ABBR = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
              7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
MONTH_MAP  = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
              "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
MONTH_NUM  = {v.lower(): str(k).zfill(2) for k, v in MONTH_ABBR.items()}

MONEY_RE   = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})*|\d+)\.\d{2}(?!\d)")
ZENITH_ROW = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(.*)$")
OPAY_ROW   = re.compile(
    r"^(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+(20\d{2})\s+\d{2}:\d{2}:\d{2}", re.I,
)

# ── FairMoney parser constants ────────────────────────────────────────────────
# Transaction anchor: DD/MM/YYYY  REFNUM  [+/-] ₦ AMOUNT  ₦ BALANCE[Narration]
_FM_TX = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+\d+\s+"    # date  reference-number
    r"([+\-])\s*₦\s*([\d,]+\.\d{2})\s+" # sign  amount
    r"₦\s*[\d,]+\.\d{2}"                 # balance (consumed, not captured)
    r"(.*)"                               # narration start (may be empty or run-on)
)
# Lines that are page-header boilerplate to skip during narration accumulation
_FM_HEADER = re.compile(
    r"^(?:FairMoney MFB|Licensed by CBN|28 Pade Odanye|Phone:|Email:|"
    r"Account number|Date Reference|number|Transaction|details|"
    r"Credit Debit Account|balance|Opening Balance|Total Deposits|"
    r"Total Withdrawals|Closing Balance|Page \d+ of \d+)",
    re.I,
)
# Pure-money lines (e.g. "₦ 62.00") and date-range lines to skip
_FM_MONEY_ONLY = re.compile(r"^₦\s*[\d,]+\.\d{2}$")
_FM_DATE_RANGE = re.compile(r"^\d{2}/\d{2}/\d{4}\s+-\s+\d{2}/\d{2}/\d{4}$")


# ── Data classes ──────────────────────────────────────────────────────────────
@dataclass
class CreditAccount:
    subscriber_name: str
    account_number: str
    account_status: str
    facility_classification: str
    instalment_amount: float
    outstanding_balance: float
    loan_duration_days: str
    tenor_months: Optional[int]
    monthly_obligation: float
    derived_from_balance: bool
    include_in_obligation: bool
    is_closed: bool
    is_bad_credit: bool


# ════════════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════════════
def ym_label(ym: str) -> str:
    year, month = ym.split("-")
    return f"{MONTH_ABBR[int(month)]} {year[-2:]}"


def _empty_bucket() -> dict:
    return {"gross": 0.0, "count": 0, "self_transfer": 0.0,
            "reversal": 0.0, "non_business": 0.0, "loan_disbursal": 0.0,
            "real_credit": 0.0}


def _parse_currency(v: str) -> float:
    return float(re.sub(r"[^\d.]", "", str(v or "")) or 0)


def _get_tenor_months(raw: str) -> Optional[int]:
    digits = int(re.sub(r"[^\d]", "", str(raw)) or 0)
    return max(1, -(-digits // 30)) if digits > 0 else None


# ════════════════════════════════════════════════════════════════════════════
# CLASSIFICATION  — self-transfer bug fix
# ════════════════════════════════════════════════════════════════════════════
def classify_credit(narration: str, account_name: str = "") -> tuple[str, str]:
    """
    Classify a credit transaction into one of:
      self_transfer  — OWealth, savings platforms, own-name transfers
      reversal       — RSVL, REV, refund, chargeback, dispute
      loan_disbursal — Loan apps, disbursement keywords
      non_business   — Gambling, betting, salary, allowance, support, contribution
      real_credit    — Genuine usable income

    SELF-TRANSFER FIX: Only flags self-transfer when the account holder name
    appears as the SENDER (60 chars after the transfer-from verb), NOT as the
    recipient. This prevents false flags like 'Transfer from X TO EMMANUEL'.

    CONTRIBUTION: ALL "contribution" credits are excluded (non_business) —
    whether from the account holder or from outsiders. Contributions (ajo,
    esusu, cooperative, group savings) are not verifiable business income.

    OVERDRAFT: Credits tagged as overdraft, OD credit, or facility drawdown
    are excluded as loan_disbursal — they are borrowed funds, not income.
    """
    text = narration.lower()

    # ── 1. OWealth / savings wallet round-trips ───────────────────────────
    owealth_kw = [
        "owealth withdrawal", "owealth deposit", "owealth interest",
        "auto-save to owealth", "savings withdrawal", "owealth balance",
        "owealth credit",
    ]
    if any(k in text for k in owealth_kw):
        return "self_transfer", "OWealth internal round-trip"

    # ── 2. Savings platforms ──────────────────────────────────────────────
    savings_kw = [
        "piggyvest", "piggy vest", "piggy bank",
        "cowrywise", "cowry wise",
        "kuda save", "kuda vault",
    ]
    if any(k in text for k in savings_kw):
        return "self_transfer", "Savings platform round-trip"

    # ── 3. Overdraft / facility drawdown → loan_disbursal ────────────────
    # Check BEFORE reversals: some OD narrations contain "reversal" in them
    # (e.g. "overdraft credit limit reversal") but are still borrowed funds.
    if re.search(
        r"\boverdraft\b|\bod\s+credit\b|\bod\s+limit\b|\bod\s+utiliz",
        text,
    ):
        return "loan_disbursal", "Overdraft credit"
    if re.search(
        r"\bfacility\s+drawdown\b|\bcredit\s+facility\b|\bod\s+reversal\b",
        text,
    ):
        return "loan_disbursal", "Facility/OD drawdown"

    # ── 4. Reversals ──────────────────────────────────────────────────────
    if re.search(r"\*+rsvl|\brsvl\b|\brev\b|\brev-\b", text):
        return "reversal", "RSVL/REV reversal marker"
    if any(k in text for k in [
        "reversal", "refund", "chargeback", "chargbk",
        "dispute", "clawback", "returned funds", "charge back",
    ]):
        return "reversal", "Reversal keyword"

    # ── 4. Loan disbursements ─────────────────────────────────────────────
    loan_exact = [
        "fairmoney", "carbon loan", "branch loan", "branch limit",
        "palmcredit", "palm credit", "aella credit", "aella",
        "kiakia", "kia kia", "quickcheck", "quick check",
        "migo ", "lidya", "zedvance", "creditwave", "creditmfb",
        "renmoney", "easemoni", "okash", "xcredit", "newcredit",
        "fast credit", "page financ", "lendigo", "specta",
        "loans by sterling", "credit direct", "gtbank loan",
        "access loan", "uba loan", "first bank loan",
        "zenith loan", "wema loan", "fcmb loan",
        # FairMoney internal credits (interest/wallet round-trips on FairMoney accounts)
        "incoming fairmoney",
    ]
    loan_regex = [
        r"\bloan\s+disburs", r"\bdisbursement\b", r"\bcredit\s+disburs",
        r"\bloan\s+credit\b", r"\bloan\s+repay",
    ]
    if any(k in text for k in loan_exact):
        return "loan_disbursal", "Loan app keyword"
    for pat in loan_regex:
        if re.search(pat, text):
            return "loan_disbursal", f"Loan pattern: {pat}"

    # ── 5. Self-transfer: own-name detection ─────────────────────────────
    if account_name and len(account_name) > 4:
        name_parts = [p for p in account_name.lower().split() if len(p) > 3]
        if name_parts:
            verb_m = re.search(
                r"\b(transfer from|transferred from|trf from|trf frm|"
                r"tfr from|transfer frm|trfr from)\b",
                text,
            )
            if verb_m:
                raw_window = text[verb_m.end(): verb_m.end() + 70]
                sender_window = re.split(r"\s+to\s+|\s*\|\s*", raw_window)[0]
                matched = sum(1 for p in name_parts if p in sender_window)
                if matched >= 2:
                    return "self_transfer", "Own-name sender detected"
            if "myself" in text or "| myself" in text or "/myself" in text:
                return "self_transfer", "Explicit self-transfer (myself)"

    # ── 5b. Contributions — ALL excluded regardless of sender ─────────────
    # Contributions (ajo, esusu, cooperative, group savings) are not
    # verifiable business income whether they come from the account holder
    # or from third parties. Classify as non_business so they are excluded.
    if re.search(r"\bcontribution\b|\bcontrib\b|\bajo\b|\besusu\b|\bcooperative\b", text):
        return "non_business", "Contribution/group-savings credit"

    # ── 6. Gambling / betting ─────────────────────────────────────────────
    betting_kw = [
        # ── Major Nigerian platforms ──────────────────────────────────────
        "sportybet", "sporty bet", "sporty|", "sporty internet",
        "bet9ja", "bet 9ja", "betnaija", "bet naija",
        "1xbet", "1x bet", "1x-bet",
        "betking", "bet king",
        "betway",
        "merrybet", "merry bet",
        "nairabet", "naira bet",
        "baba ijebu", "baba-ijebu",
        "naijabet",
        "lotto ", "lottomania", "premier lotto", "golden chance lotto",
        "supabets", "supa bets",
        "cloudbet",
        "msport", "m-sport",
        "bangbet", "bang bet",
        "parimatch", "pari match",
        "betboro",
        "betwinner", "bet winner",
        "22bet", "22 bet",
        "melbet", "mel bet",
        "betfair",
        "bet365",
        "frapapa",
        "linebet", "line bet",
        "xbet",
        "accessbet", "access bet",
        "wazobet", "wazobia bet",
        "betland",
        "surebet247", "sure bet",
        "betpawa", "bet pawa",
        "10bet", "ten bet",
        "nosporbet",
        "helabet", "hela bet",
        "premierbet", "premier bet",
        # ── Generic gambling signals ──────────────────────────────────────
        "casino ", "jackpot ", "betting winnings", "bet winnings",
        "winnings payout", "betting credit", "bet credit",
        "slot winnings", "poker winnings",
    ]
    if any(k in text for k in betting_kw):
        return "non_business", "Gambling/betting keyword"

    # ── 8. Non-business inflows ───────────────────────────────────────────
    non_biz_exact = [
        "salary", "salaries",
        "allowance",
        " support ",
        "monthly stipend", "stipend",
        "upkeep",
    ]
    if any(k in text for k in non_biz_exact):
        return "non_business", "Non-business keyword"

    return "real_credit", "Usable credit"


def add_credit(buckets: dict, ym: str, amount: float,
               narration: str, account_name: str = "") -> None:
    if not ym or not amount or amount <= 0:
        return
    if ym not in buckets:
        buckets[ym] = _empty_bucket()
    buckets[ym]["gross"]  += amount
    buckets[ym]["count"]  += 1
    cat, _ = classify_credit(narration, account_name)
    buckets[ym][cat] = buckets[ym].get(cat, 0.0) + amount


# ════════════════════════════════════════════════════════════════════════════
# PDF TEXT EXTRACTION
# ════════════════════════════════════════════════════════════════════════════
def extract_pdf_text(pdf_bytes: bytes, password: str = "") -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    if reader.is_encrypted:
        if reader.decrypt(password or "") == 0:
            raise ValueError("Incorrect or missing PDF password.")
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ════════════════════════════════════════════════════════════════════════════
# BANK DETECTION
# ════════════════════════════════════════════════════════════════════════════
def detect_bank(text: str) -> str:
    t = text.lower()
    if "fairmoney mfb" in t or "fairmoney microfinance" in t:
        return "FairMoney"
    if "opay digital" in t or "wallet account" in t or "9payment service" in t:
        return "OPay"
    # ── mybankStatement-format banks ─────────────────────────────────────
    # These banks all share the mybankStatement PDF engine which renders
    # explicit Debit/Credit columns and hyphen or slash date separators.
    # They MUST all be checked BEFORE the generic "mybankstatement" check
    # below, otherwise they'd get misrouted to parse_zenith().
    if "guaranty trust" in t or "gtbank" in t or "gt bank" in t or "gtco" in t:
        return "GTBank"
    if "access bank" in t:
        return "Access"
    if "first bank" in t or "firstbank" in t:
        return "FirstBank"
    if "united bank for africa" in t or ("uba" in t and "mybankstatement" in t):
        return "UBA"
    if "fidelity bank" in t:
        return "Fidelity"
    if "union bank" in t:
        return "Union"
    if "stanbic ibtc" in t or "stanbic" in t:
        return "Stanbic"
    if "fcmb" in t or "first city monument bank" in t:
        return "FCMB"
    if "wema" in t:
        return "Wema"
    if "sterling bank" in t:
        return "Sterling"
    # ── Zenith (mybankstatement is Zenith-specific only after above ruled out) ──
    if "date posted" in t and "value date" in t and "zenith" in t:
        return "Zenith_Corporate"
    if "mybankstatement" in t or "tran date value date narration" in t:
        return "Zenith"
    if "moniepoint mfb" in t or "moniepoint microfinance" in t:
        return "Moniepoint"
    if "kuda mf bank" in t or "kudabank" in t:
        return "Kuda"
    if "palmpay" in t:
        return "PalmPay"
    if "access bank" in t:
        return "Access"
    if "united bank for africa" in t:
        return "UBA"
    if "first bank" in t or "firstbank" in t:
        return "FirstBank"
    if "zenith" in t:
        return "Zenith"
    if "sterling bank" in t:
        return "Sterling"
    if "fcmb" in t or "first city monument bank" in t:
        return "FCMB"
    if "wema" in t:
        return "Wema"
    if "fidelity bank" in t:
        return "Fidelity"
    if "stanbic ibtc" in t or "stanbic" in t:
        return "Stanbic"
    if "union bank" in t:
        return "Union"
    return "Unknown"


# ════════════════════════════════════════════════════════════════════════════
# BANK PARSERS
# ════════════════════════════════════════════════════════════════════════════
def _extract_account_name(full_text: str) -> str:
    for pat in [
        r"Account Name\s+([A-Z][A-Z ]{4,})",
        r"ACCOUNT NAME[:\s]+([A-Z][A-Z ]{4,})",
        r"Account Name[:\s]+([A-Z][A-Z ]{4,})",
        r"Name\s+([A-Z][A-Z ]{4,})",
    ]:
        m = re.search(pat, full_text, re.I)
        if m:
            return m.group(1).strip()
    return ""


def parse_opay(full_text: str) -> tuple[dict, str]:
    buckets: dict = {}
    account_name = _extract_account_name(full_text)
    for line in full_text.splitlines():
        line = line.strip()
        m = OPAY_ROW.match(line)
        if not m:
            continue
        day, mon, year = m.group(1), m.group(2), m.group(3)
        ym = f"{year}-{MONTH_NUM[mon.lower()]}"
        rest = line[m.end():].strip()
        rest = re.sub(
            r"^\d{2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+20\d{2}\s*",
            "", rest, flags=re.I,
        )
        money = list(MONEY_RE.finditer(rest))
        if len(money) < 2:
            continue
        amount_m   = money[-2]
        amount     = float(amount_m.group().replace(",", ""))
        before_amt = rest[:amount_m.start()]
        if not re.search(r"--\s*$", before_amt.rstrip()):
            continue
        narration = re.sub(r"--\s*$", "", before_amt).strip()
        narration = re.sub(r"\s+Mobile\S*$", "", narration).strip()
        add_credit(buckets, ym, amount, narration, account_name)
    return buckets, account_name


def parse_zenith(full_text: str) -> tuple[dict, str]:
    buckets: dict = {}
    account_name = _extract_account_name(full_text)
    in_tx = False
    current: list[str] = []

    def process(lines: list[str]) -> None:
        full = " ".join(lines)
        m = ZENITH_ROW.match(full)
        if not m:
            return
        parts = m.group(1).split("/")
        ym    = f"{parts[2]}-{parts[1]}"
        rest  = m.group(3)
        money = list(re.finditer(r"[\d,]+\.\d{2}", rest))
        if len(money) < 2:
            return
        amount_m  = money[-2]
        amount    = float(amount_m.group().replace(",", ""))
        narration = rest[:amount_m.start()].strip()
        lower     = narration.lower()
        is_rev = bool(re.search(r"\*+rsvl\b|\brsvl\b", lower))
        is_cr  = bool(re.search(r"\b(?:nip\s*cr|cip\s*cr|inflow|etz\s+inflow)\b", lower)) or is_rev
        is_chg = bool(re.search(r"\b(?:stamp duty|sms alert|vat charge|charge\+vat|levy)\b", lower))
        if not is_cr or (is_chg and not is_rev):
            return
        add_credit(buckets, ym, amount, narration, account_name)

    for line in full_text.splitlines():
        line = line.strip()
        if "Tran Date Value Date Narration" in line:
            in_tx = True
            continue
        if not in_tx:
            continue
        if ZENITH_ROW.match(line):
            if current:
                process(current)
            current = [line]
        elif current and not line.startswith(("mybankStatement", "Tran Date")):
            current.append(line)
    if current:
        process(current)
    return buckets, account_name


def parse_gtbank(full_text: str) -> tuple[dict, str]:
    """
    Parser for all mybankStatement-engine banks: GTBank/GTCO, Access Bank,
    First Bank, UBA, Fidelity, Union Bank, Stanbic IBTC, FCMB, Wema, Sterling.

    These banks share the mybankStatement PDF platform which produces a
    consistent format with explicit Debit and Credit columns:

        Tran. date   Value date   Transaction details   Debit   Credit   Balance
        10-01-2025   10-01-2025   TRANSFER FROM FATHIA  0.00    50,000   129,678.71

    Key characteristics:
    - Date separators vary by bank/vintage: hyphens (10-01-2025) or slashes (10/01/2025)
    - Explicit Debit/Credit columns — credit rows have 0.00 in Debit column
    - Narrations are multi-line (continuation lines carry no date)
    - mybankStatement watermark appears on every page

    Credits are identified by the three-number tail (debit, credit, balance)
    where debit == 0.00 and credit > 0. This is more reliable than balance-
    delta inference (parse_generic) which breaks across page boundaries and
    on multi-line narrations.
    """
    buckets: dict = {}
    account_name = _extract_account_name(full_text)

    # GTBank date format in transactions: DD-MM-YYYY or DD/MM/YYYY
    GTB_DATE = re.compile(r"^(\d{2}[-/]\d{2}[-/]\d{4})")
    MONEY3   = re.compile(r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$")

    in_tx          = False
    pending_ym     = ""
    pending_narr   = ""
    pending_credit = 0.0

    def _flush() -> None:
        nonlocal pending_ym, pending_narr, pending_credit
        if pending_credit > 0:
            add_credit(buckets, pending_ym, pending_credit,
                       pending_narr.strip(), account_name)
        pending_ym = pending_narr = ""
        pending_credit = 0.0

    for line in full_text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Detect transaction section header
        if re.search(r"tran\.?\s*date|value\s*date|transaction\s*details", line, re.I):
            in_tx = True
            continue
        # Skip mybankStatement watermark lines and page headers
        if re.search(r"mybankstatement|guaranty trust|gtbank|rc no\.", line, re.I):
            continue

        if not in_tx:
            continue

        date_m = GTB_DATE.match(line)
        if date_m:
            _flush()
            raw_date = date_m.group(1).replace("-", "/")
            parts    = raw_date.split("/")
            pending_ym = f"{parts[2]}-{parts[1]}"

            # Check if this line already has all 3 money amounts (debit/credit/balance)
            m3 = MONEY3.search(line)
            if m3:
                debit  = float(m3.group(1).replace(",", ""))
                credit = float(m3.group(2).replace(",", ""))
                narr   = line[date_m.end(): m3.start()].strip()
                # Strip leading value-date (second date on same line)
                narr   = re.sub(r"^\d{2}[-/]\d{2}[-/]\d{4}\s*", "", narr).strip()
                if credit > 0 and debit == 0:
                    pending_credit = credit
                    pending_narr   = narr
                else:
                    # Debit row — flush immediately with no credit
                    pending_credit = 0.0
                    pending_narr   = ""
            else:
                # Narration continues on next lines
                narr_start = line[date_m.end():].strip()
                narr_start = re.sub(r"^\d{2}[-/]\d{2}[-/]\d{4}\s*", "", narr_start).strip()
                pending_narr   = narr_start
                pending_credit = 0.0  # will be set when we find the amounts
        else:
            # Continuation line: might complete the money amounts or add to narration
            if not pending_ym:
                continue
            m3 = MONEY3.search(line)
            if m3 and pending_credit == 0.0:
                # This continuation line has the debit/credit/balance amounts
                debit  = float(m3.group(1).replace(",", ""))
                credit = float(m3.group(2).replace(",", ""))
                narr_extra = line[:m3.start()].strip()
                pending_narr = (pending_narr + " " + narr_extra).strip()
                if credit > 0 and debit == 0:
                    pending_credit = credit
                else:
                    # It's a debit — discard
                    pending_credit = 0.0
                    pending_narr   = ""
            elif not m3:
                # Pure narration continuation
                pending_narr += " " + line

    _flush()
    return buckets, account_name


def parse_generic(full_text: str) -> tuple[dict, str]:
    """Balance-movement parser for Access, UBA, FirstBank, Sterling, etc."""
    buckets: dict = {}
    account_name  = _extract_account_name(full_text)
    prev_balance: Optional[Decimal] = None
    in_tx = False

    for line in full_text.splitlines():
        line = line.strip()
        if re.search(r"Tran Date|Value Date|Transaction Date", line, re.I):
            in_tx = True
            continue
        if not in_tx or not line:
            continue
        date_m = re.match(r"^(\d{2}/\d{2}/\d{4})", line)
        if not date_m:
            continue
        tran_date = date_m.group(1)
        parts = tran_date.split("/")
        ym    = f"{parts[2]}-{parts[1]}"
        money = list(MONEY_RE.finditer(line))
        if not money:
            continue
        balance = Decimal(money[-1].group().replace(",", ""))
        if len(money) < 2:
            prev_balance = balance
            continue
        amt_m     = money[-2]
        amount    = Decimal(amt_m.group().replace(",", ""))
        narration = line[date_m.end():amt_m.start()].strip()
        narration = re.sub(r"^\d{2}/\d{2}/\d{4}\s*", "", narration).strip()

        if prev_balance is None:
            kind = "credit" if balance >= amount else "unknown"
        else:
            delta = balance - prev_balance
            if   abs(delta - amount) <= Decimal("0.02"): kind = "credit"
            elif abs(delta + amount) <= Decimal("0.02"): kind = "debit"
            elif delta > 0:  kind = "credit";  amount = delta
            elif delta < 0:  kind = "debit";   amount = -delta
            else:            kind = "unknown"

        prev_balance = balance
        if kind == "credit" and amount > 0:
            add_credit(buckets, ym, float(amount), narration, account_name)

    return buckets, account_name


def parse_zenith_corporate(full_text: str) -> tuple[dict, str]:
    """
    Zenith Bank corporate/BOP statement format.
    Columns: DATE POSTED  VALUE DATE  DESCRIPTION  DEBIT  CREDIT  BALANCE
    """
    buckets: dict = {}
    account_name = _extract_account_name(full_text)
    if not account_name:
        name_m = re.search(r'^([A-Z][A-Z &]+(?:ENTERPRISES|LIMITED|LTD|COMPANY|CO\.?|PLC|NIG|NIGERIA))',
                            full_text, re.MULTILINE)
        account_name = name_m.group(1).strip() if name_m else "Unknown"
    account_name = re.sub(r'\s+(Account|Number|Currency|CA|NGN).*$', '', account_name, flags=re.I).strip()

    MONEY       = r'[\d,]+\.\d{2}'
    DATE_START  = re.compile(r'^\s*(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s*')
    THREE_NUMS  = re.compile(rf'({MONEY})\s+({MONEY})\s+({MONEY})\s*$')

    lines  = full_text.splitlines()
    in_tx  = False
    joined = []
    i      = 0

    def _fix_concat(line: str) -> str:
        line = re.sub(r'(\d{6,})(0\.\d{2})', r'\1 \2', line)
        line = re.sub(r'([A-Za-z/])(\d+\.\d{2})', r'\1 \2', line)
        line = re.sub(r'([A-Za-z/])(\d{1,3}(?:,\d{3})+\.\d{2})', r'\1 \2', line)
        return line

    while i < len(lines):
        line = lines[i]
        if 'DATE POSTED' in line and 'VALUE DATE' in line:
            in_tx = True
            i += 1
            continue
        if not in_tx:
            i += 1
            continue

        dm = DATE_START.match(line)
        if dm:
            if THREE_NUMS.search(_fix_concat(line)):
                joined.append(_fix_concat(line).strip())
                i += 1
            else:
                combined = line.rstrip()
                i += 1
                while i < len(lines):
                    nxt = lines[i].strip()
                    if not nxt:
                        i += 1
                        continue
                    if DATE_START.match(lines[i]):
                        break
                    combined = combined + ' ' + nxt
                    i += 1
                    if THREE_NUMS.search(_fix_concat(combined)):
                        break
                joined.append(_fix_concat(combined).strip())
        else:
            i += 1

    for row in joined:
        dm = DATE_START.match(row)
        if not dm:
            continue
        tdate = dm.group(1)
        parts = tdate.split('/')
        ym    = f"{parts[2]}-{parts[1]}"
        rest  = row[dm.end():]
        tm    = THREE_NUMS.search(rest)
        if not tm:
            continue
        debit     = float(tm.group(1).replace(',', ''))
        credit    = float(tm.group(2).replace(',', ''))
        narration = rest[:tm.start()].strip()

        lower = narration.lower()
        if any(k in lower for k in [
            'nip charge', 'stamp duty', 'sms charge', 'vat charge',
            'account maintenance', 'statement charge', 'airtime',
            'value added tax', 'charges on counter',
        ]):
            continue

        if credit > 0 and debit == 0:
            add_credit(buckets, ym, credit, narration, account_name)

    return buckets, account_name


# ════════════════════════════════════════════════════════════════════════════
# FAIRMONEY PARSER
# ════════════════════════════════════════════════════════════════════════════
def parse_fairmoney(full_text: str) -> tuple[dict, str]:
    """
    FairMoney MFB statement parser.

    PyPDF2/pypdf renders each transaction as a single line in the form:
        DD/MM/YYYY  REFNUM  [+/-] ₦ AMOUNT  ₦ BALANCENarration start
    Narration continuation lines follow with no date or reference number.

    Page headers (bank name, address, account summary) repeat on every page
    and are filtered out during narration accumulation.

    Credits are identified by the '+' sign prefix on the amount.
    'Incoming FairMoney' credits (internal FairMoney wallet/interest credits)
    are classified as loan_disbursal and excluded from eligible income.
    """
    buckets: dict    = {}
    account_name: str = ""
    lines = full_text.splitlines()

    # ── Extract account holder name ───────────────────────────────────────
    # FairMoney puts the account name as a plain "Firstname Lastname" line
    # (Title-case, 2+ words) near the top of each page before the header
    # table. Grab the first such line from the full text.
    name_re = re.compile(r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)$")
    skip_names = {"Date Reference", "Credit Debit Account"}
    for line in lines:
        stripped = line.strip()
        if stripped in skip_names:
            continue
        m = name_re.match(stripped)
        if m and len(stripped.split()) >= 2:
            account_name = stripped
            break

    # ── Parse transactions ────────────────────────────────────────────────
    pending_sign:   Optional[str]   = None
    pending_amount: float           = 0.0
    pending_ym:     str             = ""
    pending_narr:   str             = ""

    def _flush() -> None:
        nonlocal pending_sign, pending_amount, pending_ym, pending_narr
        if pending_sign == "+" and pending_amount > 0:
            add_credit(buckets, pending_ym, pending_amount,
                       pending_narr.strip(), account_name)
        pending_sign   = None
        pending_amount = 0.0
        pending_ym     = ""
        pending_narr   = ""

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Check for a new transaction anchor line first
        m = _FM_TX.match(stripped)
        if m:
            _flush()
            date_str, sign, amt_str, narr_start = m.groups()
            day, month, year = date_str.split("/")
            pending_ym     = f"{year}-{month}"
            pending_sign   = sign
            pending_amount = float(amt_str.replace(",", ""))
            pending_narr   = narr_start.strip()
            continue

        # Not a transaction anchor — handle as possible narration continuation
        if pending_sign is not None:
            # Skip page-header boilerplate lines
            if _FM_HEADER.match(stripped):
                continue
            if _FM_MONEY_ONLY.match(stripped):
                continue
            if _FM_DATE_RANGE.match(stripped):
                continue
            # Skip the account holder name line and account number that
            # repeat on each page header
            if stripped == account_name:
                continue
            if re.match(r"^\d{10}$", stripped):   # 10-digit account number
                continue
            # Append as narration continuation
            pending_narr += " " + stripped

    _flush()
    return buckets, account_name


def parse_summary_credits(full_text: str) -> dict[str, float]:
    summary: dict[str, float] = {}
    pat = re.compile(
        r"\b(20\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})", re.I,
    )
    for m in pat.finditer(full_text.replace("\n", " ")):
        year  = m.group(1)
        month = str(MONTH_MAP[m.group(2).lower()[:3]]).zfill(2)
        credit = float(m.group(4).replace(",", ""))
        summary[f"{year}-{month}"] = credit
    return summary


# ════════════════════════════════════════════════════════════════════════════
# EXCEL / MONO STATEMENT PARSERS
# ════════════════════════════════════════════════════════════════════════════

def _excel_serial_to_ym(serial) -> Optional[str]:
    try:
        f = float(serial)
        if not (40000 < f < 70000):
            return None
        import datetime as _dt
        base = _dt.date(1899, 12, 30)
        d = base + _dt.timedelta(days=int(f))
        return f"{d.year}-{str(d.month).zfill(2)}"
    except Exception:
        return None


def _detect_excel_format(rows: list) -> Optional[dict]:
    for i, row in enumerate(rows[:30]):
        h = [str(c or "").lower().strip() for c in row]

        if ("date" in h and "narration" in h and "debit" in h and "credit" in h):
            if i + 1 < len(rows):
                try:
                    first_val = float(str(rows[i+1][0] or ""))
                    if 40000 < first_val < 70000:
                        return {
                            "type": "moniepoint_excel",
                            "hdr_idx": i,
                            "date_col": h.index("date"),
                            "narration_col": h.index("narration"),
                            "debit_col": h.index("debit"),
                            "credit_col": h.index("credit"),
                        }
                except (ValueError, IndexError):
                    pass

        if h and h[0] == "transaction date":
            credit_col = next((j for j, v in enumerate(h) if "credit" in v), None)
            narration_col = next((j for j, v in enumerate(h)
                                  if "narration" in v or "description" in v), None)
            if credit_col is not None:
                return {
                    "type": "mono_standard",
                    "hdr_idx": i,
                    "date_col": 0,
                    "narration_col": narration_col or 5,
                    "credit_col": credit_col,
                }

        has_date   = any("date" in v for v in h)
        has_credit = any("credit" in v for v in h)
        has_debit  = any("debit" in v for v in h)
        if has_date and has_credit and has_debit:
            date_col   = next((j for j, v in enumerate(h) if "date" in v), 0)
            credit_col = next((j for j, v in enumerate(h) if "credit" in v), None)
            narr_col   = next((j for j, v in enumerate(h)
                               if "narration" in v or "description" in v or "details" in v), None)
            if credit_col is not None:
                return {
                    "type": "generic_excel",
                    "hdr_idx": i,
                    "date_col": date_col,
                    "narration_col": narr_col or 1,
                    "credit_col": credit_col,
                }
    return None


def _parse_excel_direct(file_bytes: bytes) -> tuple[dict, str]:
    """
    Fallback Excel parser using zipfile + lxml — bypasses openpyxl's stylesheet
    parser entirely. Handles files with invalid/missing stylesheet XML that
    openpyxl refuses to open (e.g. Moniepoint Business exports).

    Reads xl/sharedStrings.xml and xl/worksheets/sheet1.xml directly.
    Uses column letter references (A, B, J…) instead of positional indices
    so non-contiguous column layouts (common in Moniepoint Business) are
    handled correctly.
    """
    import zipfile
    import datetime as _dt
    from lxml import etree as _etree

    NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ns = {"s": NS}

    def _col_letter(cell_ref: str) -> str:
        return re.match(r"([A-Z]+)", cell_ref).group(1)

    def _serial_to_ym(val: str) -> Optional[str]:
        try:
            f = float(val)
            if not (40000 < f < 70000):
                return None
            base = _dt.date(1899, 12, 30)
            d = base + _dt.timedelta(days=int(f))
            return f"{d.year}-{str(d.month).zfill(2)}"
        except Exception:
            return None

    with zipfile.ZipFile(BytesIO(file_bytes)) as zf:
        # ── Shared strings ────────────────────────────────────────────────
        shared: list[str] = []
        with zf.open("xl/sharedStrings.xml") as f:
            tree = _etree.parse(f)
            for si in tree.findall(".//s:si", ns):
                texts = si.findall(".//s:t", ns)
                shared.append("".join(t.text or "" for t in texts))

        # ── Sheet data ────────────────────────────────────────────────────
        with zf.open("xl/worksheets/sheet1.xml") as f:
            tree = _etree.parse(f)
        xml_rows = tree.findall(".//s:row", ns)

    def _row_dict(xml_row) -> dict[str, str]:
        """Return {col_letter: value} for one XML row."""
        d: dict[str, str] = {}
        for c in xml_row.findall("s:c", ns):
            ref = c.get("r", "")
            if not ref:
                continue
            t_attr = c.get("t", "n")
            v_el = c.find("s:v", ns)
            val = ""
            if v_el is not None and v_el.text:
                val = shared[int(v_el.text)] if t_attr == "s" else v_el.text
            d[_col_letter(ref)] = val
        return d

    # ── Account name (scan first 10 rows) ─────────────────────────────────
    account_name = ""
    for xml_row in xml_rows[:10]:
        rd = _row_dict(xml_row)
        cols = sorted(rd.keys())
        for i, col in enumerate(cols):
            if "account name" in rd[col].lower():
                # value is in the next non-empty column on the same row
                for next_col in cols[i + 1:]:
                    nv = rd[next_col].strip(" -")
                    if nv:
                        account_name = nv
                        break
                break
        if account_name:
            break

    # ── Find header row ───────────────────────────────────────────────────
    hdr_row_idx = None
    date_col = credit_col = narr_col = None
    for i, xml_row in enumerate(xml_rows):
        rd = _row_dict(xml_row)
        inv = {v.lower().strip(): col for col, v in rd.items() if v.strip()}
        if "date" in inv and "credit" in inv and "debit" in inv:
            hdr_row_idx = i
            date_col   = inv["date"]
            credit_col = inv["credit"]
            narr_col   = inv.get("narration") or inv.get("description") or inv.get("details")
            break

    if hdr_row_idx is None:
        raise ValueError("Could not find header row (Date/Credit/Debit) in Excel file.")

    # ── Parse credit rows ─────────────────────────────────────────────────
    buckets: dict = {}
    for xml_row in xml_rows[hdr_row_idx + 1:]:
        rd = _row_dict(xml_row)
        date_raw   = rd.get(date_col, "")
        credit_raw = rd.get(credit_col, "")
        if not date_raw or not credit_raw:
            continue
        try:
            credit = float(str(credit_raw).replace(",", ""))
        except ValueError:
            continue
        if credit <= 0:
            continue
        # Date: try serial first (Moniepoint Business), then ISO/DD-MM-YYYY strings
        ym = _serial_to_ym(date_raw)
        if not ym:
            m = re.match(r"^(20\d{2})-(\d{2})", date_raw)
            if m:
                ym = f"{m.group(1)}-{m.group(2)}"
            else:
                m = re.match(r"^(\d{2})/(\d{2})/(20\d{2})", date_raw)
                ym = f"{m.group(3)}-{m.group(2)}" if m else None
        if not ym:
            continue
        narration = rd.get(narr_col, "").strip() if narr_col else ""
        add_credit(buckets, ym, credit, narration, account_name)

    return buckets, account_name


def parse_excel(file_bytes: bytes) -> tuple[dict, str]:
    """
    Parse an Excel bank statement.
    Primary path: openpyxl (handles most files).
    Fallback path: direct zip+lxml parsing for files where openpyxl fails
    due to an invalid/corrupt stylesheet XML (e.g. Moniepoint Business exports).
    """
    # ── Try openpyxl first ────────────────────────────────────────────────
    if OPENPYXL_AVAILABLE:
        try:
            import io
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
            ws = wb.active
            rows = [[cell.value for cell in row] for row in ws.iter_rows()]
            wb.close()
        except Exception:
            # Stylesheet or other XML error — fall through to direct parser
            return _parse_excel_direct(file_bytes)
    else:
        return _parse_excel_direct(file_bytes)

    fmt = _detect_excel_format(rows)
    if not fmt:
        # openpyxl loaded OK but format unrecognised — try direct parser
        return _parse_excel_direct(file_bytes)

    account_name = ""
    for row in rows[:fmt["hdr_idx"]]:
        for j, cell in enumerate(row):
            cv = str(cell or "").lower().strip()
            if cv in ("account name:", "account name", "name"):
                for nc in row[j+1:]:
                    nv = str(nc or "").strip()
                    if nv:
                        account_name = nv.rstrip("-").strip()
                        break
                if not account_name and j + 1 < len(row):
                    account_name = str(rows[rows.index(row)][j+1] or "").strip()
            if account_name:
                break
        if account_name:
            break

    buckets: dict = {}
    hdr_idx      = fmt["hdr_idx"]
    date_col     = fmt["date_col"]
    narr_col     = fmt["narration_col"]
    credit_col   = fmt["credit_col"]
    fmt_type     = fmt["type"]

    for row in rows[hdr_idx + 1:]:
        if not row or row[date_col] is None:
            continue

        if fmt_type == "moniepoint_excel":
            ym = _excel_serial_to_ym(row[date_col])
        else:
            date_str = str(row[date_col] or "").strip()
            m = re.match(r"^(20\d{2})-(\d{2})", date_str)
            if m:
                ym = f"{m.group(1)}-{m.group(2)}"
            else:
                m = re.match(r"^(\d{2})/(\d{2})/(20\d{2})", date_str)
                ym = f"{m.group(3)}-{m.group(2)}" if m else None

        if not ym:
            continue

        try:
            credit_raw = str(row[credit_col] or "").replace(",", "").strip()
            credit = float(credit_raw) if credit_raw else 0.0
        except ValueError:
            credit = 0.0

        if credit <= 0:
            continue

        narration = str(row[narr_col] or "").strip() if narr_col is not None else ""
        add_credit(buckets, ym, credit, narration, account_name)

    return buckets, account_name


# ════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════
def parse_transactions(file_bytes: bytes, password: str = "",
                       filename: str = "") -> tuple[dict, dict, str, str]:
    """
    Auto-detects PDF vs Excel and routes accordingly.
    Returns (buckets, summary_credits, bank_name, account_name)
    buckets: {ym: {gross, count, self_transfer, reversal, non_business, loan_disbursal, real_credit}}
    """
    # ── Excel detection ───────────────────────────────────────────────────
    is_excel = (filename.lower().endswith((".xlsx", ".xls")) or
                file_bytes[:4] in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0"))
    if is_excel:
        buckets, account_name = parse_excel(file_bytes)
        bank = "Mono Excel"
        summary: dict = {}
        return buckets, summary, bank, account_name

    # ── PDF ───────────────────────────────────────────────────────────────
    full_text = extract_pdf_text(pdf_bytes=file_bytes, password=password)
    bank = detect_bank(full_text)
    summary = parse_summary_credits(full_text)

    # mybankStatement-engine banks all share the same explicit Debit/Credit
    # column format and are handled by parse_gtbank regardless of bank name.
    _MYBANKSTATEMENT_BANKS = {
        "GTBank", "Access", "FirstBank", "UBA",
        "Fidelity", "Union", "Stanbic", "FCMB", "Wema", "Sterling",
    }

    if bank == "FairMoney":
        buckets, account_name = parse_fairmoney(full_text)
    elif bank == "OPay":
        buckets, account_name = parse_opay(full_text)
    elif bank in _MYBANKSTATEMENT_BANKS:
        buckets, account_name = parse_gtbank(full_text)
    elif bank == "Zenith":
        buckets, account_name = parse_zenith(full_text)
    elif bank == "Zenith_Corporate":
        buckets, account_name = parse_zenith_corporate(full_text)
    else:
        buckets, account_name = parse_generic(full_text)

    return buckets, summary, bank, account_name


def monthly_analysis(buckets: dict, summary: dict | None = None) -> list[dict]:
    rows = []
    for ym in sorted(set(buckets) | set(summary or {})):
        b = buckets.get(ym, _empty_bucket())
        gross = (summary or {}).get(ym, b["gross"])
        deductions = (b.get("self_transfer", 0) + b.get("reversal", 0) +
                      b.get("non_business", 0) + b.get("loan_disbursal", 0))
        rows.append({
            "ym": ym,
            "label": ym_label(ym),
            "gross": gross,
            "parsed_gross": b["gross"],
            "self_transfer": b.get("self_transfer", 0),
            "reversal": b.get("reversal", 0),
            "non_business": b.get("non_business", 0),
            "loan_disbursal": b.get("loan_disbursal", 0),
            "deductions": deductions,
            "eligible_income": max(gross - deductions, 0),
            "count": b.get("count", 0),
        })
    return rows


# ════════════════════════════════════════════════════════════════════════════
# FIRSTCENTRAL CREDIT BUREAU PARSER
# ════════════════════════════════════════════════════════════════════════════
def parse_firstcentral(pdf_bytes: bytes, password: str = "") -> dict:
    full_text = extract_pdf_text(pdf_bytes, password)

    STATUS_VALS = r"Closed|Open|Written Off|Lost Written Off|LostWritten Off|WrittenOff"
    CLASS_VALS  = r"Performing|Lost|Derogatory|Delinquent|Non Performing|Non-Performing"

    heading_re = re.compile(
        r'Details of Credit Agreement with "([^"]+)" for Account Number:\s*'
        r'([^\n\r]+(?:-\r?\n\s*[^\n\r]+)?)'
    )
    matches    = list(heading_re.finditer(full_text))
    detail_map: dict[str, dict] = {}

    def _fv(text: str, pattern: str) -> str:
        m = re.search(pattern, text, re.I)
        return m.group(1).strip() if m else ""

    for i, match in enumerate(matches):
        subscriber = match.group(1).strip()
        acct_no    = re.sub(r"[\s\r\n]+", "", match.group(2)).strip()
        if not acct_no:
            continue
        block_end  = matches[i+1].index if i < len(matches)-1 else min(len(full_text), match.index+5000)
        window     = re.sub(r"\s+", " ", full_text[match.index:block_end]).strip()

        acct_sts = (_fv(window, f"Account Status.{{0,60}}?({STATUS_VALS})") or
                    _fv(window, f"({STATUS_VALS})\\s+Account Status"))
        fac_cls  = (_fv(window, f"Facility Classification.{{0,60}}?({CLASS_VALS})") or
                    _fv(window, f"({CLASS_VALS})\\s+Facility Classification"))
        instl    = _parse_currency(
            _fv(window, r"Instalment Amount\s+([\d,]+\.\d{2})") or
            _fv(window, r"([\d,]+\.\d{2})\s+Instalment Amount"))
        outst    = _parse_currency(
            _fv(window, r"Current Balance\s+([\d,]+\.\d{2})") or
            _fv(window, r"([\d,]+\.\d{2})\s+Current Balance"))
        dur_raw  = (_fv(window, r"Loan Duration\s+((?:\d+)|(?:Not Available))\s+Day\(s\)") or
                    _fv(window, r"((?:\d+)|(?:Not Available))\s+Day\(s\)\s+Loan Duration") or
                    "Not Available")

        detail_map[acct_no] = {
            "subscriber": subscriber,
            "account_status": acct_sts or "Unknown",
            "facility_classification": fac_cls or "Unknown",
            "instalment_amount": instl,
            "outstanding_balance": outst,
            "loan_duration_days": dur_raw,
            "tenor_months": _get_tenor_months(dur_raw),
        }

    summary_map: dict[str, dict] = {}
    sum_start = full_text.find("Credit Agreements Summary")
    det_start = full_text.find("Details of Credit Agreement with")
    if sum_start >= 0 and det_start > sum_start:
        sumtext = re.sub(r"\s+", " ", full_text[sum_start:det_start]).strip()
        for acct_no, detail in detail_map.items():
            idx = sumtext.find(acct_no)
            if idx < 0:
                continue
            cs  = max(0, idx-250)
            ce  = min(len(sumtext), idx+250)
            ctx = sumtext[cs:ce]
            ap  = idx - cs
            figs = [float(m.group().replace(",","")) for m in re.finditer(r"[\d,]+\.\d{2}", ctx)]
            pc  = [m[0] for m in re.finditer(f"({CLASS_VALS})", ctx, re.I) if m.start() > ap]
            ps  = [m[0] for m in re.finditer(f"({STATUS_VALS})", ctx, re.I) if m.start() > ap]
            ac  = [m[0] for m in re.finditer(f"({CLASS_VALS})", ctx, re.I)]
            as_ = [m[0] for m in re.finditer(f"({STATUS_VALS})", ctx, re.I)]
            arr, ins, out, avl = 0, 0, 0, 0
            if len(figs) >= 4: arr, ins, out, avl = figs[:4]
            elif len(figs) == 3: arr, ins, out = figs[:3]
            elif len(figs) == 2: arr, ins = figs[:2]
            elif len(figs) == 1: arr = figs[0]
            summary_map[acct_no] = {
                "facility_classification": ((pc or ac or ["Unknown"])[0]).strip(),
                "account_status": ((ps or as_ or ["Unknown"])[0]).strip(),
                "instalment_amount": ins, "outstanding_balance": out,
                "arrear_amount": arr, "availed_limit": avl,
                "figure_count": len(figs),
            }

    records: list[CreditAccount] = []
    seen: set[str] = set()
    for acct_no, det in detail_map.items():
        if acct_no in seen:
            continue
        seen.add(acct_no)
        sm = summary_map.get(acct_no, {})
        fac_cls = det["facility_classification"] if det["facility_classification"] != "Unknown" else sm.get("facility_classification","Unknown")
        acct_sts= det["account_status"] if det["account_status"] != "Unknown" else sm.get("account_status","Unknown")
        fc      = sm.get("figure_count", 0)
        instl   = det["instalment_amount"] if det["instalment_amount"] > 0 else (sm.get("instalment_amount",0) if fc >= 3 else 0)
        outst   = det["outstanding_balance"] if det["outstanding_balance"] > 0 else (sm.get("outstanding_balance",0) if fc >= 3 else 0)
        arrear  = sm.get("arrear_amount", 0)
        tenor   = det["tenor_months"] or _get_tenor_months(det["loan_duration_days"])

        sn = re.sub(r"[^a-z]", "", acct_sts.lower())
        cn = re.sub(r"[^a-z]", "", fac_cls.lower())
        is_closed = sn == "closed"
        is_bad = (sn in ("open","writtenoff","lostwrittenoff") and
                  cn in ("lost","derogatory","delinquent","nonperforming") and
                  (outst > 10000 or arrear > 10000))
        is_perf = sn == "open" and cn == "performing"
        mo, drv = 0.0, False
        if is_perf:
            if instl > 0: mo = instl
            elif outst > 0 and tenor: mo = outst / tenor; drv = True

        records.append(CreditAccount(
            subscriber_name=det["subscriber"], account_number=acct_no,
            account_status=acct_sts, facility_classification=fac_cls,
            instalment_amount=instl, outstanding_balance=outst,
            loan_duration_days=det["loan_duration_days"], tenor_months=tenor,
            monthly_obligation=mo, derived_from_balance=drv,
            include_in_obligation=mo > 0, is_closed=is_closed, is_bad_credit=is_bad,
        ))

    visible = [r for r in records if not r.is_closed]
    return {
        "records": visible,
        "total_monthly_obligation": sum(r.monthly_obligation for r in visible if r.include_in_obligation),
        "bad_credit_accounts": [r for r in visible if r.is_bad_credit],
    }
