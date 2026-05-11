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
    # ── Zenith Corporate: MUST be checked before ALL named bank checks ─────
    # Zenith Corporate e-statements contain "NIP/FCMB/...", "NIP/GTB/...",
    # etc. in transaction narrations. Any named-bank check that runs first
    # will misidentify the statement. The "date posted" + "value date" column
    # pair is unique to Zenith Corporate BOP format; no other bank uses both.
    # Adding "zenithdirect" OR "zenith" as a tiebreaker so a non-Zenith PDF
    # that happens to have both column headers doesn't misfire.
    if "date posted" in t and "value date" in t and             ("zenith" in t or "zenithdirect" in t):
        return "Zenith_Corporate"

    # ── Kuda: MUST be checked before GTBank ──────────────────────────────
    # Kuda statements contain "Gtbank Plc" in transaction narrations.
    if "kuda mf bank" in t or "kudabank" in t or "kuda technologies" in t:
        return "Kuda"

    # ── mybankStatement-format banks ─────────────────────────────────────
    # These banks all share the mybankStatement PDF engine which renders
    # explicit Debit/Credit columns and hyphen or slash date separators.
    # They MUST all be checked BEFORE the generic "mybankstatement" check
    # below, otherwise they'd get misrouted to parse_zenith().
    # GTBank check: require "guaranty trust" (header text) OR "gtbank" with
    # mybankstatement watermark — plain "gtbank" alone can appear in Kuda/
    # Moniepoint narrations and must NOT trigger this branch.
    if "guaranty trust" in t or "gtco" in t or             ("gtbank" in t and "mybankstatement" in t) or             ("gt bank" in t and "mybankstatement" in t):
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
    # FCMB: require mybankstatement watermark OR "first city monument bank"
    # (the full name). Plain "fcmb" alone appears in Zenith narrations
    # (NIP/FCMB/...) and must NOT trigger this branch.
    if "first city monument bank" in t or ("fcmb" in t and "mybankstatement" in t):
        return "FCMB"
    if "wema" in t:
        return "Wema"
    if "sterling bank" in t:
        return "Sterling"
    if "mybankstatement" in t or "tran date value date narration" in t:
        return "Zenith"
    if "moniepoint mfb" in t or "moniepoint microfinance" in t:
        return "Moniepoint"
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
                # Narration continuation from time line: after mm:ss
                time_cont = ""
                for ol in other_lines:
                    tm = TIME_RE.match(ol.strip())
                    if tm and tm.group(1).strip():
                        time_cont = tm.group(1).strip()
                        break
                narr = " ".join(p for p in [narr1, narr2, time_cont] if p)
                add_credit(buckets, ym, credit, narr, account_name)
            break  # only the first amount line per block

        i = j

    return buckets, account_name


# ════════════════════════════════════════════════════════════════════════════
# KUDA BANK PARSER
# ════════════════════════════════════════════════════════════════════════════
_KUDA_DATE  = re.compile(r"^(\d{2}/\d{2}/\d{2})$")
_KUDA_TIME  = re.compile(r"^\d{2}:\d{2}:\d{2}$")
_KUDA_MONEY = re.compile(r"^₦([\d,]+\.\d{2})$")
_KUDA_OUTWARD = re.compile(
    r"\boutward\b|\bpaid\b|\bpayment\b|\bbills?\b|\bairtime\b|\bdata\b|"
    r"\belectricity\b|\bcable\b|\bwithdrawal\b|\batm\b|\bpos\s+charge\b|"
    r"\bbank\s+charge\b|\bfee\b|\bstamp\s+duty\b|\bdebit\b|"
    r"\bspend\s+and\s+save\b|\bspend\s*\+\s*save\b",
    re.I,
)


def parse_kuda(full_text: str) -> tuple[dict, str]:
    """
    Parser for Kuda MF Bank PDF statements.

    PyPDF2 extracts Kuda columns as individual newline-separated tokens.
    Each transaction block follows this sequence:
      DD/MM/YY       ← date anchor
      HH:MM:SS       ← time
      ₦Amount        ← transaction amount (first ₦ token)
      category_word  ← direction indicator (inward/outward/local/house etc.)
      to/from info
      description
      ₦NewBalance    ← running balance (second ₦ token)

    Direction: any category token matching _KUDA_OUTWARD → debit (skip).
    All others (inward transfer, local funds transfer, house, etc.) → credit.

    Page-boundary dedup: Kuda PDFs repeat the last transaction on a page at
    the top of the next page. Dedup key = (date, time, first_amount).
    """
    lines = [ln.strip() for ln in full_text.split("\n")]

    # Account name: near top of statement as an ALL-CAPS multi-word line.
    # PyPDF2 may render tab characters between name parts; normalise before matching.
    account_name = ""
    for line in lines[:25]:
        normalised = re.sub(r"\t", " ", line).strip()
        if re.match(r"^[A-Z][A-Z ]{4,}$", normalised) and len(normalised.split()) >= 2:
            account_name = normalised
            break

    buckets: dict = {}
    seen_keys: set = set()
    i = 0

    while i < len(lines):
        dm = _KUDA_DATE.match(lines[i])
        if not dm:
            i += 1
            continue

        date_str = dm.group(1)  # DD/MM/YY

        # Collect lines until next date anchor or page-header keyword
        window: list[str] = []
        j = i + 1
        while j < len(lines):
            wl = lines[j].strip()
            if _KUDA_DATE.match(wl):
                break
            if re.match(r"^(?:Page \d+|All\s+Statements|Kuda\s+MF|Statement)", wl, re.I):
                j += 1
                continue
            window.append(wl)
            j += 1

        # Partition window into ₦amounts, time, and text tokens
        time_found: str | None = None
        amounts: list[str] = []
        text_tokens: list[str] = []

        for wl in window:
            if not wl:
                continue
            mm = _KUDA_MONEY.match(wl)
            if mm:
                amounts.append(mm.group(1))
                continue
            if _KUDA_TIME.match(wl):
                time_found = wl
                continue
            text_tokens.append(wl)

        if not amounts:
            i = j
            continue

        # Skip page-boundary duplicates
        dedup_key = (date_str, time_found or "notime", amounts[0])
        if dedup_key in seen_keys:
            i = j
            continue
        seen_keys.add(dedup_key)

        # Direction: check ONLY the first few tokens (the category cell).
        # Stop collecting at account-name tokens (contain digits or "/NNNN" pattern)
        # so that description text doesn't pollute the direction check.
        cat_tokens: list[str] = []
        for tok in text_tokens[:5]:
            if re.search(r"\d{6,}|/\d{4,}", tok):
                break
            cat_tokens.append(re.sub(r"\t", " ", tok).strip())
        cat_str = " ".join(cat_tokens).strip().lower()

        is_outward = bool(_KUDA_OUTWARD.search(cat_str))
        # Lone "spend" token = spend+save deduction → outward
        if not is_outward and cat_tokens and re.match(r"^spend$", cat_tokens[0], re.I):
            is_outward = True
        if is_outward:
            i = j
            continue

        # Parse date: DD/MM/YY → YYYY-MM
        parts = date_str.split("/")
        if len(parts) != 3:
            i = j
            continue
        day, mon, yr = parts
        ym = f"{int(yr) + 2000}-{mon}"

        amount = float(amounts[0].replace(",", ""))
        narration = re.sub(r"\s+", " ", re.sub(r"\t", " ", " ".join(text_tokens))).strip()

        # Skip stamp duty and government levies (these are bank charges, not income)
        if re.search(r"\bstamp\s+duty\b|\belectronic.*levy\b|\bfgn.*levy\b", narration, re.I):
            i = j
            continue

        add_credit(buckets, ym, amount, narration, account_name)
        i = j

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

    pypdf renders the Debit/Credit columns in two ways depending on PDF vintage:

    Layout A — 3 numbers (debit=0.00 explicit on credit rows):
        10-01-2025  10-01-2025  TRANSFER FROM X  0.00  50,000.00  129,678.71

    Layout B — 2 numbers (debit column blank/omitted on credit rows):
        10-01-2025  10-01-2025  TRANSFER FROM X  50,000.00  129,678.71

    Layout A: debit==0 and credit>0 → confirmed credit.
    Layout B: balance delta > 0 → confirmed credit; delta < 0 → debit.

    The skip filter is anchored to line-start patterns so it never
    accidentally drops transaction narrations that mention a bank name.
    """
    buckets: dict     = {}
    account_name: str = _extract_account_name(full_text)

    GTB_DATE = re.compile(r"^(\d{2}[-/]\d{2}[-/]\d{4})")
    MONEY3   = re.compile(r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$")
    MONEY2   = re.compile(r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$")
    DATE_ORDER = "dmy"
    period_m = re.search(
        r"\bPeriod\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s*20\d{2}",
        full_text,
        re.I,
    )
    if period_m:
        first_date_m = re.search(r"\n\s*(\d{2})[-/](\d{2})[-/](20\d{2})", full_text)
        if first_date_m and int(first_date_m.group(1)) == MONTH_MAP[period_m.group(1).lower()[:3]]:
            DATE_ORDER = "mdy"

    # Skip lines that ARE page headers — anchored so narrations mentioning
    # "GTBank" or "Access Bank" in the text are NOT accidentally dropped.
    SKIP_HDR = re.compile(
        r"^(?:mybankstatement|guaranty trust bank|first bank|access bank plc|"
        r"united bank for africa|fidelity bank|union bank|stanbic\s+(?:ibtc\s+)?bank|fcmb(?:\s|$)|"
        r"wema bank|sterling bank|gtco|rc no\.|"
        r"account\s+(?:name|no\.?|number|type|branch|currency|sort)|"
        r"available\s+balance|book\s+balance|total\s+(?:debit|credit)|"
        r"opening\s+balance|closing\s+balance|statement\s+period|"
        r"dear\s+customer|page\s+\d+)",
        re.I,
    )

    in_tx        = False
    prev_bal     : Optional[Decimal] = None
    pending_ym   = ""
    pending_narr = ""
    pending_cr   = 0.0   # confirmed credit amount (0 = nothing pending)

    def _flush() -> None:
        nonlocal pending_ym, pending_narr, pending_cr
        if pending_cr > 0:
            add_credit(buckets, pending_ym, pending_cr,
                       pending_narr.strip(), account_name)
        pending_ym = pending_narr = ""
        pending_cr = 0.0

    def _kind_from_delta(amount: Decimal, balance: Decimal, narr: str = "") -> str:
        nonlocal prev_bal
        if prev_bal is None:
            # No previous balance — use narration heuristic for first row
            tl = narr.lower()
            if re.search(r"\bfrom\b|\bpayment\b|\bcredit\b|\bdeposit\b|\binflow\b|\breceived\b", tl):
                kind = "credit"
            elif re.search(r"\bto\b|\btransfer to\b|\bwithdraw\b|\bdebit\b|\boutflow\b|\bcharge\b|\bairtime\b|\bpurchase\b", tl):
                kind = "debit"
            else:
                kind = "credit"  # default to credit when truly ambiguous on first row
        else:
            delta = balance - prev_bal
            if   abs(delta - amount) <= Decimal("0.02"): kind = "credit"
            elif abs(delta + amount) <= Decimal("0.02"): kind = "debit"
            elif delta > 0:                               kind = "credit"
            elif delta < 0:                               kind = "debit"
            else:                                         kind = "credit"
        prev_bal = balance
        return kind

    def _process_amounts(m3, m2, narr_prefix: str) -> None:
        """Resolve amounts and set pending_cr / clear it."""
        nonlocal pending_cr, pending_narr, prev_bal
        if m3:
            debit   = float(m3.group(1).replace(",", ""))
            credit  = float(m3.group(2).replace(",", ""))
            balance = Decimal(m3.group(3).replace(",", ""))
            prev_bal = balance
            extra   = narr_prefix.strip()
            pending_narr = (pending_narr + " " + extra).strip() if extra else pending_narr
            if credit > 0 and debit == 0:
                pending_cr = credit
            else:
                pending_cr   = 0.0
                pending_narr = ""
        elif m2:
            amount  = Decimal(m2.group(1).replace(",", ""))
            balance = Decimal(m2.group(2).replace(",", ""))
            extra   = narr_prefix.strip()
            pending_narr = (pending_narr + " " + extra).strip() if extra else pending_narr
            kind = _kind_from_delta(amount, balance, pending_narr)
            if kind == "credit":
                pending_cr = float(amount)
            else:
                pending_cr   = 0.0
                pending_narr = ""

    for line in full_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # ── Section-start detection ───────────────────────────────────────
        if re.search(r"tran\.?\s*date", stripped, re.I) and \
           re.search(r"debit|credit|balance", stripped, re.I):
            in_tx = True
            continue

        # ── Page-header skip (anchored — won't kill narration lines) ─────
        if SKIP_HDR.match(stripped):
            continue

        if not in_tx:
            continue

        date_m = GTB_DATE.match(stripped)

        if date_m:
            _flush()
            raw_date   = date_m.group(1).replace("-", "/")
            p1, p2, yy = raw_date.split("/")
            if DATE_ORDER == "mdy" or (int(p2) > 12 >= int(p1)):
                mm = p1
            else:
                mm = p2
            pending_ym = f"{yy}-{mm}"

            # Everything after the transaction date (strip optional value-date)
            rest = stripped[date_m.end():].strip()
            rest = re.sub(r"^\d{2}[-/]\d{2}[-/]\d{4}\s*", "", rest).strip()

            m3 = MONEY3.search(rest)
            m2 = MONEY2.search(rest) if not m3 else None

            if m3 or m2:
                amt_match  = m3 or m2
                narr_chunk = rest[:rest.rfind(amt_match.group(0))].strip()
                pending_narr = narr_chunk
                _process_amounts(m3, m2, "")
            else:
                # Amounts not on this line yet — narration wraps
                pending_narr = rest
                pending_cr   = 0.0

        else:
            # ── Continuation line ─────────────────────────────────────────
            if not pending_ym:
                continue

            m3 = MONEY3.search(stripped)
            m2 = MONEY2.search(stripped) if not m3 else None

            if (m3 or m2) and pending_cr == 0.0:
                amt_match  = m3 or m2
                narr_chunk = stripped[:stripped.rfind(amt_match.group(0))].strip()
                _process_amounts(m3, m2, narr_chunk)
            elif not m3 and not m2:
                # Pure narration continuation
                pending_narr += " " + stripped

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
        r"\b(20\d{2})\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
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
    elif bank == "OPay_v2":
        # New OPay "Account Statement" format — requires pdftotext -layout
        # for accurate column-aligned extraction.
        buckets, account_name = parse_opay_v2(file_bytes)
    elif bank == "OPay_Business":
        # OPay Business "Account Statement" — ISO timestamp rows, 3-line blocks
        buckets, account_name = parse_opay_business(file_bytes)
    elif bank == "Moniepoint_Business":
        # Moniepoint Business — identical ISO-timestamp 3-line layout to OPay Business
        buckets, account_name = parse_opay_business(file_bytes)
    elif bank == "Kuda":
        buckets, account_name = parse_kuda(full_text)
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
