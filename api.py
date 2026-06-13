# Copyright (c) 2026 Kenechukwu (Kvic7). All rights reserved.
# Proprietary and confidential — see LICENSE. No license granted.
"""
PARSIO Credit Intelligence API
================================
POST /analyse   — upload a bank statement PDF, receive a JSON credit verdict.

Authentication
--------------
Every request must include the header:
    X-API-Key: <key>

Keys are stored in the environment variable PARSIO_API_KEYS as a
comma-separated list, e.g.:
    PARSIO_API_KEYS=bank_A_key_abc123,bank_B_key_xyz789

Running locally
---------------
    pip install -r requirements_api.txt
    uvicorn api:app --reload --port 8000

    curl -X POST http://localhost:8000/analyse \
      -H "X-API-Key: testkey" \
      -F "pdf=@statement.pdf" \
      -F "location=Core" \
      -F "product_type=NEW" \
      -F "tenor=12"
"""
from __future__ import annotations

import datetime
import os
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.models import APIKey, APIKeyIn
from fastapi.security import APIKeyHeader

import bank_parser
from sel_rules import calculate_eligibility

# ── App ───────────────────────────────────────────────────────────────────────

_api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

app = FastAPI(
    title="PARSIO Credit Intelligence API",
    description=(
        "Upload a Nigerian bank statement PDF and receive a structured "
        "credit-eligibility verdict. Powered by the PARSIO engine."
    ),
    version="1.0.0",
    contact={"name": "PARSIO / Kvic7™", "email": "kenechosen@gmail.com"},
    license_info={"name": "Proprietary — All rights reserved"},
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

_TZ_LAGOS = datetime.timezone(datetime.timedelta(hours=1), name="WAT")


# ── Auth ──────────────────────────────────────────────────────────────────────

def _valid_keys() -> set[str]:
    raw = os.environ.get("PARSIO_API_KEYS", "")
    return {k.strip() for k in raw.split(",") if k.strip()}


def verify_api_key(x_api_key: Annotated[str | None, Depends(_api_key_scheme)] = None) -> str:
    keys = _valid_keys()
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API keys not configured on server.",
        )
    if not x_api_key or x_api_key not in keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header.",
        )
    return x_api_key


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_monthly_breakdown(buckets: dict, today_ym: str) -> list[dict]:
    """Convert parser buckets to a clean list, newest-first, excluding current month."""
    rows = []
    for ym, b in sorted(buckets.items(), reverse=True):
        if ym >= today_ym:
            continue
        rows.append({
            "month":            ym,
            "gross_inflow":     round(float(b.get("gross") or 0), 2),
            "real_credit":      round(float(b.get("real_credit") or 0), 2),
            "self_transfer":    round(float(b.get("self_transfer") or 0), 2),
            "reversal":         round(float(b.get("reversal") or 0), 2),
            "loan_disbursal":   round(float(b.get("loan_disbursal") or 0), 2),
            "non_business":     round(float(b.get("non_business") or 0), 2),
            "transaction_count": int(b.get("count") or 0),
        })
    return rows


def _nets_and_counts(buckets: dict, today_ym: str, n_months: int = 6) -> tuple[list[float], list[int]]:
    """Extract eligible nets and transaction counts for the N most recent complete months."""
    active = [
        (ym, b) for ym, b in sorted(buckets.items())
        if ym < today_ym and float(b.get("real_credit") or b.get("gross") or 0) > 0
    ][-n_months:]
    nets   = [float(b.get("real_credit") or b.get("gross") or 0) for _, b in active]
    counts = [int(b.get("count") or 0) for _, b in active]
    return nets, counts


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def root():
    now = datetime.datetime.now(_TZ_LAGOS).isoformat()
    return {"service": "PARSIO Credit Intelligence API", "status": "ok", "server_time_WAT": now}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok"}


@app.post("/analyse", tags=["Analysis"])
async def analyse(
    pdf: Annotated[UploadFile, File(description="Bank statement PDF file")],
    _key: Annotated[str, Depends(verify_api_key)],
    pdf_password: Annotated[str, Form()] = "",
    location: Annotated[str, Form(description="'Core' or 'Expansion'")] = "Core",
    product_type: Annotated[str, Form(description="'NEW' or 'RENEWAL'")] = "NEW",
    tenor: Annotated[int, Form(description="Loan tenor in months (3–24)")] = 12,
    other_loans: Annotated[float, Form(description="Existing monthly loan obligations (NGN)")] = 0.0,
    requested_loan: Annotated[float, Form(description="Specific loan amount requested (0 = max)")] = 0.0,
    n_months: Annotated[int, Form(description="Number of months to analyse (3–12)")] = 6,
    applicant_name: Annotated[str, Form(description="Applicant name — stored in audit log")] = "",
):
    """
    Analyse a bank statement PDF and return a credit eligibility verdict.

    **Returns**
    - `approved` — whether the applicant qualifies
    - `max_loan` — maximum eligible loan amount (NGN)
    - `dti` — debt-to-income ratio (decimal, e.g. 0.28 = 28%)
    - `monthly_repayment` — repayment per period
    - `bank` — detected bank
    - `account_holder` — detected account holder name
    - `months_analysed` — number of complete months used
    - `monthly_breakdown` — per-month income figures
    - `requested` — analysis of requested loan amount (if provided)
    """
    # Validate inputs
    if location not in ("Core", "Expansion"):
        raise HTTPException(status_code=400, detail="location must be 'Core' or 'Expansion'")
    if product_type not in ("NEW", "RENEWAL", "SEL"):
        raise HTTPException(status_code=400, detail="product_type must be 'NEW', 'RENEWAL', or 'SEL'")
    if not (1 <= tenor <= 36):
        raise HTTPException(status_code=400, detail="tenor must be between 1 and 36 months")
    if not (1 <= n_months <= 12):
        raise HTTPException(status_code=400, detail="n_months must be between 1 and 12")
    if not pdf.filename or not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a PDF")

    # Read PDF bytes
    pdf_bytes = await pdf.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded PDF is empty")

    # Parse the statement
    try:
        buckets, summary, bank, account_holder, txns, debit_txns = bank_parser.parse_transactions(
            pdf_bytes, pdf_password, filename=pdf.filename
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Statement parse failed: {exc}") from exc

    if not buckets:
        raise HTTPException(status_code=422, detail="No transactions found in the statement.")

    today_ym = datetime.datetime.now(_TZ_LAGOS).strftime("%Y-%m")
    nets, counts = _nets_and_counts(buckets, today_ym, n_months)

    if not nets or all(n == 0 for n in nets):
        raise HTTPException(
            status_code=422,
            detail="No usable income months found in statement. "
                   "The statement may be too short or all months have zero eligible income.",
        )

    # Run eligibility engine
    sel_mode = (product_type == "SEL")
    result = calculate_eligibility(
        nets=nets,
        counts=counts,
        location=location,
        product_type=product_type if product_type != "SEL" else "NEW",
        tenor=tenor,
        other_loans=other_loans,
        requested_loan=requested_loan if requested_loan > 0 else 0,
        sel_mode=sel_mode,
    )

    monthly_breakdown = _build_monthly_breakdown(buckets, today_ym)

    # Build response
    response = {
        "approved":          result["approved"],
        "decision":          result["decision"],
        "max_loan":          round(result["max_loan"], 2),
        "dti":               round(result["dti"], 4),
        "dti_percent":       round(result["dti"] * 100, 2),
        "interest_rate":     round((result["interest_rate"] or 0), 6),
        "interest_rate_pct": round((result["interest_rate"] or 0) * 100, 4),
        "tenor_months":      result["tenor"],
        "repayment_frequency": result["repayment_frequency"],
        "monthly_repayment": round(result["max_repayment_monthly"], 2),
        "repayment_per_period": round(result["max_repayment_display"], 2),
        "total_net_income":  round(result["total_net"], 2),
        "applicable_turnover": round(result["applicable_turnover"], 2),
        "months_analysed":   len(nets),
        "bank":              bank or "Unknown",
        "account_holder":    account_holder or applicant_name or "Unknown",
        "turnover_capped":   result.get("turnover_capped", False),
        "monthly_breakdown": monthly_breakdown,
        "analysed_at_WAT":   datetime.datetime.now(_TZ_LAGOS).isoformat(),
    }

    if result.get("requested"):
        req = result["requested"]
        response["requested"] = {
            "amount":        round(req["amount"], 2),
            "within_max":    req["within_max"],
            "interest_rate": round(req["rate"] or 0, 6),
            "repayment_per_period": round(req["repayment"], 2),
            "dti":           round(req["dti"], 4),
            "dti_percent":   round(req["dti"] * 100, 2),
        }

    return response
