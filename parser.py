from __future__ import annotations

import re
import subprocess
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


def extract_pdf_text_layout(pdf_bytes: bytes) -> str:
    """
    Extract PDF text preserving spatial column layout using pdftotext -layout.
    Required for PDFs where amounts are in fixed-width columns (e.g. OPay v2).
    Returns empty string if pdftotext is unavailable.
    """
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", "-", "-"],
            input=pdf_bytes,
            capture_output=True,
            timeout=30,
        )
        return result.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


# ════════════════════════════════════════════════════════════════════════════
# BANK DETECTION
# ════════════════════════════════════════════════════════════════════════════
def detect_bank(text: str) -> str:
    t = text.lower()
    # ── OPay v2: new "Account Statement" format with Trans. Time column ──
    # Must be checked FIRST — before FairMoney — because OPay transaction
    # narrations often contain "FairMoney MFB" (transfer-from counterparty),
    # which would otherwise trigger a false FairMoney detection.
    # The "trans. time" column header is unique to the OPay v2 layout.
    if ("trans. time" in t and
            ("opay digital" in t or "wallet account" in t or "9payment service" in t)):
        return "OPay_v2"

    # ── Moniepoint Business: same ISO-timestamp layout as OPay Business ──
    # The Moniepoint logo is an image (invisible to PyPDF2) but the MFB
    # stamp/watermark text IS extracted. Check BEFORE OPay_Business so
    # Moniepoint statements are never misrouted to OPay_Business.
    if ("moniepoint mfb" in t or "moniepoint microfinance" in t) and \
            re.search(r"\d{4}-\d{2}-\d{2}t\d{2}:", t):
        return "Moniepoint_Business"

    # ── OPay Business: ISO-timestamp "Account Statement" format ──────────
    # Moniepoint check fires first when MFB watermark text is present.
    # OPay Business: "Business Name" + ISO timestamps + OPay ref patterns.
    if "business name" in t and re.search(r"\d{4}-\d{2}-\d{2}t\d{2}:", t) and \
            ("2mpt" in t or "ap_trsf" in t or "business_credit" in t):
        return "OPay_Business"

    if ("fairmoney mfb" in t or "fairmoney microfinance" in t) and "mybankstatement" not in t:
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


def parse_opay_v2(pdf_bytes: bytes) -> tuple[dict, str]:
    """
    Parser for the OPay "Account Statement" format (2025+).

    Layout characteristics:
    - Header: Trans. Time | Value Date | Description | Debit(₦) | Credit(₦) | Balance After(₦) | Channel
    - Each transaction occupies one anchor line (date/time at the start) plus
      optional description fragment lines before and after it.
    - The debit / credit / balance / channel columns occupy fixed character
      positions starting around column 88 of the pdftotext -layout output.
    - Null values in numeric columns are rendered as "--".
    - Channel keyword ("Mobile", "POS", "Web", etc.) always follows the three
      numeric columns, making AMOUNTS_PAT reliable for anchor detection.
    - The PDF contains TWO account sections:
        1. Wallet Account  (the main transactional account)
        2. Savings Account (the OWealth sub-account — interest + round-trips)
      We parse only the first section, stopping when the second "Trans. Time"
      header (or "Savings Account" label) is encountered.

    Requires pdftotext (poppler-utils) installed on the host.
    Falls back gracefully to an empty result if pdftotext is unavailable.
    """
    # ── Layout-preserved text via pdftotext -layout ───────────────────────
    layout_text = extract_pdf_text_layout(pdf_bytes)
    if not layout_text:
        return {}, ""

    # ── Patterns ──────────────────────────────────────────────────────────
    # Anchor line: starts with 0–4 spaces then DD Mon YYYY HH:MM:SS
    DATE_LINE = re.compile(
        r"^\s{0,4}(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+(20\d{2})\s+\d{2}:\d{2}:\d{2}",
        re.I,
    )
    # Three amount tokens (-- or N,NNN.NN) followed by a channel keyword.
    # This anchors on the channel word so it works regardless of how much
    # the description overflows into the amounts region.
    AMOUNTS_PAT = re.compile(
        r"([\d,]+\.\d{2}|--)\s+([\d,]+\.\d{2}|--)\s+([\d,]+\.\d{2})\s+"
        r"(?:Mobile|POS|Web|USSD|ATM|Agent)",
        re.I,
    )
    # Description continuation: deeply indented (36+ spaces), non-blank.
    DESC_CONT  = re.compile(r"^\s{36,}(\S.*)$")
    # Strip trailing transaction-reference digit runs from continuation lines.
    TRAIL_REF  = re.compile(r"\s+\d{10,}\s*$")
    # Section boundary markers.
    TRANS_TIME = re.compile(r"Trans\.\s*Time", re.I)
    SECTION_END = re.compile(r"Savings Account", re.I)

    # ── Account name ──────────────────────────────────────────────────────
    # OPay v2 layout: the "Account Name" label and "Account Number" label
    # appear on the same header line; the actual name value appears on the
    # NEXT line at the far-left column (before the account number).
    account_name = ""
    layout_lines = layout_text.splitlines()
    for i, line in enumerate(layout_lines):
        if "Account Name" in line and "Account Number" in line:
            for j in range(i + 1, min(i + 5, len(layout_lines))):
                val = layout_lines[j][:50].strip()
                if val and re.match(r"^[A-Z][A-Z ]{3,}$", val):
                    account_name = val
                    break
            break
    if not account_name:
        account_name = _extract_account_name(layout_text)

    # ── Parse main Wallet section only ────────────────────────────────────
    lines = layout_text.splitlines()
    buckets: dict = {}
    pre_desc: list[str] = []   # description fragments accumulating BEFORE the date line
    state = "pre"              # "pre" = before date line, "post" = after date line
    section_count = 0
    in_tx = False

    for line in lines:
        # Section boundary detection
        if TRANS_TIME.search(line):
            section_count += 1
            if section_count > 1:
                break           # Enter OWealth/Savings section — stop
            in_tx = True
            pre_desc = []
            state = "pre"
            continue

        if SECTION_END.search(line):
            break               # Belt-and-suspenders stop

        if not in_tx:
            continue

        # ── Transaction anchor line ───────────────────────────────────────
        m = DATE_LINE.match(line)
        if m:
            mon  = m.group(2).lower()[:3]
            year = m.group(3)
            ym   = f"{year}-{MONTH_NUM[mon]}"

            amt_m = AMOUNTS_PAT.search(line)
            if amt_m:
                credit_tok = amt_m.group(2)
                credit = (0.0 if credit_tok == "--"
                          else float(credit_tok.replace(",", "")))
                # Description: text between value-date end (col ~38) and
                # the start of the amounts block.
                inline_desc = line[38:amt_m.start()].strip()
                parts = pre_desc[:]
                if inline_desc:
                    parts.append(inline_desc)
                full_desc = " ".join(parts).strip()
                if credit > 0:
                    add_credit(buckets, ym, credit, full_desc, account_name)

            pre_desc = []
            state = "post"
            continue

        # ── Non-anchor line ───────────────────────────────────────────────
        stripped = line.strip()
        if not stripped:
            pre_desc = []
            state = "pre"
            continue

        dm = DESC_CONT.match(line)
        if dm:
            text = TRAIL_REF.sub("", dm.group(1)).strip()
            # Only collect non-empty, non-digit-only continuation lines
            if text and not re.match(r"^[\d\s]+$", text):
                if state == "pre":
                    # Pre-description: belongs to the next date line
                    pre_desc.append(text)
                # Post-description lines (after the date line) are
                # transaction-ref overflows — discard them.

    return buckets, account_name


def parse_opay_business(pdf_bytes: bytes) -> tuple[dict, str]:
    """
    Parser for the OPay Business "Account Statement" format.

    Layout characteristics (pdftotext -layout):
    - Header: Business Name, Account Number, Currency, Date range, Address
    - Column header line: Date | Narration | Reference | Debit | Credit | Balance
    - Each transaction spans THREE physical lines:
        Line A (DATE):  YYYY-MM-DDThh:   [narration_part_1 at col ~17+]
        Line B (MID):   [narration_part_2 at col ~17–80]  [reference col ~82]
                        [debit]  [credit]  [balance]   ← at fixed end of line
        Line C (TIME):  mm:ss   [narration_part_3 at col ~17+]

    PAGE-BREAK MERGE: When a transaction straddles a page boundary, pdftotext
    concatenates the DATE line and the MID line (with reference + amounts) into
    a single long line prefixed with \x0c. In this case the amounts appear at
    the END of the DATE line itself and no separate MID line exists.

    Both cases are handled by first checking MONEY3 against the date line, then
    falling back to searching the subsequent middle line.

    Narration assembly: narr_part_1 (from DATE line after hh:) +
                        narr_part_2 (from MID line, cols 17–80) +
                        narr_part_3 (from TIME line after mm:ss)
    Reference tokens (≥15 chars, e.g. AP_TRSF|...) are stripped from narration.

    Requires pdftotext -layout (poppler-utils). Returns ({}, "") if unavailable.
    """
    layout_text = extract_pdf_text_layout(pdf_bytes)
    if not layout_text:
        return {}, ""

    DATE_LINE = re.compile(r"^\x0c?(\d{4})-(\d{2})-\d{2}T\d{2}:", re.I)
    MONEY3    = re.compile(
        r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$"
    )
    TIME_RE   = re.compile(r"^\d{2}:\d{2}\s*(.*)")
    # Strip trailing reference token (pipe-delimited IDs, ≥15 non-space chars)
    REF_RE    = re.compile(r"\s+\S{15,}\S*\s*$")

    # Account name from "Business Name" header (business account)
    # or "Account Name" (personal fallback)
    account_name = ""
    biz_m = re.search(r"Business Name\s{2,}([A-Z][A-Z ]{3,})", layout_text)
    if biz_m:
        account_name = biz_m.group(1).strip()
    if not account_name:
        account_name = _extract_account_name(layout_text)

    lines   = layout_text.splitlines()
    buckets: dict = {}
    i = 0

    while i < len(lines):
        l = lines[i].lstrip("\x0c")
        dm = DATE_LINE.match(lines[i])   # match on raw (preserves \x0c for page-break detection)
        if not dm:
            i += 1
            continue

        year, month = dm.group(1), dm.group(2)
        ym = f"{year}-{month}"

        # ── PAGE-BREAK MERGED: amounts are on the date line itself ────────
        date_am = MONEY3.search(l)
        if date_am:
            debit  = float(date_am.group(1).replace(",", ""))
            credit = float(date_am.group(2).replace(",", ""))
            if credit > 0 and debit == 0:
                # Narration: text after hh: up to where the reference starts
                after_hh = l[l.index("T") + 4:date_am.start()].strip()
                narr = REF_RE.sub("", after_hh).strip()
                add_credit(buckets, ym, credit, narr, account_name)
            # Skip to next date anchor
            j = i + 1
            while j < len(lines) and not DATE_LINE.match(lines[j]):
                j += 1
            i = j
            continue

        # ── NORMAL: narration on date line, amounts on mid line ───────────
        narr1 = l[l.index("T") + 4:].strip() if "T" in l else ""

        # Collect non-blank lines until next date anchor
        other_lines: list[str] = []
        j = i + 1
        while j < len(lines) and not DATE_LINE.match(lines[j]):
            if lines[j].strip():
                other_lines.append(lines[j].rstrip())
            j += 1

        for mid in other_lines:
            am = MONEY3.search(mid)
            if not am:
                continue
            debit  = float(am.group(1).replace(",", ""))
            credit = float(am.group(2).replace(",", ""))
            if credit > 0 and debit == 0:
                # Narration from mid line: cols 16–80 (before the reference)
                narr2 = mid[16:80].strip() if len(mid) > 16 else ""
            