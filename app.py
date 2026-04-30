from __future__ import annotations

import pandas as pd
import streamlit as st

from parser import monthly_analysis, parse_transactions
from sel_rules import calculate_eligibility, ym_label


st.set_page_config(page_title="SEL Loan Calculator", layout="wide")


def money(value: float) -> str:
    return f"₦{value:,.0f}"


def pct(value: float | None) -> str:
    return "--" if value is None else f"{value * 100:.2f}%"


st.title("SEL Loan Calculator")
st.caption("Python parser with auditable deduction rules")

with st.sidebar:
    st.header("Statement")
    uploaded = st.file_uploader("Upload bank statement PDF", type=["pdf"])
    password = st.text_input("PDF password", type="password")

    st.header("Loan parameters")
    location = st.selectbox("Location", ["Lagos", "Outside Lagos", "Expansion"])
    product_type = st.selectbox("Product type", ["NTB", "RENEWAL", "TOP-UP"])
    tenor = st.selectbox("Tenor", list(range(2, 13)), index=4)
    other_loans = st.number_input("Other monthly loan repayments", min_value=0.0, value=0.0, step=1000.0)
    requested_loan = st.number_input("Requested loan amount (optional)", min_value=0.0, value=0.0, step=10000.0)
    manual_rate = st.number_input("Manual interest rate % (optional)", min_value=0.0, value=0.0, step=0.1)


if not uploaded:
    st.info("Upload a password-protected or open PDF bank statement to begin.")
    st.stop()

try:
    transactions, summary_credits = parse_transactions(uploaded.getvalue(), password)
except Exception as exc:
    st.error(str(exc))
    st.stop()

monthly_rows = monthly_analysis(transactions, summary_credits)
completed_rows = [row for row in monthly_rows if row["ym"] < "2026-04" and row["gross"] > 0]
selected_rows = completed_rows[-6:]

if not selected_rows:
    st.error("No completed monthly credit buckets found.")
    st.stop()

st.success(f"Extracted {len(selected_rows)} completed months from statement")

summary_match = all(abs(row["gross"] - row["parsed_gross"]) < 0.05 for row in monthly_rows if row["parsed_gross"])
gross_total = sum(row["gross"] for row in monthly_rows)
parsed_total = sum(row["parsed_gross"] for row in monthly_rows)

col1, col2, col3 = st.columns(3)
col1.metric("Summary match", "Yes" if summary_match else "Check")
col2.metric("Statement gross credits", money(gross_total))
col3.metric("Parsed gross credits", money(parsed_total))

display_rows = []
for row in selected_rows:
    display_rows.append(
        {
            "Month": ym_label(row["ym"]),
            "Total inflow": row["gross"],
            "Self/Internal": row["self_transfer"],
            "Reversal": row["reversal"],
            "Non-business": row["non_business"],
            "Loan disbursal": row["loan_disbursal"],
            "Eligible income": row["eligible_income"],
            "Credit count": row["count"],
        }
    )

st.subheader("Monthly credits breakdown")
monthly_df = pd.DataFrame(display_rows)
st.dataframe(
    monthly_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        c: st.column_config.NumberColumn(c, format="₦%d")
        for c in ["Total inflow", "Self/Internal", "Reversal", "Non-business", "Loan disbursal", "Eligible income"]
    },
)

nets = [row["eligible_income"] for row in selected_rows]
counts = [row["count"] for row in selected_rows]
result = calculate_eligibility(
    nets=nets,
    counts=counts,
    location=location,
    product_type=product_type,
    tenor=tenor,
    other_loans=other_loans,
    requested_loan=requested_loan,
    manual_rate_percent=manual_rate or None,
)

st.subheader("SEL result")
r1, r2, r3, r4 = st.columns(4)
r1.metric("Maximum loan", money(result["max_loan"]))
r2.metric("Applicable turnover", money(result["applicable_turnover"]))
r3.metric("Total eligible net inflow", money(result["total_net"]))
r4.metric("DTI", pct(result["dti"]))

r5, r6, r7, r8 = st.columns(4)
r5.metric("Rate", pct(result["interest_rate"]))
r6.metric("Frequency", result["repayment_frequency"])
r7.metric("Repayment", money(result["max_repayment_display"]))
r8.metric("Max total repayment", money(result["max_total_repayment"]))

deducted = [tx for tx in transactions if tx.kind == "credit" and tx.category in {"self_transfer", "reversal", "non_business", "loan_disbursal"}]
deducted_rows = [
    {
        "Page": tx.page,
        "Date": tx.tran_date,
        "Month": ym_label(tx.ym),
        "Amount": tx.amount,
        "Category": tx.category,
        "Reason": tx.reason,
        "Narration": tx.narration,
    }
    for tx in deducted
    if tx.ym in {row["ym"] for row in selected_rows}
]

st.subheader("Deduction audit")
st.caption("Every deducted credit used in the selected six-month result.")
audit_df = pd.DataFrame(deducted_rows)
st.dataframe(
    audit_df,
    use_container_width=True,
    hide_index=True,
    column_config={"Amount": st.column_config.NumberColumn("Amount", format="₦%d")},
)

st.download_button(
    "Download deduction audit CSV",
    audit_df.to_csv(index=False).encode("utf-8"),
    file_name="sel_deduction_audit.csv",
    mime="text/csv",
)
