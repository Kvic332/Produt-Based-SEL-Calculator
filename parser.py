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

    # ── 3. Reversals ──────────────────────────────────────────────────────
    # Covers: RSVL, ***RSVL, REV, reversal, refund, chargeback, dispute,
    # clawback — all case-insensitive (text already lowercased)
    if re.search(r"\*+rsvl|\brsvl\b|\brev\b|\brev-\b", text):
        return "reversal", "RSVL/REV reversal marker"
    if any(k in text for k in [
        "reversal", "refund", "chargeback", "chargbk",
        "dispute", "clawback", "returned funds", "charge back",
    ]):
        return "reversal", "Reversal keyword"

    # ── 4. Loan disbursements ─────────────────────────────────────────────
    # All major Nigerian loan apps + generic disbursement keywords
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
    # CRITICAL: only match name in the SENDER window (after transfer-from verb)
    # NOT in the full narration, to avoid flagging incoming transfers FROM others
    # to the account holder as self-transfers.
    if account_name and len(account_name) > 4:
        name_parts = [p for p in account_name.lower().split() if len(p) > 3]
        if name_parts:
            verb_m = re.search(
                r"\b(transfer from|transferred from|trf from|trf frm|"
                r"tfr from|from|frm)\b",
                text,
            )
            if verb_m:
                raw_window = text[verb_m.end(): verb_m.end() + 70]
                # Cut window at " to " or "|" — these separate sender from recipient
                sender_window = re.split(r"\s+to\s+|\s*\|\s*", raw_window)[0]
                matched = sum(1 for p in name_parts if p in sender_window)
                if matched >= 2:
                    return "self_transfer", "Own-name sender detected"
            if "myself" in text or "| myself" in text or "/myself" in text:
                return "self_transfer", "Explicit self-transfer (myself)"

    # Contribution from self (common in Nigerian banking — person sends money
    # from another account to this one, narration says "contribution")
    if re.search(r"\bcontribution\b", text) and account_name:
        name_parts = [p for p in account_name.lower().split() if len(p) > 3]
        matched = sum(1 for p in name_parts if p in text)
        if matched >= 1:
            return "self_transfer", "Self-contribution"

    # ── 6. Gambling / betting ─────────────────────────────────────────────
    betting_kw = [
        # Major Nigerian platforms
        "sportybet", "sporty bet", "sporty|", "sporty internet",
        "bet9ja", "bet 9ja", "betnaija",
        "1xbet", "1x bet",
        "betking", "bet king",
        "betway",
        "merrybet", "merry bet",
        "nairabet", "naira bet",
        "baba ijebu", "baba-ijebu",
        "naijabet",
        "lotto ", "lottomania", "premier lotto",
        "supabets", "supa bets",
        "cloudbet", "msport", "bangbet",
        "parimatch", "betboro", "betwinner",
        "22bet", "melbet",
        # Generic gambling signals
        "casino ", "jackpot ", "betting winnings",
    ]
    if any(k in text for k in betting_kw):
        return "non_business", "Gambling/betting keyword"

    # ── 7. Non-business inflows ───────────────────────────────────────────
    # Salary, allowance, support payments, contributions
    non_biz_exact = [
        "salary", "salaries",
        "allowance",
        " support ",         # spaces prevent matching "customer support"
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
    if "opay digital" in t or "wallet account" in t or "9payment service" in t:
        return "OPay"
    if "mybankstatement" in t or "tran date value date narration" in t:
        return "Zenith"
    if "moniepoint mfb" in t or "moniepoint microfinance" in t:
        return "Moniepoint"
    if "kuda mf bank" in t or "kudabank" in t:
        return "Kuda"
    if "palmpay" in t:
        return "PalmPay"
    if "guaranty trust" in t or "gtbank" in t or "gt bank" in t:
        return "GTBank"
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
            continue  # debit row
        narration = re.sub(r"--\s*$", "", before_amt).strip()
        narration = re.sub(r"\s+Mobile\S*$", "", narration).strip()
        add_credit(buckets, ym, amount, narration, account_name)
    return buckets, account_name


def parse_zenith(full_text: str) -> tuple[dict, str]:
    buckets: dict = {}
    account_name = _extract_account_name(full_text)

    in_tx = False
    current_lines: list[str] = []

    def process(lines: list[str]) -> None:
        full = " ".join(lines)

        # Match date at start
        m = re.match(r"^(\d{2}/\d{2}/\d{4})", full)
        if not m:
            return

        parts = m.group(1).split("/")
        ym = f"{parts[2]}-{parts[1]}"

        # Extract all monetary values
        money = list(re.finditer(r"[\d,]+\.\d{2}", full))
        if len(money) < 3:
            return

        try:
            # Zenith format: DEBIT CREDIT BALANCE
            debit  = float(money[-3].group().replace(",", ""))
            credit = float(money[-2].group().replace(",", ""))
            # balance = money[-1]  # not needed
        except Exception:
            return

        # Only process credits
        if credit <= 0:
            return

        narration = full[m.end():money[-3].start()].strip()

        add_credit(buckets, ym, credit, narration, account_name)

    for line in full_text.splitlines():
        line = line.strip()

        # Detect start of transaction table
        if "DATE POSTED VALUE DATE DESCRIPTION" in line.upper():
            in_tx = True
            continue

        if not in_tx or not line:
            continue

        # If line starts with date → new transaction
        if re.match(r"^\d{2}/\d{2}/\d{4}", line):
            if current_lines:
                process(current_lines)
            current_lines = [line]
        else:
            # continuation of previous line (multi-line description)
            if current_lines:
                current_lines.append(line)

    # process last block
    if current_lines:
        process(current_lines)

    return buckets, account_name


def parse_generic(full_text: str) -> tuple[dict, str]:
    """Balance-movement parser for GTBank, Access, UBA, FirstBank, Sterling, etc."""
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
    """Convert Excel serial date number to YYYY-MM string."""
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
    """
    Detect Excel format:
    - Mono standard:      header row with 'Transaction Date', 'Credit', 'Narration'
    - Moniepoint Business: header row with 'Date', 'Narration', 'Debit', 'Credit' + serial dates
    - Generic debit/credit table: any row with date + credit + debit columns
    """
    for i, row in enumerate(rows[:30]):
        h = [str(c or "").lower().strip() for c in row]

        # Moniepoint Business Excel: serial dates + date/narration/debit/credit headers
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

        # Mono standard: 'transaction date' as first column
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

        # Generic: has date + credit + debit
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


def parse_excel(file_bytes: bytes) -> tuple[dict, str]:
    """
    Parse Excel bank statement (Mono, Moniepoint Business, or generic format).
    Returns (buckets, account_name).
    """
    if not OPENPYXL_AVAILABLE:
        raise ImportError("openpyxl is required for Excel parsing. Add it to requirements.txt")

    import io
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows = [[cell.value for cell in row] for row in ws.iter_rows()]
    wb.close()

    fmt = _detect_excel_format(rows)
    if not fmt:
        raise ValueError("Could not detect Excel statement format. Supported: Mono, Moniepoint Business.")

    # Extract account name from header rows above the table
    account_name = ""
    for row in rows[:fmt["hdr_idx"]]:
        for j, cell in enumerate(row):
            cv = str(cell or "").lower().strip()
            if cv in ("account name:", "account name", "name"):
                # Next non-empty cell in same row
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

        # Date parsing
        if fmt_type == "moniepoint_excel":
            ym = _excel_serial_to_ym(row[date_col])
        else:
            date_str = str(row[date_col] or "").strip()
            # Try ISO format first: 2025-10-03 or 2025-10-03T...
            m = re.match(r"^(20\d{2})-(\d{2})", date_str)
            if m:
                ym = f"{m.group(1)}-{m.group(2)}"
            else:
                # Try dd/mm/yyyy
                m = re.match(r"^(\d{2})/(\d{2})/(20\d{2})", date_str)
                ym = f"{m.group(3)}-{m.group(2)}" if m else None

        if not ym:
            continue

        # Credit amount
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
        # Try to detect bank name from account name or default
        bank = "Mono Excel"
        summary: dict = {}
        return buckets, summary, bank, account_name

    # ── PDF ───────────────────────────────────────────────────────────────
    full_text = extract_pdf_text(file_bytes, password)
    bank = detect_bank(full_text)
    summary = parse_summary_credits(full_text)

    if bank == "OPay":
        buckets, account_name = parse_opay(full_text)
    elif bank == "Zenith":
        buckets, account_name = parse_zenith(full_text)
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

    # Summary table fallback
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
