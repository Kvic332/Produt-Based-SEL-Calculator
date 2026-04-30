from __future__ import annotations
import pandas as pd
import streamlit as st
from parser import (
    monthly_analysis, parse_transactions, parse_firstcentral,
    ym_label, CreditAccount,
)
from sel_rules import calculate_eligibility

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SEL Loan Eligibility Calculator",
    page_icon="▶",
    layout="wide",
)

# ── Dark theme CSS matching HTML calculator ───────────────────────────────────
st.markdown("""
<style>
  /* Import fonts */
  @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Serif+Display:ital@0;1&display=swap');

  /* Root variables */
  :root {
    --bg: #0a0f1e; --surface: #111827; --surface2: #1a2235;
    --border: #1e3a5f; --accent: #00d4ff; --accent2: #ff6b35;
    --green: #00e676; --red: #ff4444; --text: #e2e8f0;
    --muted: #64748b; --gold: #ffd700; --orange: #ff9500;
  }

  /* Global */
  .stApp { background: var(--bg) !important; color: var(--text) !important; font-family: 'Space Mono', monospace !important; }
  .block-container { padding: 2rem 2rem 4rem !important; max-width: 1000px !important; }

  /* Headers */
  h1 { font-family: 'DM Serif Display', serif !important; color: #fff !important; }
  h1 em { color: var(--accent) !important; }
  h2, h3 { font-family: 'Space Mono', monospace !important; color: var(--accent) !important;
           font-size: 11px !important; letter-spacing: 3px !important; text-transform: uppercase !important; }

  /* Sections */
  .sel-section { background: var(--surface); border: 1px solid var(--border);
                 border-radius: 4px; padding: 24px; margin-bottom: 20px; }
  .sel-section-title { font-size: 11px; letter-spacing: 3px; color: var(--accent);
                       text-transform: uppercase; border-bottom: 1px solid var(--border);
                       padding-bottom: 10px; margin-bottom: 16px; }

  /* Metric cards */
  .sel-card { background: var(--surface2); border: 1px solid var(--border);
              border-radius: 3px; padding: 14px; }
  .sel-card.highlight { border-color: var(--accent); box-shadow: 0 0 20px rgba(0,212,255,.1); }
  .sel-label { font-size: 10px; letter-spacing: 2px; color: var(--muted);
               text-transform: uppercase; margin-bottom: 4px; }
  .sel-value { font-size: 20px; font-weight: 700; color: var(--accent); }
  .sel-value.green  { color: var(--green) !important; }
  .sel-value.gold   { color: var(--gold) !important; }
  .sel-value.red    { color: var(--red) !important; }
  .sel-value.orange { color: var(--orange) !important; }

  /* Banners */
  .banner-approved { background: rgba(0,230,118,.08); border: 1px solid rgba(0,230,118,.3);
                     color: var(--green); padding: 14px 18px; border-radius: 3px;
                     font-size: 14px; letter-spacing: 1px; }
  .banner-rejected { background: rgba(255,68,68,.08); border: 1px solid rgba(255,68,68,.3);
                     color: var(--red); padding: 14px 18px; border-radius: 3px;
                     font-size: 14px; letter-spacing: 1px; }
  .banner-info     { background: rgba(0,212,255,.05); border: 1px solid rgba(0,212,255,.2);
                     color: var(--accent); padding: 12px 16px; border-radius: 3px;
                     font-size: 12px; }
  .banner-bad      { background: rgba(255,68,68,.08); border: 1px solid rgba(255,68,68,.25);
                     color: var(--red); padding: 12px 16px; border-radius: 3px;
                     font-size: 12px; }
  .banner-good     { background: rgba(0,230,118,.08); border: 1px solid rgba(0,230,118,.25);
                     color: var(--green); padding: 12px 16px; border-radius: 3px;
                     font-size: 12px; }

  /* Tags / badges */
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px;
           font-size: 9px; letter-spacing: 1px; text-transform: uppercase; }
  .badge-blue   { background: rgba(0,212,255,.1); color: var(--accent); border: 1px solid rgba(0,212,255,.2); }
  .badge-red    { background: rgba(255,68,68,.1);  color: var(--red);    border: 1px solid rgba(255,68,68,.25); }
  .badge-orange { background: rgba(255,149,0,.1);  color: var(--orange); border: 1px solid rgba(255,149,0,.25); }
  .badge-green  { background: rgba(0,230,118,.1);  color: var(--green);  border: 1px solid rgba(0,230,118,.25); }

  /* Tables */
  .preview-table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }
  .preview-table th { font-size: 9px; letter-spacing: 1px; color: var(--muted);
                      text-transform: uppercase; padding: 6px 10px;
                      border-bottom: 1px solid var(--border); text-align: right; }
  .preview-table th:first-child { text-align: left; }
  .preview-table td { padding: 6px 10px; border-bottom: 1px solid rgba(30,58,95,.3);
                      text-align: right; }
  .preview-table td:first-child { text-align: left; color: var(--accent); font-weight: 700; }
  .col-gross  { color: var(--green); }
  .col-self   { color: var(--orange); }
  .col-rev    { color: #e040fb; }
  .col-nonbiz { color: var(--muted); }
  .col-loan   { color: var(--gold); }
  .col-net    { color: var(--accent); font-weight: 700; }

  /* Credit table */
  .credit-table { width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 10px; }
  .credit-table th { font-size: 9px; letter-spacing: 1px; color: var(--muted);
                     text-transform: uppercase; padding: 8px;
                     border-bottom: 1px solid var(--border); text-align: left; }
  .credit-table td { padding: 8px; border-bottom: 1px solid rgba(30,58,95,.3); vertical-align: top; }
  .credit-table tfoot td { border-top: 1px solid var(--border); font-weight: 700; }

  /* Sidebar */
  [data-testid="stSidebar"] { background: var(--surface) !important;
                               border-right: 1px solid var(--border) !important; }
  [data-testid="stSidebar"] label { color: var(--muted) !important; font-size: 10px !important;
                                    letter-spacing: 1px !important; text-transform: uppercase !important; }

  /* Inputs */
  input, select, textarea, [data-testid="stTextInput"] input,
  [data-testid="stNumberInput"] input, [data-testid="stSelectbox"] select {
    background: var(--surface2) !important; border: 1px solid var(--border) !important;
    color: var(--text) !important; font-family: 'Space Mono', monospace !important;
  }

  /* Buttons */
  .stButton button { background: transparent !important; border: 1px solid var(--accent) !important;
                     color: var(--accent) !important; font-family: 'Space Mono', monospace !important;
                     letter-spacing: 2px !important; text-transform: uppercase !important; }
  .stButton button:hover { background: rgba(0,212,255,.08) !important; }

  /* File uploader */
  [data-testid="stFileUploader"] { background: var(--surface2) !important;
                                    border: 2px dashed var(--border) !important;
                                    border-radius: 4px !important; }

  /* Dataframe */
  .stDataFrame { background: var(--surface2) !important; }

  /* Metric */
  [data-testid="stMetric"] { background: var(--surface2) !important;
                              border: 1px solid var(--border) !important;
                              border-radius: 3px !important; padding: 12px !important; }
  [data-testid="stMetricLabel"] { color: var(--muted) !important; font-size: 10px !important;
                                   letter-spacing: 2px !important; text-transform: uppercase !important; }
  [data-testid="stMetricValue"] { color: var(--accent) !important; font-size: 20px !important; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────
def money(v: float) -> str:
    return f"₦{v:,.0f}"

def pct(v) -> str:
    return "--" if v is None else f"{v * 100:.2f}%"

def card(label: str, value: str, cls: str = "") -> str:
    return (f'<div class="sel-card{"highlight" if cls=="_h" else ""}" style="margin-bottom:8px">'
            f'<div class="sel-label">{label}</div>'
            f'<div class="sel-value {cls}">{value}</div></div>')

def section(title: str) -> str:
    return f'<div class="sel-section-title">{title}</div>'


# ── Session state init ────────────────────────────────────────────────────────
for key in ["buckets_a","summary_a","bank_a","name_a",
            "buckets_b","summary_b","bank_b","name_b",
            "credit_data","rows_a","rows_b"]:
    if key not in st.session_state:
        st.session_state[key] = None


# ════════════════════════════════════════════════════════════════════════════
# HEADER
# ════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div style="border-bottom:1px solid #1e3a5f;padding-bottom:24px;margin-bottom:32px">
  <div style="font-size:10px;letter-spacing:4px;color:#00d4ff;text-transform:uppercase;margin-bottom:8px">▶ SEL Financial Toolkit</div>
  <h1 style="font-family:DM Serif Display,serif;font-size:clamp(28px,4vw,44px);color:#fff;line-height:1.1">
    Loan <em style="color:#00d4ff;font-style:italic">Eligibility</em><br>Calculator
  </h1>
  <div style="font-size:11px;color:#64748b;margin-top:6px">
    All Products &nbsp;|&nbsp; Auto-computes DTI, Repayment, Turnover &amp; Loan Amount &nbsp;|&nbsp; Recycling Detection
  </div>
</div>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 00 — FIRST BANK STATEMENT
# ════════════════════════════════════════════════════════════════════════════
st.markdown('<div class="sel-section-title">00 — Bank Statement Auto-Fill &nbsp;<span style="color:#64748b;font-size:10px">— Optional</span></div>', unsafe_allow_html=True)
st.caption("Upload PDF bank statement. Credits are automatically classified into real income vs recycled amounts.")

col1, col2 = st.columns(2)
with col1:
    file_a = st.file_uploader("Upload Bank Statement (PDF)", type=["pdf"], key="upload_a")
with col2:
    pw_a   = st.text_input("PDF Password", type="password", key="pw_a", placeholder="Leave blank if not encrypted")
    if st.button("Extract Monthly Credits", key="btn_extract_a"):
        if not file_a:
            st.error("Please select a PDF file first.")
        else:
            with st.spinner("Extracting..."):
                try:
                    buckets, summary, bank, name = parse_transactions(file_a.getvalue(), pw_a)
                    rows = monthly_analysis(buckets, summary)
                    st.session_state.buckets_a = buckets
                    st.session_state.summary_a = summary
                    st.session_state.bank_a    = bank
                    st.session_state.name_a    = name
                    st.session_state.rows_a    = rows
                    st.success(f"Extracted from {bank} statement — {name or 'account holder'}")
                except Exception as e:
                    st.error(f"Error: {e}")

# Show breakdown table for statement A
if st.session_state.rows_a:
    rows_a = [r for r in st.session_state.rows_a
              if r["ym"] < __import__("datetime").date.today().strftime("%Y-%m")
              and r["gross"] > 0][-6:]
    if rows_a:
        has_self  = any(r["self_transfer"]  > 0 for r in rows_a)
        has_rev   = any(r["reversal"]       > 0 for r in rows_a)
        has_nb    = any(r["non_business"]   > 0 for r in rows_a)
        has_loan  = any(r["loan_disbursal"] > 0 for r in rows_a)

        hdr = ('<tr>'
               '<th class="col-gross" style="text-align:left">Month</th>'
               '<th class="col-gross">Total Inflow</th>')
        if has_self:  hdr += '<th class="col-self">Internal Movements</th>'
        if has_rev:   hdr += '<th class="col-rev">Reversals</th>'
        if has_nb:    hdr += '<th class="col-nonbiz">Non-Business</th>'
        if has_loan:  hdr += '<th class="col-loan">Loan Disbursals</th>'
        hdr += '<th class="col-net">Eligible Income</th></tr>'

        body = ""
        for r in rows_a:
            body += (f'<tr><td>{r["label"]}</td>'
                     f'<td class="col-gross">{money(r["gross"])}</td>')
            if has_self:  body += f'<td class="col-self">{("-"+money(r["self_transfer"])) if r["self_transfer"] > 0 else "—"}</td>'
            if has_rev:   body += f'<td class="col-rev">{("-"+money(r["reversal"])) if r["reversal"] > 0 else "—"}</td>'
            if has_nb:    body += f'<td class="col-nonbiz">{("-"+money(r["non_business"])) if r["non_business"] > 0 else "—"}</td>'
            if has_loan:  body += f'<td class="col-loan">{("-"+money(r["loan_disbursal"])) if r["loan_disbursal"] > 0 else "—"}</td>'
            body += f'<td class="col-net">{money(r["eligible_income"])}</td></tr>'

        st.markdown(
            f'<table class="preview-table"><thead>{hdr}</thead><tbody>{body}</tbody></table>',
            unsafe_allow_html=True,
        )


# ════════════════════════════════════════════════════════════════════════════
# SECTION 00B — SECOND BANK STATEMENT
# ════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown('<div class="sel-section-title">00B — Second Bank Statement &nbsp;<span style="color:#64748b;font-size:10px">— Optional</span></div>', unsafe_allow_html=True)
st.caption("Upload a second bank statement. Nets are merged month-by-month. Only months present in **both** statements are used.")

col3, col4 = st.columns(2)
with col3:
    file_b = st.file_uploader("Upload Second Bank Statement (PDF)", type=["pdf"], key="upload_b")
with col4:
    pw_b   = st.text_input("PDF Password", type="password", key="pw_b", placeholder="Leave blank if not encrypted")
    if st.button("Extract & Merge with First Statement", key="btn_extract_b"):
        if not file_b:
            st.error("Please select a second PDF file first.")
        elif not st.session_state.buckets_a:
            st.error("Please extract the first statement first.")
        else:
            with st.spinner("Extracting second statement..."):
                try:
                    buckets_b, summary_b, bank_b, name_b = parse_transactions(file_b.getvalue(), pw_b)
                    rows_b = monthly_analysis(buckets_b, summary_b)
                    st.session_state.buckets_b = buckets_b
                    st.session_state.summary_b = summary_b
                    st.session_state.bank_b    = bank_b
                    st.session_state.name_b    = name_b
                    st.session_state.rows_b    = rows_b
                    st.success(f"Second statement extracted: {bank_b} — {name_b or 'account holder'}")
                except Exception as e:
                    st.error(f"Error: {e}")

# Show merged preview
if st.session_state.rows_a and st.session_state.rows_b:
    import datetime
    today = datetime.date.today().strftime("%Y-%m")
    rows_a_map = {r["ym"]: r for r in st.session_state.rows_a if r["ym"] < today and r["gross"] > 0}
    rows_b_map = {r["ym"]: r for r in st.session_state.rows_b if r["ym"] < today and r["gross"] > 0}
    common = sorted(set(rows_a_map) & set(rows_b_map))[-6:]

    if common:
        st.markdown('<div style="font-size:10px;letter-spacing:2px;color:#00e676;text-transform:uppercase;margin:12px 0 6px">▷ Merged Result (Intersection of Both Statements)</div>', unsafe_allow_html=True)
        html = '<table class="preview-table"><thead><tr><th style="text-align:left">Month</th><th class="col-gross">Statement 1 Net</th><th style="text-align:right;color:#ff6b35">Statement 2 Net</th><th class="col-net">Combined Net</th></tr></thead><tbody>'
        tA = tB = tC = 0
        for ym in common:
            rA = rows_a_map[ym]
            rB = rows_b_map[ym]
            netA = rA["eligible_income"]
            netB = rB["eligible_income"]
            combined = netA + netB
            tA += netA; tB += netB; tC += combined
            html += (f'<tr><td>{ym_label(ym)}</td>'
                     f'<td class="col-gross">{money(netA)}</td>'
                     f'<td style="text-align:right;color:#ff6b35">{money(netB)}</td>'
                     f'<td class="col-net">{money(combined)}</td></tr>')
        html += (f'</tbody><tfoot><tr>'
                 f'<td style="color:#64748b;font-size:10px;text-transform:uppercase">Total</td>'
                 f'<td class="col-gross">{money(tA)}</td>'
                 f'<td style="text-align:right;color:#ff6b35">{money(tB)}</td>'
                 f'<td class="col-net">{money(tC)}</td>'
                 f'</tr></tfoot></table>')
        html += f'<div style="font-size:11px;color:#64748b;margin-top:6px">ℹ Showing {len(common)} overlapping months.</div>'
        st.markdown(html, unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 01 — FIRSTCENTRAL CREDIT REPORT
# ════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown('<div class="sel-section-title">01 — FirstCentral Credit Report &nbsp;<span style="color:#64748b;font-size:10px">— External obligations</span></div>', unsafe_allow_html=True)
st.caption("Upload the FirstCentral consumer credit report. Closed accounts are ignored. Open performing accounts feed into monthly obligations.")

col5, col6 = st.columns(2)
with col5:
    credit_file = st.file_uploader("Upload FirstCentral Report (PDF)", type=["pdf"], key="credit_upload")
with col6:
    credit_pw = st.text_input("Credit Report Password", type="password", key="credit_pw", placeholder="Leave blank if not encrypted")
    if st.button("Extract External Obligations", key="btn_credit"):
        if not credit_file:
            st.error("Please select a FirstCentral PDF first.")
        else:
            with st.spinner("Parsing credit report..."):
                try:
                    data = parse_firstcentral(credit_file.getvalue(), credit_pw)
                    st.session_state.credit_data = data
                    total = data["total_monthly_obligation"]
                    bad   = len(data["bad_credit_accounts"])
                    st.success(f"Extracted {len(data['records'])} active accounts — Monthly obligations: {money(total)}")
                    if bad > 0:
                        st.markdown(f'<div class="banner-bad">⚠ Bad credit flag: {bad} account{"s" if bad>1 else ""} marked lost, derogatory, or delinquent with outstanding balance above ₦10,000.</div>', unsafe_allow_html=True)
                    else:
                        st.markdown('<div class="banner-good">✓ No bad-credit triggers found in the active FirstCentral accounts.</div>', unsafe_allow_html=True)
                except Exception as e:
                    st.error(f"Error: {e}")

# Show credit table
if st.session_state.credit_data:
    data = st.session_state.credit_data
    records = data["records"]
    total = data["total_monthly_obligation"]
    if records:
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("External Monthly Obligations", money(total))
        col_m2.metric("Active Accounts", len(records))
        col_m3.metric("Bad Credit Flags", len(data["bad_credit_accounts"]))

        html = """<table class="credit-table"><thead><tr>
            <th>Subscriber</th><th>Account Status</th><th>Facility Classification</th>
            <th>Instalment</th><th>Outstanding</th><th>Tenor</th>
            <th>Monthly Obligation</th><th>Rule</th>
        </tr></thead><tbody>"""
        for r in records:
            sts_cls  = "badge-red"    if r.is_bad_credit else "badge-blue"
            cls_cls  = "badge-orange" if r.facility_classification.lower() != "performing" else "badge-green"
            rule = ("Flagged as bad credit" if r.is_bad_credit
                    else "Derived from balance / tenor" if r.derived_from_balance
                    else "Instalment amount used" if r.include_in_obligation
                    else "Monitoring only")
            mo_str = money(r.monthly_obligation) if r.include_in_obligation else "—"
            tenor_str = f"{r.tenor_months} mo" if r.tenor_months else r.loan_duration_days
            html += (f'<tr>'
                     f'<td>{r.subscriber_name}<div style="color:#64748b;margin-top:3px;font-size:10px">{r.account_number}</div></td>'
                     f'<td><span class="badge {sts_cls}">{r.account_status}</span></td>'
                     f'<td><span class="badge {cls_cls}">{r.facility_classification}</span></td>'
                     f'<td>{money(r.instalment_amount)}</td>'
                     f'<td>{money(r.outstanding_balance)}</td>'
                     f'<td>{tenor_str}</td>'
                     f'<td style="color:#ffd700;font-weight:700">{mo_str}</td>'
                     f'<td style="color:#64748b;font-size:10px">{rule}</td>'
                     f'</tr>')
        html += (f'</tbody><tfoot><tr>'
                 f'<td colspan="6" style="color:#64748b">Total monthly external obligations</td>'
                 f'<td style="color:#ffd700;font-weight:700">{money(total)}</td>'
                 f'<td>{len(data["bad_credit_accounts"])} bad flag(s)' if data["bad_credit_accounts"] else '<td>Clear'
                 + '</td></tr></tfoot></table>')
        st.markdown(html, unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 02 — MONTHLY INFLOWS (editable)
# ════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown('<div class="sel-section-title">02 — Monthly Inflows (Last 6 Months)</div>', unsafe_allow_html=True)
st.caption("Gross credits, recycling deductions, and net inflows per month.")

import datetime
today = datetime.date.today()

# Determine which rows to pre-fill
def get_prefill_rows():
    """Get last 6 completed months from merged or single statement."""
    today_ym = today.strftime("%Y-%m")
    if st.session_state.rows_a and st.session_state.rows_b:
        rows_a_map = {r["ym"]: r for r in st.session_state.rows_a if r["ym"] < today_ym and r["gross"] > 0}
        rows_b_map = {r["ym"]: r for r in st.session_state.rows_b if r["ym"] < today_ym and r["gross"] > 0}
        common = sorted(set(rows_a_map) & set(rows_b_map))[-6:]
        if common:
            merged = []
            for ym in common:
                rA = rows_a_map[ym]
                rB = rows_b_map[ym]
                merged.append({
                    "ym": ym, "label": ym_label(ym),
                    "gross": rA["eligible_income"] + rB["eligible_income"],
                    "deductions": 0, "count": max(rA["count"], rB["count"]),
                })
            return merged
    elif st.session_state.rows_a:
        return [r for r in st.session_state.rows_a if r["ym"] < today_ym and r["gross"] > 0][-6:]
    return None

prefill = get_prefill_rows()

# Build default month labels
def default_months():
    months = []
    for i in range(1, 7):
        d = datetime.date(today.year, today.month, 1)
        # go back (7-i) months
        month = today.month - (7 - i)
        year  = today.year
        while month <= 0:
            month += 12
            year  -= 1
        months.append((f"{year}-{str(month).zfill(2)}", ym_label(f"{year}-{str(month).zfill(2)}")))
    return months

default_m = default_months()
inflow_data = []

for i in range(6):
    if prefill and i < len(prefill):
        r    = prefill[i]
        label= r["label"]
        gross= r.get("gross", 0)
        deduct= r.get("deductions", 0)
        count = r.get("count", 12)
    else:
        label = default_m[i][1]
        gross = 0.0
        deduct= 0.0
        count = 12

    c1, c2, c3, c4, c5 = st.columns([1.2, 2, 2, 1.5, 0.8])
    with c1: st.markdown(f"**{label}**")
    with c2: g = st.number_input("Gross Credit (₦)", min_value=0.0, value=float(gross),  step=1000.0, key=f"gross_{i}", label_visibility="collapsed")
    with c3: d = st.number_input("Deductions (₦)",   min_value=0.0, value=float(deduct), step=1000.0, key=f"deduct_{i}", label_visibility="collapsed")
    with c4:
        net = max(g - d, 0)
        st.markdown(f'<div style="color:#00e676;font-weight:700;padding-top:8px">{money(net)}</div>', unsafe_allow_html=True)
    with c5: cnt = st.number_input("Count", min_value=1, max_value=9999, value=max(1, int(count)), key=f"count_{i}", label_visibility="collapsed")
    inflow_data.append({"label": label, "gross": g, "deduct": d, "net": net, "count": cnt})

if i == 0:
    colh1, colh2, colh3, colh4, colh5 = st.columns([1.2, 2, 2, 1.5, 0.8])
    with colh1: st.caption("Month")
    with colh2: st.caption("Gross Credit (₦)")
    with colh3: st.caption("Deductions (₦)")
    with colh4: st.caption("Net Credit")
    with colh5: st.caption("Count")


# ════════════════════════════════════════════════════════════════════════════
# SECTION 03 — LOAN PARAMETERS
# ════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown('<div class="sel-section-title">03 — Loan Parameters</div>', unsafe_allow_html=True)

p1, p2, p3, p4 = st.columns(4)
with p1: location  = st.selectbox("Location", ["Lagos","Outside Lagos","Expansion"])
with p2: prod_type = st.selectbox("Product Type", ["NTB","RENEWAL","TOP-UP"])
with p3: tenor     = st.selectbox("Tenor (Months)", list(range(2,13)), index=4)
with p4:
    default_other = 0.0
    if st.session_state.credit_data:
        default_other = st.session_state.credit_data["total_monthly_obligation"]
    other_loans = st.number_input("Other Monthly Loan Repayments (₦)", min_value=0.0,
                                   value=float(default_other), step=1000.0)

r1, r2 = st.columns(2)
with r1: req_loan   = st.number_input("Requested Loan Amount (₦) — Optional", min_value=0.0, value=0.0, step=10000.0)
with r2: manual_rate= st.number_input("Manual Interest Rate (%) — Optional Override", min_value=0.0, value=0.0, step=0.01,
                                       help="If entered, overrides the rate grid. Leave at 0 to use rate grid.")

calc_btn = st.button("▶   Calculate Eligibility", key="calc", use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 04 — RESULTS
# ════════════════════════════════════════════════════════════════════════════
if calc_btn:
    nets   = [r["net"]   for r in inflow_data]
    counts = [r["count"] for r in inflow_data]

    if all(n == 0 for n in nets):
        st.error("Please enter monthly inflow data before calculating.")
    else:
        result = calculate_eligibility(
            nets=nets, counts=counts,
            location=location, product_type=prod_type,
            tenor=tenor, other_loans=other_loans,
            requested_loan=req_loan if req_loan > 0 else 0,
            manual_rate_percent=manual_rate if manual_rate > 0 else None,
        )

        st.markdown("---")
        st.markdown('<div class="sel-section-title">04 — Results</div>', unsafe_allow_html=True)

        approved = result.get("approved", False)
        loan     = result["max_loan"]
        decision = "✅ Max loan amount" if approved else "❌ Below product minimum"

        banner_cls = "banner-approved" if approved else "banner-rejected"
        st.markdown(f'<div class="{banner_cls}">{decision}</div>', unsafe_allow_html=True)
        st.markdown("")

        # Result cards
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("New Loan Amount",        money(loan),                   delta=None)
        m2.metric("Applicable Turnover",    money(result["applicable_turnover"]))
        m3.metric("Total Eligible Net",     money(result["total_net"]))
        m4.metric("DTI",                    pct(result["dti"]))

        m5, m6, m7, m8 = st.columns(4)
        rate_label = (f"{pct(result['interest_rate'])} ★" if manual_rate > 0
                      else pct(result["interest_rate"]))
        m5.metric("Interest Rate",          rate_label)
        m6.metric("Repayment Frequency",    result["repayment_frequency"])
        m7.metric("Max Repayment / Period", money(result["max_repayment_display"]))
        m8.metric("Max Total Repayment",    money(result["max_total_repayment"]))

        # Requested loan analysis
        if req_loan > 0 and "requested" in result:
            st.markdown("---")
            st.markdown('<div style="font-size:10px;letter-spacing:2px;color:#ff6b35;text-transform:uppercase;margin-bottom:8px">Requested Loan Analysis</div>', unsafe_allow_html=True)
            req = result["requested"]
            within = req["within_max"]
            r_col1, r_col2 = st.columns(2)
            with r_col1:
                st.markdown(f"""
                | | |
                |---|---|
                | Requested Amount | {money(req_loan)} |
                | Interest Rate | {pct(req.get("rate"))} |
                | Repayment / Period | {money(req["repayment"])} |
                | DTI for Requested | {pct(req["dti"])} |
                | vs Max Loan | {("+" if req_loan >= loan else "") + money(abs(req_loan - loan))} |
                | Status | {"✅ Below max — eligible" if within else "❌ Above max — not eligible"} |
                """)

        # Deduction audit table
        if st.session_state.rows_a or st.session_state.rows_b:
            st.markdown("---")
            st.markdown('<div style="font-size:10px;letter-spacing:2px;color:#64748b;text-transform:uppercase;margin-bottom:6px">Deduction Audit — All Classified Credits</div>', unsafe_allow_html=True)

            audit_rows = []
            src_rows = (st.session_state.rows_a or [])
            today_ym = today.strftime("%Y-%m")
            for r in src_rows:
                if r["ym"] >= today_ym or r["gross"] == 0:
                    continue
                for cat in ["self_transfer","reversal","non_business","loan_disbursal"]:
                    if r.get(cat, 0) > 0:
                        audit_rows.append({
                            "Month": r["label"],
                            "Category": cat.replace("_", " ").title(),
                            "Amount": r[cat],
                        })

            if audit_rows:
                df = pd.DataFrame(audit_rows)
                st.dataframe(
                    df, hide_index=True, use_container_width=True,
                    column_config={"Amount": st.column_config.NumberColumn("Amount", format="₦%d")},
                )
                st.download_button(
                    "Download Deduction Audit CSV",
                    df.to_csv(index=False).encode("utf-8"),
                    file_name="sel_deduction_audit.csv",
                    mime="text/csv",
                )