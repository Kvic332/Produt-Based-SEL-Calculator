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
_FM_TX = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+\d+\s+"
    r"([+\-])\s*₦\s*([\d,]+\.\d{2})\s+"
    r"₦\s*[\d,]+\.\d{2}"
    r"(.*)"
)
_FM_HEADER = re.compile(
    r"^(?:FairMoney MFB|Licensed by CBN|28 Pade Odanye|Phone:|Email:|"
    r"Account number|Date Reference|number|Transaction|details|"
    r"Credit Debit Account|balance|Opening Balance|Total Deposits|"
    r"Total Withdrawals|Closing Balance|Page \d+ of \d+)",
    re.I,
)
_FM_MONEY_ONLY = re.compile(r"^₦\s*[\d,]+\.\d{2}$")
_FM_DATE_RANGE = re.compile(r"^\d{2}/\d{2}/\d{4}\s+-\s+\d{2}/\d{2}/\d{4}$")

# ── PalmPay parser constants ──────────────────────────────────────────────────
# Anchor: MM/DD/YYYY HH:MM:SS AM/PM  Description  +/-Amount  TransactionID
_PP_ROW = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+\d{2}:\d{2}:\d{2}\s+(?:AM|PM)\s+"
    r"(.+?)\s+([+\-][\d,]+\.\d{2})\s+\S+$",
    re.I,
)
# Simpler signed-amount capture for lines already split
_PP_SIGNED = re.compile(r"([+\-])([\d,]+\.\d{2})")

# Narration patterns specific to PalmPay to detect self-transfers
_PP_SELF_RECV = re.compile(
    r"^received\s+from\s+(.+)$", re.I
)

# ── Classification keyword lists (module-level for easy review / PR governance) ──

# OWealth / savings wallet round-trips
# Includes prefix-clipped variants caused by pdftotext column-offset bug
# where line[38:] clips the leading "O" → "Wealth..." or "A" → "uto-save..."
_OWEALTH_KW = [
    "owealth withdrawal", "owealth deposit", "owealth interest",
    "auto-save to owealth", "savings withdrawal", "owealth balance",
    "owealth credit",
    # Clipped variants (pdftotext drops leading char at description column boundary)
    "wealth withdrawal", "wealth deposit", "wealth interest",
    "uto-save to owealth",
]

# Third-party savings platforms
_SAVINGS_KW = [
    "piggyvest", "piggy vest", "piggy bank",
    "cowrywise", "cowry wise",
    "kuda save", "kuda vault",
]

# Loan apps and disbursement keywords
_LOAN_EXACT = [
    "fairmoney", "carbon loan", "branch loan", "branch limit",
    "palmcredit", "palm credit", "aella credit", "aella",
    "kiakia", "kia kia", "quickcheck", "quick check",
    "migo ", "lidya", "zedvance", "creditwave", "creditmfb",
    "renmoney", "easemoni", "okash", "xcredit", "newcredit",
    "fast credit", "page financ", "lendigo", "specta",
    "loans by sterling", "credit direct", "gtbank loan",
    "access loan", "uba loan", "first bank loan",
    "zenith loan", "wema loan", "fcmb loan",
    "incoming fairmoney",
    # PalmPay Cash Loan disbursements (CAL prefix in txn ID is in narration too)
    "disbursement-cash loan",
]
_LOAN_REGEX = [
    r"\bloan\s+disburs", r"\bdisbursement\b", r"\bcredit\s+disburs",
    r"\bloan\s+credit\b", r"\bloan\s+repay",
]

# Nigerian betting / gambling platforms
_BETTING_KW = [
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
    "casino ", "jackpot ", "betting winnings", "bet winnings",
    "winnings payout", "betting credit", "bet credit",
    "slot winnings", "poker winnings",
]

# Non-business inflows — exact keyword matches (lowercased)
# FIX #2 + #5: Added employer allowances, 13th month, medical balance, cake ifo
_NON_BIZ_EXACT = [
    "salary", "salaries",
    "allowance",
    " support ",
    "monthly stipend", "stipend",
    "upkeep",
    # Employer-specific disbursements (Access Bank / corporate payroll)
    "custodian allowance",
    "13th month",
    "medical balance",
    "medical allowance",
    # Staff welfare
    "cake ifo",
    "staff contribution for bereavement",
]


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
# CLASSIFICATION
# ════════════════════════════════════════════════════════════════════════════
def classify_credit(narration: str, account_name: str = "") -> tuple[str, str]:
    """
    Classify a credit transaction into one of:
      self_transfer  — OWealth, savings platforms, own-name transfers,
                       PalmPay same-name receipts
      reversal       — RSVL, REV, refund, chargeback, dispute
      loan_disbursal — Loan apps, disbursement keywords, PalmPay CAL loans
      non_business   — Gambling, betting, salary, allowance, support,
                       contribution, employer allowances, 13th month,
                       medical balance, cake ifo
      real_credit    — Genuine usable income

    SELF-TRANSFER FIX: Only flags self-transfer when the account holder name
    appears as the SENDER (60 chars after the transfer-from verb), NOT as the
    recipient. This prevents false flags like 'Transfer from X TO EMMANUEL'.

    CONTRIBUTION: ALL "contribution" credits are excluded (non_business).

    OVERDRAFT: Credits tagged as overdraft, OD credit, or facility drawdown
    are excluded as loan_disbursal.

    FIX #2/#5: Employer allowances (custodian allowance, 13th month allowance,
    medical balance, cake ifo) are now excluded as non_business.
    """
    text = narration.lower()

    # ── 1. OWealth / savings wallet round-trips ───────────────────────────
    if any(k in text for k in _OWEALTH_KW):
        return "self_transfer", "OWealth internal round-trip"

    # ── 2. Savings platforms ──────────────────────────────────────────────
    if any(k in text for k in _SAVINGS_KW):
        return "self_transfer", "Savings platform round-trip"

    # ── 3. Overdraft / facility drawdown → loan_disbursal ────────────────
    if re.search(r"\boverdraft\b|\bod\s+credit\b|\bod\s+limit\b|\bod\s+utiliz", text):
        return "loan_disbursal", "Overdraft credit"
    if re.search(r"\bfacility\s+drawdown\b|\bcredit\s+facility\b|\bod\s+reversal\b", text):
        return "loan_disbursal", "Facility/OD drawdown"

    # ── 4. Reversals ──────────────────────────────────────────────────────
    if re.search(r"\*+rsvl|\brsvl\b|\brev\b|\brev-\b", text):
        return "reversal", "RSVL/REV reversal marker"
    if any(k in text for k in [
        "reversal", "refund", "chargeback", "chargbk",
        "dispute", "clawback", "returned funds", "charge back",
    ]):
        return "reversal", "Reversal keyword"

    # ── 5. Loan disbursements ─────────────────────────────────────────────
    if any(k in text for k in _LOAN_EXACT):
        return "loan_disbursal", "Loan app keyword"
    for pat in _LOAN_REGEX:
        if re.search(pat, text):
            return "loan_disbursal", f"Loan pattern: {pat}"

    # ── 6. Self-transfer: own-name detection ─────────────────────────────
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

    # ── 6b. PalmPay self-transfer: "Received from <own name>" ────────────
    # PalmPay narrations use "Received from FIRSTNAME LASTNAME" format.
    # If the sender name matches the account holder, it's a self-transfer.
    # FIX #1 (part): catches own-account PalmPay round-trips.
    if account_name and len(account_name) > 4:
        recv_m = _PP_SELF_RECV.match(narration.strip())
        if recv_m:
            sender = recv_m.group(1).lower().strip()
            name_parts = [p for p in account_name.lower().split() if len(p) > 3]
            matched = sum(1 for p in name_parts if p in sender)
            if matched >= 2:
                return "self_transfer", "PalmPay own-name receipt"

    # ── 7. Contributions — ALL excluded regardless of sender ──────────────
    if re.search(r"\bcontribution\b|\bcontrib\b|\bajo\b|\besusu\b|\bcooperative\b", text):
        return "non_business", "Contribution/group-savings credit"

    # ── 8. Gambling / betting ─────────────────────────────────────────────
    if any(k in text for k in _BETTING_KW):
        return "non_business", "Gambling/betting keyword"

    # ── 9. Non-business inflows ───────────────────────────────────────────
    # FIX #2 + #5: _NON_BIZ_EXACT now includes employer allowances, medical,
    # 13th month, and cake ifo — no code change needed here, driven by the
    # module-level list above.
    if any(k in text for k in _NON_BIZ_EXACT):
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
# FIX #3: PalmPay detection placed AFTER OPay checks and guarded by tagline
# ════════════════════════════════════════════════════════════════════════════
def detect_bank(text: str) -> str:
    t = text.lower()

    # ── OPay v2 FIRST — must beat FairMoney check ─────────────────────────
    if ("trans. time" in t and
            ("opay digital" in t or "wallet account" in t or "9payment service" in t)):
        return "OPay_v2"

    if ("fairmoney mfb" in t or "fairmoney microfinance" in t) and "mybankstatement" not in t:
        return "FairMoney"

    if "opay digital" in t or "wallet account" in t or "9payment service" in t:
        return "OPay"

    # ── PalmPay: guarded by tagline OR text-only header keywords ──────────
    # Logo text is an image so "digital finance that fits your life" may not
    # extract. Fall back to the unique column combination: "total money in"
    # + "transaction detail" + "money out (ngn)" which only appears on PalmPay.
    if "digital finance that fits your life" in t or "palmpay.com" in t:
        return "PalmPay"
    if "total money in" in t and "transaction detail" in t and "money out (ngn)" in t:
        return "PalmPay"

    # ── Kuda: detected before generic MBS checks ─────────────────────────
    if "kuda mf bank" in t or "kudabank" in t or "kuda technologies" in t:
        return "Kuda"

    # ── Zenith Corporate BOP: MUST be detected before any bank-name scan ──
    # because Zenith Corporate narrations frequently contain other bank names
    # (FCMB, GTB, Access etc.) that would be picked up first.
    # Primary signal: "date posted" + "value date" columns (unique to Zenith BOP).
    if "date posted" in t and "value date" in t:
        return "Zenith_Corporate"

    # ── mybankStatement-format banks (explicit detection) ─────────────────
    # All of these use the mybankStatement PDF engine which produces a
    # uniform Tran Date / Value Date / Narration / Debit / Credit / Balance
    # column layout. They all route to parse_gtbank.
    if "guaranty trust" in t or "gtbank" in t or "gt bank" in t or "gtco" in t:
        return "GTBank"
    if "access bank" in t:
        # Access direct e-statements use "Posted Date  Value Date  Description  Debit (NGN)  Credit (NGN)"
        # Access MBS statements use "mybankstatement" watermark
        if "posted date" in t and "credit (ngn)" in t and "mybankstatement" not in t:
            return "Access_Direct"
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
    # ── MBS banks not previously detected — now explicit ─────────────────
    # FIX: These 19 banks were silently falling through to the "Zenith"
    # branch which routed them to parse_zenith (NIP CR parser). parse_zenith
    # finds zero credits on MBS PDFs because MBS uses Debit/Credit columns,
    # not NIP CR narration markers. Now detected explicitly and added to
    # _MYBANKSTATEMENT_BANKS so they correctly route to parse_gtbank.
    if "ecobank" in t:
        return "Ecobank"
    if "polaris bank" in t:
        return "Polaris"
    if "heritage bank" in t:
        return "Heritage"
    if "keystone bank" in t:
        return "Keystone"
    if "globus bank" in t:
        return "Globus"
    if "jaiz bank" in t:
        return "Jaiz"
    if "unity bank" in t:
        return "Unity"
    if "providus bank" in t:
        return "Providus"
    if "lotus bank" in t:
        return "Lotus"
    if "titan trust" in t:
        return "TitanTrust"
    if "optimus bank" in t:
        return "Optimus"
    if "parallex bank" in t:
        return "Parallex"
    if "vfd microfinance" in t or "vfd mfb" in t:
        return "VFD"
    if "octopus mfb" in t or "octopus microfinance" in t:
        return "OctopusMFB"
    if "arm securities" in t:
        return "ARM"
    if "fsdh merchant" in t:
        return "FSDH"
    if "meristem" in t:
        return "Meristem"
    if "first-ally" in t or "first ally capital" in t:
        return "FirstAlly"
    if "investment one" in t:
        return "InvestmentOne"

    # ── Zenith ────────────────────────────────────────────────────────────
    # Zenith retail (mybankStatement engine) — must come AFTER all named MBS
    # banks above. Any remaining MBS PDF that wasn't caught above gets
    # "MBS_Generic" instead of "Zenith" to avoid the parse_zenith mis-route.
    if "zenith" in t and ("mybankstatement" in t or "tran date value date narration" in t):
        return "Zenith"
    if "mybankstatement" in t or "tran date value date narration" in t:
        return "MBS_Generic"

    if "moniepoint mfb" in t or "moniepoint microfinance" in t:
        return "Moniepoint"
    if "kuda mf bank" in t or "kudabank" in t:
        return "Kuda"

    # Secondary passes (less specific)
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


# ════════════════════════════════════════════════════════════════════════════
# NEW: PALMPAY PARSER (FIX #1)
# ════════════════════════════════════════════════════════════════════════════
def parse_palmpay(full_text: str) -> tuple[dict, str]:
    """
    Parser for PalmPay "Account Statement" PDF format.

    Layout characteristics:
    - Header: Name, Phone Number, Account Number, Total Money In/Out,
      Statement Period, Print Time
    - Each transaction row:
        MM/DD/YYYY HH:MM:SS AM/PM  Description  +/-Amount.00  TransactionID
    - Positive amounts (+) = Money In; negative amounts (-) = Money Out
    - No balance column — cannot use balance-delta method
    - Date format is US-style MM/DD/YYYY (not DD/MM/YYYY)

    Self-transfer detection (two layers):
    1. "Received from <OWN NAME>" — same name as account holder → self_transfer
    2. Disbursement-Cash loan / prefix CAL in narration → loan_disbursal
       (PalmPay internal loan product)

    Notes:
    - "Auto deduct-Cash loan" / "Auto deduct-Installment loan" are repayments
      (debits) — they appear as negative amounts and are ignored.
    - "Received from Agent" credits are treated as real_credit (cash deposits
      via PalmPay agents).
    - "Disbursement-Cash loan" is positive but is a loan draw → loan_disbursal.
    """
    buckets: dict = {}

    # ── Extract account name ──────────────────────────────────────────────
    # PalmPay PDF: "Name  FIRSTNAME LASTNAME" near the top
    account_name = ""
    name_m = re.search(r"^Name\s{2,}([A-Z][A-Z ]{4,})", full_text, re.MULTILINE)
    if name_m:
        account_name = name_m.group(1).strip()
    if not account_name:
        account_name = _extract_account_name(full_text)

    # ── Parse transaction lines ───────────────────────────────────────────
    # PalmPay renders each transaction across one or two lines in the PDF.
    # PyPDF2 typically concatenates them into a single line with the
    # date-time at the start. We scan for lines beginning with MM/DD/YYYY.
    DATE_START = re.compile(
        r"^(\d{2}/\d{2}/\d{4})\s+\d{2}:\d{2}:\d{2}\s+(?:AM|PM)\s+(.*)"
    )
    SIGNED_AMT = re.compile(r"([+\-])([\d,]+\.\d{2})")

    lines = full_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        dm = DATE_START.match(line)
        if dm:
            raw_date = dm.group(1)   # MM/DD/YYYY
            rest     = dm.group(2).strip()

            # If rest is empty or too short, try merging the next line
            if len(rest) < 5 and i + 1 < len(lines):
                rest = (rest + " " + lines[i + 1].strip()).strip()
                i += 1

            # Find the signed amount — last +/- amount token in the rest
            amt_matches = list(SIGNED_AMT.finditer(rest))
            if not amt_matches:
                i += 1
                continue

            last_amt = amt_matches[-1]
            sign     = last_amt.group(1)
            amount   = float(last_amt.group(2).replace(",", ""))

            # Only process credits (positive / Money In)
            if sign != "+":
                i += 1
                continue

            # Narration: everything before the last signed amount token
            narration = rest[:last_amt.start()].strip()
            # Strip trailing transaction ID fragment (alphanumeric, no spaces)
            narration = re.sub(r"\s+\w{8,}\s*$", "", narration).strip()

            # Parse date: MM/DD/YYYY → YYYY-MM
            parts = raw_date.split("/")
            if len(parts) == 3:
                mm, dd, yyyy = parts
                ym = f"{yyyy}-{mm.zfill(2)}"
            else:
                i += 1
                continue

            add_credit(buckets, ym, amount, narration, account_name)

        i += 1

    return buckets, account_name


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


def _parse_opay_v2_text(full_text: str) -> tuple[dict, str]:
    """
    Fallback parser for OPay v2 when pdftotext -layout is unavailable.
    PyPDF2 still extracts the transaction rows, but multi-line descriptions and
    channel/reference text are collapsed differently from the layout extractor.
    """
    if not full_text:
        return {}, ""

    TXN_START = re.compile(
        r"^\s*(\d{2})\s+"
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+(20\d{2})\s+\d{2}:\d{2}:\d{2}",
        re.I,
    )
    AMOUNTS_PAT = re.compile(
        r"([\d,]+\.\d{2}|--)\s+([\d,]+\.\d{2}|--)\s+([\d,]+\.\d{2})\s+"
        r"(?:Mobile|POS|Web|USSD|ATM|Agent|Internet|Bank)",
        re.I,
    )
    VALUE_DATE_PREFIX = re.compile(
        r"^\s*\d{2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+20\d{2}\s*",
        re.I,
    )
    TRAIL_REF = re.compile(r"\s+\d{10,}\s*$")
    SECTION_END = re.compile(r"Savings Account", re.I)

    account_name = _extract_account_name(full_text)
    buckets: dict = {}
    blocks: list[list[str]] = []
    current: list[str] = []

    for raw in full_text.splitlines():
        line = raw.rstrip()
        if SECTION_END.search(line) and (blocks or current):
            break
        if TXN_START.match(line):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)

    if current:
        blocks.append(current)

    for block in blocks:
        first = block[0]
        m = TXN_START.match(first)
        if not m:
            continue

        ym = f"{m.group(3)}-{MONTH_NUM[m.group(2).lower()[:3]]}"
        full_block = " ".join(ln.strip() for ln in block if ln.strip())
        amt_m = AMOUNTS_PAT.search(full_block)
        if not amt_m:
            continue

        credit_tok = amt_m.group(2)
        if credit_tok == "--":
            continue

        narration = full_block[m.end():amt_m.start()]
        narration = VALUE_DATE_PREFIX.sub("", narration)
        narration = TRAIL_REF.sub("", narration)
        narration = re.sub(r"\s+", " ", narration).strip()
        if not narration:
            continue

        add_credit(
            buckets,
            ym,
            float(credit_tok.replace(",", "")),
            narration,
            account_name,
        )

    return buckets, account_name


def parse_opay_v2(pdf_bytes: bytes) -> tuple[dict, str]:
    """
    Parser for the OPay "Account Statement" format (2025+).
    Requires pdftotext -layout for accurate column-aligned extraction.
    """
    layout_text = extract_pdf_text_layout(pdf_bytes)
    if not layout_text:
        return _parse_opay_v2_text(extract_pdf_text(pdf_bytes))

    DATE_LINE = re.compile(
        r"^\s{0,4}(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+(20\d{2})\s+\d{2}:\d{2}:\d{2}",
        re.I,
    )
    AMOUNTS_PAT = re.compile(
        r"([\d,]+\.\d{2}|--)\s+([\d,]+\.\d{2}|--)\s+([\d,]+\.\d{2})\s+"
        r"(?:Mobile|POS|Web|USSD|ATM|Agent|Internet|Bank)",
        re.I,
    )
    DESC_CONT  = re.compile(r"^\s{36,}(\S.*)$")
    TRAIL_REF  = re.compile(r"\s+\d{10,}\s*$")
    TRANS_TIME = re.compile(r"Trans\.\s*Time", re.I)
    SECTION_END = re.compile(r"Savings Account", re.I)

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

    lines = layout_text.splitlines()
    buckets: dict = {}

    TXN_START = re.compile(
        r"^\s*(\d{2})\s+"
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\s+(20\d{2})\s+\d{2}:\d{2}:\d{2}",
        re.I,
    )

    blocks = []
    current = []

    for raw in lines:
        line = raw.rstrip()

        if TRANS_TIME.search(line):
            continue

        if SECTION_END.search(line):
            if blocks or current:   # already have wallet txns → stop here
                break
            else:
                continue       

        if TXN_START.match(line):

            if current:
                blocks.append(current)

            current = [line]

        else:
            if current:
                current.append(line)

    if current:
        blocks.append(current)

    for block in blocks:

        first = block[0]

        m = TXN_START.match(first)

        if not m:
            continue

        day = m.group(1)
        mon = m.group(2).lower()[:3]
        year = m.group(3)

        ym = f"{year}-{MONTH_NUM[mon]}"

        full_block = "\n".join(block)

        amt_m = AMOUNTS_PAT.search(full_block)

        if not amt_m:
            continue

        debit_tok = amt_m.group(1)
        credit_tok = amt_m.group(2)

        if credit_tok == "--":
            continue

        credit = float(credit_tok.replace(",", ""))

        narration_lines = []

        for ln in block[1:]:

            if AMOUNTS_PAT.search(ln):
                break

            cleaned = TRAIL_REF.sub("", ln).strip()

            if not cleaned:
                continue

            narration_lines.append(cleaned)

        inline_desc = first[m.end():]

        if amt_m:
            inline_desc = inline_desc[:max(0, amt_m.start() - m.end())]

        inline_desc = inline_desc.strip()

        if inline_desc:
            narration_lines.insert(0, inline_desc)

        narration = " ".join(narration_lines)

        narration = re.sub(r"\s+", " ", narration).strip()

        if not narration:
            continue

        add_credit(
            buckets,
            ym,
            credit,
            narration,
            account_name,
        )
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
    """
    buckets: dict     = {}
    account_name: str = _extract_account_name(full_text)

    GTB_DATE = re.compile(r"^(\d{2}[-/]\d{2}[-/]\d{4})")
    MONEY3   = re.compile(r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$")
    MONEY2   = re.compile(r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$")
    DATE_ORDER = "dmy"
    period_m = re.search(
        r"\bPeriod\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s*20\d{2}",
        full_text, re.I,
    )
    if period_m:
        first_date_m = re.search(r"\n\s*(\d{2})[-/](\d{2})[-/](20\d{2})", full_text)
        if first_date_m and int(first_date_m.group(1)) == MONTH_MAP[period_m.group(1).lower()[:3]]:
            DATE_ORDER = "mdy"

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
    pending_cr   = 0.0

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
            tl = narr.lower()
            if re.search(r"\bfrom\b|\bpayment\b|\bcredit\b|\bdeposit\b|\binflow\b|\breceived\b", tl):
                kind = "credit"
            elif re.search(r"\bto\b|\btransfer to\b|\bwithdraw\b|\bdebit\b|\boutflow\b|\bcharge\b|\bairtime\b|\bpurchase\b", tl):
                kind = "debit"
            else:
                kind = "credit"
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

        if re.search(r"tran\.?\s*date", stripped, re.I) and \
           re.search(r"debit|credit|balance", stripped, re.I):
            in_tx = True
            continue

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
                pending_narr = rest
                pending_cr   = 0.0

        else:
            if not pending_ym:
                continue

            m3 = MONEY3.search(stripped)
            m2 = MONEY2.search(stripped) if not m3 else None

            if (m3 or m2) and pending_cr == 0.0:
                amt_match  = m3 or m2
                narr_chunk = stripped[:stripped.rfind(amt_match.group(0))].strip()
                _process_amounts(m3, m2, narr_chunk)
            elif not m3 and not m2:
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
    Credits identified by '+' sign prefix on the amount.
    'Incoming FairMoney' credits classified as loan_disbursal and excluded.
    """
    buckets: dict    = {}
    account_name: str = ""
    lines = full_text.splitlines()

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

        if pending_sign is not None:
            if _FM_HEADER.match(stripped):
                continue
            if _FM_MONEY_ONLY.match(stripped):
                continue
            if _FM_DATE_RANGE.match(stripped):
                continue
            if stripped == account_name:
                continue
            if re.match(r"^\d{10}$", stripped):
                continue
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
    parser entirely. Handles files with invalid/missing stylesheet XML.
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
        shared: list[str] = []
        with zf.open("xl/sharedStrings.xml") as f:
            tree = _etree.parse(f)
            for si in tree.findall(".//s:si", ns):
                texts = si.findall(".//s:t", ns)
                shared.append("".join(t.text or "" for t in texts))

        with zf.open("xl/worksheets/sheet1.xml") as f:
            tree = _etree.parse(f)
        xml_rows = tree.findall(".//s:row", ns)

    def _row_dict(xml_row) -> dict[str, str]:
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

    account_name = ""
    for xml_row in xml_rows[:10]:
        rd = _row_dict(xml_row)
        cols = sorted(rd.keys())
        for i, col in enumerate(cols):
            if "account name" in rd[col].lower():
                for next_col in cols[i + 1:]:
                    nv = rd[next_col].strip(" -")
                    if nv:
                        account_name = nv
                        break
                break
        if account_name:
            break

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
    Primary: openpyxl. Fallback: direct zip+lxml for corrupt stylesheet files.
    """
    if OPENPYXL_AVAILABLE:
        try:
            import io
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
            ws = wb.active
            rows = [[cell.value for cell in row] for row in ws.iter_rows()]
            wb.close()
        except Exception:
            return _parse_excel_direct(file_bytes)
    else:
        return _parse_excel_direct(file_bytes)

    fmt = _detect_excel_format(rows)
    if not fmt:
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

# mybankStatement-engine banks — all share the explicit Debit/Credit column
# format and are handled by parse_gtbank regardless of the bank name.
# FIX: Added all 19 MBS banks that were previously mis-routed to parse_zenith
# (which found zero credits because it looks for NIP CR patterns, not
# Debit/Credit columns). Also added "MBS_Generic" as the safe fallback for
# any MBS-watermarked PDF whose bank name was not caught by a specific check.

# ════════════════════════════════════════════════════════════════════════════
# KUDA BANK PARSER
# Format: Tab-separated tokens — PyPDF2 splits each tab into its own line.
# Sequence per transaction: DD/MM/YY / HH:MM:SS / ₦Amount / category_words
#   / To-From info / description / ₦Balance
# Outward direction = category contains "outward", "paid", "payment", etc.
# ════════════════════════════════════════════════════════════════════════════
_KUDA_DATE  = re.compile(r"^(\d{2}/\d{2}/\d{2})$")
_KUDA_TIME  = re.compile(r"^\d{2}:\d{2}:\d{2}$")
_KUDA_MONEY = re.compile(r"^₦([\d,]+\.\d{2})$")
_KUDA_OUTWARD = re.compile(
    r"^(?:outward|paid|payment|bill|airtime|data|electricity|cable|"
    r"withdrawal|atm|pos charge|bank charge|fee|stamp duty|debit|"
    r"spend and save|spend\s*\+\s*save)", re.I
)


def parse_kuda(full_text: str) -> tuple[dict, str]:
    """
    Parser for Kuda MF Bank PDF statements.

    PyPDF2 extracts Kuda tabs as individual lines. Each transaction block:
      DD/MM/YY       ← date
      HH:MM:SS       ← time (used for dedup key)
      ₦Amount        ← transaction amount
      category_word  ← direction indicator (tab-split)
      to/from info
      description
      ₦NewBalance    ← new running balance (second ₦ amount)

    Direction: "inward transfer" or "local funds transfer" = credit.
    "outward", "spend and save", "paid", "bills", "airtime" etc. = debit.
    
    Page-boundary dedup: Kuda PDFs repeat transactions at page seams.
    Dedup key = (date, time, amount) — unique per real transaction.
    Same amount at same second from same direction = same transaction.

    spend+save detection: The Spend+Save feature tab-splits its category
    into ['spend', 'and\tsave', 'spend\tand\tsave']. We join all tokens
    (replacing tabs) before checking the outward pattern.
    """
    m = re.search(r"Account Number\s*:\s*\d+\s+([A-Z][A-Z ]{3,})", full_text)
    account_name = m.group(1).strip() if m else ""

    lines = [ln.strip() for ln in full_text.split("\n")]
    buckets: dict = {}
    seen_keys: set = set()   # (date, time, first_amount) dedup

    i = 0
    while i < len(lines):
        dm = _KUDA_DATE.match(lines[i])
        if not dm:
            i += 1
            continue

        date_str = dm.group(1)  # DD/MM/YY

        # Collect lines until the next date or page header
        window = []
        j = i + 1
        while j < len(lines):
            wl = lines[j].strip()
            if _KUDA_DATE.match(wl):
                break
            if re.match(r"^(?:Page \d+|All\s+Statements|Kuda\s+MF)", wl, re.I):
                j += 1
                continue
            window.append(wl)
            j += 1

        # Extract time, amounts, and narration tokens from the window
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

        # Dedup by (date, time, first_amount) — eliminates page-boundary repeats
        dedup_key = (date_str, time_found or "notime", amounts[0])
        if dedup_key in seen_keys:
            i = j
            continue
        seen_keys.add(dedup_key)

        # Direction detection: join ALL tokens with tabs replaced by spaces
        # This correctly catches tab-split "spend and save"
        category_full = re.sub(r"\t", " ", " ".join(text_tokens)).strip().lower()
        is_outward = bool(_KUDA_OUTWARD.search(category_full))
        # Standalone "spend" token = savings deduction, always outward
        if not is_outward and text_tokens and re.match(r"^spend$", text_tokens[0].strip(), re.I):
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

        amount = Decimal(amounts[0].replace(",", ""))
        narration = re.sub(r"\t", " ", " ".join(text_tokens))
        narration = re.sub(r"\s+", " ", narration).strip()

        # Skip stamp duty / government levies (these are tiny bank charges, not income)
        if re.search(r"\bstamp\s+duty\b|\belectronic.*levy\b|\bfgn.*levy\b", narration, re.I):
            i = j
            continue

        # Safety: catch any spend+save that escaped the category check
        if re.search(r"\bspend\s+and\s+save\b|\bspend\s*\+\s*save\b", narration, re.I):
            i = j
            continue

        kind, _ = classify_credit(narration, account_name)

        if ym not in buckets:
            buckets[ym] = _empty_bucket()
        b = buckets[ym]
        b["gross"] += float(amount)
        b["count"] += 1
        b[kind] += float(amount)
        b.setdefault(f"{kind}_txns", []).append(
            {"date": date_str, "narration": narration, "amount": float(amount)}
        )

        i = j

    return buckets, account_name

# ════════════════════════════════════════════════════════════════════════════
# ACCESS BANK DIRECT E-STATEMENT CONSTANTS
# BUG 2 FIX: _AX_DATE_ROW and _AX_TAIL were referenced in parse_access_direct
# but never defined, causing an immediate NameError on any Access direct PDF.
# ════════════════════════════════════════════════════════════════════════════

# Matches the opening date pair on an Access direct e-statement row:
#   DD-MMM-YY  DD-MMM-YY  <rest of line>
# e.g. "10-SEP-25  10-SEP-25  NIP Transfer to MODUPE COMFORT..."
_AX_DATE_ROW = re.compile(
    r"^(\d{2}-[A-Za-z]{3}-\d{2})\s+(\d{2}-[A-Za-z]{3}-\d{2})\s*(.*)"
)

# Matches the three trailing amount columns at the end of a combined row:
#   debit_or_dash  credit_or_dash  balance
# e.g. "-  5,000.00  1,011.73"  OR  "1,000.00  -  1,011.73"
_AX_TAIL = re.compile(
    r"([\d,]+\.\d{2}|-)\s+([\d,]+\.\d{2}|-)\s+([\d,]+\.\d{2})\s*$"
)


def _ax_to_ym(ds: str) -> str | None:
    parts = ds.upper().split("-")
    if len(parts) != 3:
        return None
    _, mon, yr = parts
    mon_num = MONTH_NUM.get(mon.lower())
    if not mon_num:
        return None
    return f"{int(yr) + 2000}-{mon_num}"


def parse_access_direct(full_text: str) -> tuple[dict, str]:
    m = re.search(r"Account Name[:\s]+([A-Z][A-Z ]{3,})", full_text, re.I)
    account_name = m.group(1).strip() if m else ""

    lines = [ln.strip() for ln in full_text.split("\n")]
    buckets: dict = {}

    pending_date: str | None = None
    pending_ym: str | None = None
    pending_chunks: list[str] = []

    _skip = re.compile(
        r"^(?:Posted Date|Value Date|TRANSACTIONS|This is an automated|"
        r"Generated on|Account Details|Financial Summary|ACCOUNT ST|"
        r"Page \d+|Cleared|UnCleared|Opening Balance|Closing Balance|"
        r"Total Withdrawals|Total Deposits)", re.I
    )

    def flush(chunks: list[str], date: str, ym: str) -> None:
        if not chunks or not ym:
            return
        combined = " ".join(chunks)
        am = _AX_TAIL.search(combined)
        if not am:
            return
        debit_s, credit_s = am.group(1), am.group(2)
        if credit_s == "-" or not credit_s:
            return
        credit_val = Decimal(credit_s.replace(",", ""))
        if credit_val <= 0:
            return
        narr = _AX_TAIL.sub("", combined).strip()
        narr = re.sub(r"\s+", " ", narr)
        # Normalise PyPDF2 word-splits: "CUST ODIAN" → "CUSTODIAN", "ALLOW ANCE" → "ALLOWANCE"
        narr = re.sub(r"\bCUST ODIAN\b", "CUSTODIAN", narr)
        narr = re.sub(r"\bALLOW ANCE\b", "ALLOWANCE", narr)
        narr = re.sub(r"\bSALAR Y\b", "SALARY", narr)
        narr = re.sub(r"\bT O\b", "TO", narr)
        narr = re.sub(r"\bFROM\s+F AVOUR\b", "FROM FAVOUR", narr)
        narr = re.sub(r"  +", " ", narr)
        kind, _ = classify_credit(narr, account_name)
        if ym not in buckets:
            buckets[ym] = _empty_bucket()
        b = buckets[ym]
        b["gross"] += float(credit_val)
        b["count"] += 1
        b[kind] += float(credit_val)
        b.setdefault(f"{kind}_txns", []).append(
            {"date": date, "narration": narr, "amount": float(credit_val)}
        )

    for line in lines:
        if not line or _skip.match(line):
            continue
        dm = _AX_DATE_ROW.match(line)
        if dm:
            flush(pending_chunks, pending_date or "", pending_ym or "")
            pending_date = dm.group(1)
            pending_ym = _ax_to_ym(pending_date)
            pending_chunks = [dm.group(3)] if dm.group(3).strip() else []
        elif pending_date:
            pending_chunks.append(line)

    flush(pending_chunks, pending_date or "", pending_ym or "")
    return buckets, account_name


_MYBANKSTATEMENT_BANKS = {
    # Original 10 (explicitly detected by name keyword)
    "GTBank", "Access", "FirstBank", "UBA",
    "Fidelity", "Union", "Stanbic", "FCMB", "Wema", "Sterling",
    # Zenith retail (via mybankStatement engine, not the corporate BOP format)
    "Zenith",
    # 19 newly added MBS banks (previously silent zero-credit failures)
    "Ecobank", "Polaris", "Heritage", "Keystone", "Globus",
    "Jaiz", "Unity", "Providus", "Lotus", "TitanTrust",
    "Optimus", "Parallex", "VFD", "OctopusMFB",
    "ARM", "FSDH", "Meristem", "FirstAlly", "InvestmentOne",
    # Fallback: any MBS-watermarked PDF not caught by a named check above
    "MBS_Generic",
}


def parse_transactions(file_bytes: bytes, password: str = "",
                       filename: str = "") -> tuple[dict, dict, str, str]:
    """
    Auto-detects PDF vs Excel and routes to the correct parser.

    Returns:
        buckets        — {ym: {gross, count, self_transfer, reversal,
                               non_business, loan_disbursal, real_credit}}
        summary_credits — {ym: credit_amount} from statement summary table
        bank_name      — detected bank string
        account_name   — extracted account holder name
    """
    # ── Excel detection ───────────────────────────────────────────────────
    is_excel = (filename.lower().endswith((".xlsx", ".xls")) or
                file_bytes[:4] in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0"))
    if is_excel:
        buckets, account_name = parse_excel(file_bytes)
        return buckets, {}, "Mono Excel", account_name

    # ── PDF ───────────────────────────────────────────────────────────────
    full_text = extract_pdf_text(pdf_bytes=file_bytes, password=password)
    bank = detect_bank(full_text)
    summary = parse_summary_credits(full_text)

    if bank == "FairMoney":
        buckets, account_name = parse_fairmoney(full_text)
    elif bank == "OPay":
        buckets, account_name = parse_opay(full_text)
    elif bank == "OPay_v2":
        buckets, account_name = parse_opay_v2(file_bytes)
    elif bank == "PalmPay":
        buckets, account_name = parse_palmpay(full_text)
    elif bank == "Kuda":
        buckets, account_name = parse_kuda(full_text)
    elif bank == "Access_Direct":
        buckets, account_name = parse_access_direct(full_text)
    elif bank in _MYBANKSTATEMENT_BANKS:
        # Access MBS routes here; Access_Direct (direct e-statement) routes above
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
