from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO

from PyPDF2 import PdfReader


MONEY_RE = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})*|\d+)\.\d{2}(?!\d)")
ROW_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(.*)$")

MONTH_NUM = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

# OPay: "dd Mon yyyy hh:mm:ss dd Mon yyyy Description -- credit balance"
OPAY_ROW_RE = re.compile(
    r"^(\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(20\d{2})\s+\d{2}:\d{2}:\d{2}",
    re.I,
)


@dataclass
class Transaction:
    page: int
    tran_date: str
    value_date: str
    narration: str
    amount: float
    balance: float
    kind: str
    category: str = ""
    reason: str = ""

    @property
    def ym(self) -> str:
        day, month, year = self.tran_date.split("/")
        return f"{year}-{month}"


def money_to_decimal(value: str) -> Decimal:
    return Decimal(value.replace(",", ""))


def detect_format(full_text: str) -> str:
    """Detect bank statement format from full text."""
    t = full_text.lower()
    if "opay digital" in t or "wallet account" in t or "9payment service" in t:
        return "opay"
    if "mybankstatement" in t or ("tran date value date narration" in t):
        return "zenith"
    return "generic"


def extract_pdf_text_rows(pdf_bytes: bytes, password: str = "") -> tuple[str, list[dict]]:
    reader = PdfReader(BytesIO(pdf_bytes))
    if reader.is_encrypted:
        result = reader.decrypt(password or "")
        if result == 0:
            raise ValueError("Incorrect or missing PDF password.")

    full_text_parts = []
    for page in reader.pages:
        full_text_parts.append(page.extract_text() or "")
    full_text = "\n".join(full_text_parts)

    fmt = detect_format(full_text)

    if fmt == "opay":
        records = _parse_opay_rows(full_text)
    else:
        records = _parse_zenith_rows(full_text)

    return full_text, records


def _parse_zenith_rows(full_text: str) -> list[dict]:
    records = []
    current = None
    in_transactions = False
    page_no = 1
    for raw_line in full_text.splitlines():
        line = raw_line.strip()
        if "\x0c" in raw_line:
            page_no += 1
        if line.startswith("Tran Date Value Date Narration"):
            in_transactions = True
            continue
        if not in_transactions:
            continue
        match = ROW_RE.match(line)
        if match:
            if current:
                records.append(current)
            current = {
                "page": page_no,
                "tran_date": match.group(1),
                "value_date": match.group(2),
                "lines": [match.group(3)],
            }
        elif current and not line.startswith(("mybankStatement", "Tran Date")):
            current["lines"].append(line)
    if current:
        records.append(current)
    return records


def _parse_opay_rows(full_text: str) -> list[dict]:
    """Parse OPay statement format.
    Each transaction starts with: dd Mon yyyy hh:mm:ss dd Mon yyyy Description -- credit balance
    or: dd Mon yyyy hh:mm:ss dd Mon yyyy Description debit -- balance
    """
    records = []
    lines = full_text.splitlines()
    current = None
    page_no = 1

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if "\x0c" in raw_line:
            page_no += 1

        m = OPAY_ROW_RE.match(line)
        if m:
            if current:
                records.append(current)
            day, mon, year = m.group(1), m.group(2), m.group(3)
            tran_date = f"{day}/{MONTH_NUM[mon.lower()]}/{year}"
            rest = line[m.end():].strip()
            # Remove the value date (second occurrence of date after time)
            rest = re.sub(
                r"^\d{2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+20\d{2}\s*",
                "", rest, flags=re.I
            )
            current = {
                "page": page_no,
                "tran_date": tran_date,
                "value_date": tran_date,
                "lines": [rest],
                "format": "opay",
            }
        elif current:
            current["lines"].append(line)

    if current:
        records.append(current)
    return records


def parse_summary_credits(full_text: str) -> dict[str, float]:
    summary = {}
    pattern = re.compile(
        r"\b(20\d{2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
        r"([\d,]+\.\d{2})\s+([\d,]+\.\d{2})",
        re.I,
    )
    month_map = {
        "jan": "01",
        "feb": "02",
        "mar": "03",
        "apr": "04",
        "may": "05",
        "jun": "06",
        "jul": "07",
        "aug": "08",
        "sep": "09",
        "oct": "10",
        "nov": "11",
        "dec": "12",
    }
    for match in pattern.finditer(full_text.replace("\n", " ")):
        year = match.group(1)
        month = month_map[match.group(2).lower()[:3]]
        credit = float(match.group(4).replace(",", ""))
        summary[f"{year}-{month}"] = credit
    return summary


def classify_credit(narration: str, account_name: str = "") -> tuple[str, str]:
    text = narration.lower()
    compact = re.sub(r"[^a-z0-9]+", "", text)

    # ── OWealth / savings round-trips ──────────────────────────────────────
    owealth_kw = [
        "owealth withdrawal", "owealth deposit", "owealth interest",
        "auto-save to owealth", "savings withdrawal", "owealth balance",
    ]
    if any(k in text for k in owealth_kw):
        return "self_transfer", "OWealth internal round-trip"

    # PiggyVest / Cowrywise
    if any(k in text for k in ["piggyvest", "piggy vest", "cowrywise", "cowry wise"]):
        return "self_transfer", "Savings platform round-trip"

    if "rsvl" in text or any(k in text for k in [" rev ", "reversal", "refund", "chargeback", "dispute", "clawback"]):
        return "reversal", "Reversal/refund keyword"

    loan_patterns = [
        r"\bokash\b",
        r"\beasemoni\b",
        r"\bfairmoney\b",
        r"\brenmoney\b",
        r"\bpalmcredit\b",
        r"\bbranch\s+loan\b",
        r"\bcarbon\s+loan\b",
        r"\bcarbon\s+credit\b",
        r"\bmigo\b",
        r"\blidya\b",
        r"\bzedvance\b",
        r"\bcreditwave\b",
        r"\bquickcheck\b",
        r"\bloan\s+disburs",
        r"\bcredit\s+disburs",
        r"\bdisbursement\b",
        r"\baella\b",
        r"\bkiakia\b",
        r"\bkia\s+kia\b",
        r"\bpage\s+financ",
        r"\blendigo\b",
        r"\bbranch\s+limit\b",
    ]
    for pattern in loan_patterns:
        if re.search(pattern, text):
            return "loan_disbursal", f"Loan keyword matched: {pattern}"

    # Self-transfer: check if sender name matches account holder name
    if account_name:
        name_lower = account_name.lower()
        name_parts = [p for p in name_lower.split() if len(p) > 3]
        if name_parts:
            matched = sum(1 for p in name_parts if p in text)
            has_verb = bool(re.search(r"\b(transfer from|from|myself|trf from)\b", text))
            if has_verb and matched >= 2:
                return "self_transfer", "Own-name transfer"

    # Gambling / betting winbacks
    betting_kw = ["sportybet","bet9ja","betnaija","1xbet","betking",
                  "betway","merrybet","nairabet","baba ijebu","naijabet","lotto ","sporty internet"]
    if any(k in text for k in betting_kw):
        return "non_business", "Gambling/betting keyword"

    # Non-business inflows — tightened keywords only
    if any(k in text for k in ["salary", "allowance"]):
        return "non_business", "Non-business keyword"

    return "real_credit", "Usable credit"


def parse_transactions(pdf_bytes: bytes, password: str = "") -> tuple[list[Transaction], dict[str, float]]:
    full_text, records = extract_pdf_text_rows(pdf_bytes, password)
    summary_credits = parse_summary_credits(full_text)
    # Extract account holder name for self-transfer detection
    name_m = (
        re.search(r"Account Name\s+([A-Z][A-Z ]{4,})", full_text) or
        re.search(r"Account Name\n([A-Z][A-Z ]+[A-Z])", full_text, re.I)
    )
    account_name = name_m.group(1).strip() if name_m else ""

    transactions = []
    previous_balance = None

    for record in records:
        full = " ".join(record["lines"])
        money_matches = list(MONEY_RE.finditer(full))
        if not money_matches:
            continue

        # ── OPay format: "Description -- credit_amount balance" or "Description debit_amount -- balance"
        if record.get("format") == "opay":
            balance = money_to_decimal(money_matches[-1].group())
            if len(money_matches) < 2:
                continue
            amount_match = money_matches[-2]
            amount = money_to_decimal(amount_match.group())
            before_amount = full[: amount_match.start()]
            # Credit: "--" appears immediately before the amount
            is_credit = bool(re.search(r"--\s*$", before_amount.rstrip()))
            kind = "credit" if is_credit else "debit"
            # Narration: text before the "--" and amount columns
            narration = re.sub(r"--\s*$", "", before_amount).strip()
            # Remove trailing channel/reference junk (Mobile + digits)
            narration = re.sub(r"\s+Mobile\S*$", "", narration).strip()
            previous_balance = balance
        else:
            # ── Zenith/generic: use balance movement to determine direction
            balance = money_to_decimal(money_matches[-1].group())
            amount = money_to_decimal(money_matches[-2].group()) if len(money_matches) >= 2 else Decimal("0")
            narration = full[: money_matches[-2].start()].strip() if len(money_matches) >= 2 else full[: money_matches[-1].start()].strip()

            if previous_balance is None:
                kind = "credit" if balance >= amount else "unknown"
            else:
                delta = balance - previous_balance
                if abs(delta - amount) <= Decimal("0.02"):
                    kind = "credit"
                elif abs(delta + amount) <= Decimal("0.02"):
                    kind = "debit"
                elif delta > 0:
                    kind = "credit"
                    amount = delta
                elif delta < 0:
                    kind = "debit"
                    amount = -delta
                else:
                    kind = "unknown"
            previous_balance = balance

        category = ""
        reason = ""
        if kind == "credit":
            category, reason = classify_credit(narration, account_name)

        transactions.append(
            Transaction(
                page=record["page"],
                tran_date=record["tran_date"],
                value_date=record["value_date"],
                narration=narration,
                amount=float(amount),
                balance=float(balance),
                kind=kind,
                category=category,
                reason=reason,
            )
        )

    return transactions, summary_credits


def monthly_analysis(transactions: list[Transaction], summary_credits: dict[str, float] | None = None) -> list[dict]:
    buckets = defaultdict(lambda: defaultdict(float))
    counts = defaultdict(int)

    for tx in transactions:
        if tx.kind != "credit":
            continue
        counts[tx.ym] += 1
        buckets[tx.ym]["parsed_gross"] += tx.amount
        buckets[tx.ym][tx.category] += tx.amount

    rows = []
    months = sorted(set(buckets) | set(summary_credits or {}))
    for ym in months:
        bucket = buckets[ym]
        gross = (summary_credits or {}).get(ym, bucket["parsed_gross"])
        deductions = (
            bucket["self_transfer"]
            + bucket["reversal"]
            + bucket["non_business"]
            + bucket["loan_disbursal"]
        )
        rows.append(
            {
                "ym": ym,
                "gross": gross,
                "parsed_gross": bucket["parsed_gross"],
                "self_transfer": bucket["self_transfer"],
                "reversal": bucket["reversal"],
                "non_business": bucket["non_business"],
                "loan_disbursal": bucket["loan_disbursal"],
                "deductions": deductions,
                "eligible_income": max(gross - deductions, 0),
                "count": int(counts[ym]),
            }
        )
    return rows
