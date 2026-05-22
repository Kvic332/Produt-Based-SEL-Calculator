from __future__ import annotations
import pandas as pd
import streamlit as st
from parser import (
    monthly_analysis, parse_transactions, parse_firstcentral,
    ym_label, CreditAccount,
    extract_stated_totals, verify_extraction_accuracy,
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

  /* Root variables — Emerald + Gold palette */
  :root {
    --bg: #090e0c; --surface: #0f1a15; --surface2: #162019;
    --border: #1a3d2b; --accent: #10b981; --accent2: #f59e0b;
    --green: #34d399; --red: #f87171; --text: #e2e8f0;
    --muted: #6b7f74; --gold: #fbbf24; --orange: #fb923c;
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
  .sel-card.highlight { border-color: var(--accent); box-shadow: 0 0 20px rgba(16,185,129,.1); }
  .sel-label { font-size: 10px; letter-spacing: 2px; color: var(--muted);
               text-transform: uppercase; margin-bottom: 4px; }
  .sel-value { font-size: 20px; font-weight: 700; color: var(--accent); }
  .sel-value.green  { color: var(--green) !important; }
  .sel-value.gold   { color: var(--gold) !important; }
  .sel-value.red    { color: var(--red) !important; }
  .sel-value.orange { color: var(--orange) !important; }

  /* Banners */
  .banner-approved { background: rgba(52,211,153,.08); border: 1px solid rgba(52,211,153,.3);
                     color: var(--green); padding: 14px 18px; border-radius: 3px;
                     font-size: 14px; letter-spacing: 1px; }
  .banner-rejected { background: rgba(248,113,113,.08); border: 1px solid rgba(248,113,113,.3);
                     color: var(--red); padding: 14px 18px; border-radius: 3px;
                     font-size: 14px; letter-spacing: 1px; }
  .banner-info     { background: rgba(16,185,129,.05); border: 1px solid rgba(16,185,129,.2);
                     color: var(--accent); padding: 12px 16px; border-radius: 3px;
                     font-size: 12px; }
  .banner-bad      { background: rgba(248,113,113,.08); border: 1px solid rgba(248,113,113,.25);
                     color: var(--red); padding: 12px 16px; border-radius: 3px;
                     font-size: 12px; }
  .banner-good     { background: rgba(52,211,153,.08); border: 1px solid rgba(52,211,153,.25);
                     color: var(--green); padding: 12px 16px; border-radius: 3px;
                     font-size: 12px; }

  /* Tags / badges */
  .badge { display: inline-block; padding: 2px 8px; border-radius: 999px;
           font-size: 9px; letter-spacing: 1px; text-transform: uppercase; }
  .badge-blue   { background: rgba(16,185,129,.1); color: var(--accent); border: 1px solid rgba(16,185,129,.2); }
  .badge-red    { background: rgba(248,113,113,.1);  color: var(--red);    border: 1px solid rgba(248,113,113,.25); }
  .badge-orange { background: rgba(251,146,60,.1);  color: var(--orange); border: 1px solid rgba(251,146,60,.25); }
  .badge-green  { background: rgba(52,211,153,.1);  color: var(--green);  border: 1px solid rgba(52,211,153,.25); }

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
  .col-rev    { color: #a78bfa; }
  .col-nonbiz { color: var(--muted); }
  .col-loan   { color: var(--gold); }
  .col-net    { color: var(--accent); font-weight: 700; }

  /* Credit table */
  .credit-table { width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 10px; }
  .credit-table th { font-size: 9px; letter-spacing: 1px; color: var(--muted);
                     text-transform: uppercase; padding: 8px;
                     border-bottom: 1px solid var(--border); text-align: left; }
  .credit-table td { padding: 8px; border-bottom: 1px solid rgba(26,61,43,.4); vertical-align: top; }
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
  .stButton button:hover { background: rgba(16,185,129,.08) !important; }

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

  /* Divider */
  hr { border: none !important; border-top: 1px solid var(--border) !important; margin: 28px 0 !important; }

  /* Caption */
  [data-testid="stCaptionContainer"] { color: var(--muted) !important; font-size: 11px !important; }

  /* Search input highlight */
  [data-testid="stTextInput"] input:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 2px rgba(16,185,129,.15) !important;
  }

  /* Number input */
  [data-testid="stNumberInput"] input:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 2px rgba(16,185,129,.1) !important;
  }

  /* Spinner */
  .stSpinner > div { border-top-color: var(--accent) !important; }

  /* Success / error messages */
  [data-testid="stAlert"] { font-family: 'Space Mono', monospace !important; font-size: 12px !important; }

  /* Column gap tightening for inflow grid */
  .inflow-grid-header { font-size: 9px; letter-spacing: 2px; color: var(--muted);
                         text-transform: uppercase; padding-bottom: 4px; }

  /* Download button */
  [data-testid="stDownloadButton"] button {
    background: transparent !important;
    border: 1px solid var(--muted) !important;
    color: var(--muted) !important;
    font-size: 10px !important;
    letter-spacing: 1px !important;
  }
  [data-testid="stDownloadButton"] button:hover {
    border-color: var(--accent) !important;
    color: var(--accent) !important;
  }
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
            "credit_data","rows_a","rows_b","txns_a","txns_b"]:
    if key not in st.session_state:
        st.session_state[key] = None


# ════════════════════════════════════════════════════════════════════════════
# HEADER
# ════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div style="border-bottom:1px solid #1a3d2b;padding-bottom:24px;margin-bottom:32px">
  <div style="font-size:10px;letter-spacing:4px;color:#10b981;text-transform:uppercase;margin-bottom:8px">▶ SEL Financial Toolkit</div>
  <h1 style="font-family:DM Serif Display,serif;font-size:clamp(28px,4vw,44px);color:#fff;line-height:1.1">
    Loan <em style="color:#10b981;font-style:italic">Eligibility</em><br>Calculator
  </h1>
  <div style="font-size:11px;color:#64748b;margin-top:6px">
    All Products &nbsp;|&nbsp; Auto-computes DTI, Repayment, Turnover &amp; Loan Amount &nbsp;|&nbsp; Recycling Detection
  </div>
</div>
""", unsafe_allow_html=True)


# ── Trademark badge — floats in every 5 minutes ───────────────────────────────
import streamlit.components.v1 as _components
_components.html("""
<script>
(function() {
  var p = window.parent;
  if (p.__kvicBadgeInit) return;
  p.__kvicBadgeInit = true;

  /* Create badge */
  var b = p.document.createElement('div');
  b.id = 'kvic-tm';
  b.innerHTML =
    '<span style="font-size:9px;letter-spacing:3px;color:#6b7f74;text-transform:uppercase;display:block;margin-bottom:3px">Powered by</span>' +
    '<span style="font-size:13px;font-weight:700;color:#10b981;letter-spacing:1px">Kenechukwu Kvic7</span>' +
    '<span style="font-size:10px;color:#fbbf24;margin-left:4px">&#8482;</span>';

  b.style.cssText = [
    'position:fixed',
    'bottom:28px',
    'right:28px',
    'background:#0f1a15',
    'border:1px solid #1a3d2b',
    'border-left:3px solid #10b981',
    'border-radius:4px',
    'padding:10px 16px',
    'font-family:"Space Mono",monospace',
    'box-shadow:0 4px 24px rgba(16,185,129,.18)',
    'z-index:99999',
    'opacity:0',
    'transform:translateY(16px)',
    'transition:opacity .5s ease,transform .5s ease',
    'pointer-events:none',
    'min-width:180px',
  ].join(';');

  p.document.body.appendChild(b);

  function show() {
    b.style.opacity  = '1';
    b.style.transform = 'translateY(0)';
    setTimeout(function() {
      b.style.opacity  = '0';
      b.style.transform = 'translateY(16px)';
    }, 5000);   /* visible for 5 seconds */
  }

  show();                              /* show on load   */
  setInterval(show, 5 * 60 * 1000);   /* repeat every 5 min */
})();
</script>
""", height=0)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 00 — FIRST BANK STATEMENT
# ════════════════════════════════════════════════════════════════════════════
st.markdown('<div class="sel-section-title">00 — Bank Statement Auto-Fill &nbsp;<span style="color:#64748b;font-size:10px">— Optional</span></div>', unsafe_allow_html=True)
st.caption("Upload PDF bank statement. Credits are automatically classified into real income vs recycled amounts.")

col1, col2 = st.columns(2)
with col1:
    file_a = st.file_uploader("Upload Bank Statement (PDF or Excel)", type=["pdf","xlsx","xls"], key="upload_a")
with col2:
    pw_a   = st.text_input("PDF Password", type="password", key="pw_a", placeholder="Leave blank if not encrypted")
    if st.button("Extract Monthly Credits", key="btn_extract_a"):
        if not file_a:
            st.error("Please select a PDF file first.")
        else:
            with st.spinner("Extracting..."):
                try:
                    buckets, summary, bank, name, txns = parse_transactions(file_a.getvalue(), pw_a, filename=file_a.name)
                    rows = monthly_analysis(buckets, summary)
                    st.session_state.buckets_a  = buckets
                    st.session_state.summary_a  = summary
                    st.session_state.bank_a     = bank
                    st.session_state.name_a     = name
                    st.session_state.rows_a     = rows
                    st.session_state.txns_a     = txns
                    st.success(f"Extracted from {bank} statement — {name or 'account holder'}")

                    # ── AI Accuracy Verification (free, no API key needed) ──
                    # Only meaningful for PDF files that carry a stated total.
                    is_pdf = not file_a.name.lower().endswith((".xlsx", ".xls"))
                    if is_pdf and buckets:
                        from parser import extract_pdf_text as _ept
                        try:
                            raw_text = _ept(file_a.getvalue(), pw_a)
                            stated   = extract_stated_totals(raw_text)
                            verdict  = verify_extraction_accuracy(buckets, stated)
                            if verdict["pct_match"] is not None:
                                pct = verdict["pct_match"]
                                ext = verdict["extracted"]
                                stl = verdict["stated_total"]
                                colour = ("#34d399" if pct >= 95
                                          else "#fb923c" if pct >= 90
                                          else "#f87171")
                                st.markdown(
                                    f'<div style="background:rgba(0,0,0,.2);border:1px solid {colour}33;'
                                    f'border-radius:3px;padding:10px 14px;margin-top:8px;font-size:12px;">'
                                    f'<span style="color:{colour};font-weight:700">▶ Accuracy Check — {pct}% match</span>'
                                    f'&nbsp;&nbsp;<span style="color:#64748b">Extracted ₦{ext:,.0f} vs '
                                    f'stated ₦{stl:,.0f}</span><br>'
                                    f'<span style="color:#94a3b8;font-size:11px">{verdict["message"]}</span>'
                                    f'</div>',
                                    unsafe_allow_html=True,
                                )
                        except Exception:
                            pass  # Accuracy check is best-effort; never block the main flow

                except Exception as e:
                    if "EOF marker not found" in str(e) or "Unexpected EOF" in str(e):
                        st.error(
                            "This PDF appears to be corrupted or incomplete. "
                            "Please download the bank statement again from your bank app/portal."
                        )
                    else:
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
        if has_self:  hdr += '<th class="col-self">Self Deposits</th>'
        if has_rev:   hdr += '<th class="col-rev">Reversals</th>'
        if has_nb:    hdr += '<th class="col-nonbiz">Non-Business</th>'
        if has_loan:  hdr += '<th class="col-loan">Loan Disbursals</th>'
        hdr += '<th class="col-net">Eligible Income</th></tr>'

        body = ""
        t_gross = t_self = t_rev = t_nb = t_loan = t_net = 0.0
        for r in rows_a:
            t_gross += r["gross"]
            t_self  += r["self_transfer"]
            t_rev   += r["reversal"]
            t_nb    += r["non_business"]
            t_loan  += r["loan_disbursal"]
            t_net   += r["eligible_income"]
            body += (f'<tr><td>{r["label"]}</td>'
                     f'<td class="col-gross">{money(r["gross"])}</td>')
            if has_self:  body += f'<td class="col-self" style="color:var(--orange);font-size:11px">{money(r["self_transfer"]) if r["self_transfer"] > 0 else "—"}</td>'
            if has_rev:   body += f'<td class="col-rev">{("-"+money(r["reversal"])) if r["reversal"] > 0 else "—"}</td>'
            if has_nb:    body += f'<td class="col-nonbiz">{("-"+money(r["non_business"])) if r["non_business"] > 0 else "—"}</td>'
            if has_loan:  body += f'<td class="col-loan">{("-"+money(r["loan_disbursal"])) if r["loan_disbursal"] > 0 else "—"}</td>'
            body += f'<td class="col-net">{money(r["eligible_income"])}</td></tr>'

        # ── Totals footer ─────────────────────────────────────────────────
        foot = (f'<tfoot><tr>'
                f'<td style="color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:1px">Total</td>'
                f'<td class="col-gross" style="border-top:1px solid #1a3d2b">{money(t_gross)}</td>')
        if has_self:  foot += f'<td class="col-self" style="border-top:1px solid #1a3d2b">{money(t_self) if t_self > 0 else "—"}</td>'
        if has_rev:   foot += f'<td class="col-rev"  style="border-top:1px solid #1a3d2b">{("-"+money(t_rev))  if t_rev  > 0 else "—"}</td>'
        if has_nb:    foot += f'<td class="col-nonbiz" style="border-top:1px solid #1a3d2b">{("-"+money(t_nb))   if t_nb   > 0 else "—"}</td>'
        if has_loan:  foot += f'<td class="col-loan" style="border-top:1px solid #1a3d2b">{("-"+money(t_loan)) if t_loan > 0 else "—"}</td>'
        foot += (f'<td class="col-net" style="border-top:1px solid #1a3d2b;font-size:14px">{money(t_net)}</td>'
                 f'</tr></tfoot>')

        st.markdown(
            f'<table class="preview-table"><thead>{hdr}</thead><tbody>{body}</tbody>{foot}</table>',
            unsafe_allow_html=True,
        )
        if has_self:
            st.markdown(
                '<div style="font-size:10px;color:#fb923c;margin-top:4px">'
                '⚑ Self Deposits are shown for reference only — they are <strong>not deducted</strong> from eligible income.</div>',
                unsafe_allow_html=True,
            )

# ── Transaction Search — Statement A ─────────────────────────────────────────
if st.session_state.txns_a:
    st.markdown(
        '<div style="margin-top:16px;font-size:10px;letter-spacing:2px;color:#10b981;'
        'text-transform:uppercase;margin-bottom:6px">Transaction Search</div>',
        unsafe_allow_html=True,
    )
    search_a = st.text_input(
        "Search keyword",
        key="search_a",
        placeholder="e.g. salary, transfer, POS, Flutterwave...",
        label_visibility="collapsed",
    )
    if search_a and search_a.strip():
        kw = search_a.strip().lower()
        matches = [t for t in st.session_state.txns_a if kw in t["narration"].lower()]
        if matches:
            total_match = sum(t["amount"] for t in matches)
            st.markdown(
                f'<div style="font-size:11px;color:#64748b;margin-bottom:6px">'
                f'Found <span style="color:#10b981;font-weight:700">{len(matches)}</span> credit(s) '
                f'matching <em>"{search_a}"</em> — '
                f'Total: <span style="color:#34d399;font-weight:700">{money(total_match)}</span></div>',
                unsafe_allow_html=True,
            )
            _CAT_COLOUR = {
                "real_credit": "#34d399", "self_transfer": "#fb923c",
                "reversal": "#a78bfa", "non_business": "#64748b", "loan_disbursal": "#fbbf24",
            }
            rows_html = ""
            for t in matches[:100]:  # cap at 100 rows for performance
                clr = _CAT_COLOUR.get(t["category"], "#e2e8f0")
                cat_label = t["category"].replace("_", " ").title()
                rows_html += (
                    f'<tr><td style="color:#10b981">{t["ym"]}</td>'
                    f'<td style="color:#34d399;text-align:right">{money(t["amount"])}</td>'
                    f'<td><span style="background:rgba(255,255,255,.05);padding:1px 6px;'
                    f'border-radius:3px;font-size:9px;color:{clr}">{cat_label}</span></td>'
                    f'<td style="color:#94a3b8;font-size:11px">{t["narration"][:80]}</td></tr>'
                )
            if len(matches) > 100:
                rows_html += f'<tr><td colspan="4" style="color:#64748b;font-size:10px;padding-top:6px">... and {len(matches)-100} more</td></tr>'
            st.markdown(
                f'<table class="preview-table" style="margin-top:4px">'
                f'<thead><tr>'
                f'<th style="text-align:left">Month</th>'
                f'<th style="text-align:right">Amount</th>'
                f'<th style="text-align:left">Category</th>'
                f'<th style="text-align:left">Narration</th>'
                f'</tr></thead><tbody>{rows_html}</tbody></table>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="color:#64748b;font-size:12px;padding:8px 0">No credit transactions found matching <em>"{search_a}"</em>.</div>',
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
    file_b = st.file_uploader("Upload Second Bank Statement (PDF or Excel)", type=["pdf","xlsx","xls"], key="upload_b")
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
                    buckets_b, summary_b, bank_b, name_b, txns_b = parse_transactions(file_b.getvalue(), pw_b, filename=file_b.name)
                    rows_b = monthly_analysis(buckets_b, summary_b)
                    st.session_state.buckets_b = buckets_b
                    st.session_state.summary_b = summary_b
                    st.session_state.bank_b    = bank_b
                    st.session_state.name_b    = name_b
                    st.session_state.rows_b    = rows_b
                    st.session_state.txns_b    = txns_b
                    st.success(f"Second statement extracted: {bank_b} — {name_b or 'account holder'}")

                    # ── Accuracy Verification for statement B ──
                    is_pdf_b = not file_b.name.lower().endswith((".xlsx", ".xls"))
                    if is_pdf_b and buckets_b:
                        from parser import extract_pdf_text as _ept2
                        try:
                            raw_text_b = _ept2(file_b.getvalue(), pw_b)
                            stated_b   = extract_stated_totals(raw_text_b)
                            verdict_b  = verify_extraction_accuracy(buckets_b, stated_b)
                            if verdict_b["pct_match"] is not None:
                                pct = verdict_b["pct_match"]
                                ext = verdict_b["extracted"]
                                stl = verdict_b["stated_total"]
                                colour = ("#34d399" if pct >= 95
                                          else "#fb923c" if pct >= 90
                                          else "#f87171")
                                st.markdown(
                                    f'<div style="background:rgba(0,0,0,.2);border:1px solid {colour}33;'
                                    f'border-radius:3px;padding:10px 14px;margin-top:8px;font-size:12px;">'
                                    f'<span style="color:{colour};font-weight:700">▶ Accuracy Check — {pct}% match</span>'
                                    f'&nbsp;&nbsp;<span style="color:#64748b">Extracted ₦{ext:,.0f} vs '
                                    f'stated ₦{stl:,.0f}</span><br>'
                                    f'<span style="color:#94a3b8;font-size:11px">{verdict_b["message"]}</span>'
                                    f'</div>',
                                    unsafe_allow_html=True,
                                )
                        except Exception:
                            pass

                except Exception as e:
                    if "EOF marker not found" in str(e) or "Unexpected EOF" in str(e):
                        st.error(
                            "This PDF appears to be corrupted or incomplete. "
                            "Please download the bank statement again from your bank app/portal."
                        )
                    else:
                        st.error(f"Error: {e}")

# Show merged preview
if st.session_state.rows_a and st.session_state.rows_b:
    import datetime
    today = datetime.date.today().strftime("%Y-%m")
    rows_a_map = {r["ym"]: r for r in st.session_state.rows_a if r["ym"] < today and r["gross"] > 0}
    rows_b_map = {r["ym"]: r for r in st.session_state.rows_b if r["ym"] < today and r["gross"] > 0}
    common = sorted(set(rows_a_map) & set(rows_b_map))[-6:]

    if common:
        st.markdown('<div style="font-size:10px;letter-spacing:2px;color:#34d399;text-transform:uppercase;margin:12px 0 6px">▷ Merged Result (Intersection of Both Statements)</div>', unsafe_allow_html=True)
        html = '<table class="preview-table"><thead><tr><th style="text-align:left">Month</th><th class="col-gross">Statement 1 Net</th><th style="text-align:right;color:#f59e0b">Statement 2 Net</th><th class="col-net">Combined Net</th></tr></thead><tbody>'
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
                     f'<td style="text-align:right;color:#f59e0b">{money(netB)}</td>'
                     f'<td class="col-net">{money(combined)}</td></tr>')
        html += (f'</tbody><tfoot><tr>'
                 f'<td style="color:#64748b;font-size:10px;text-transform:uppercase">Total</td>'
                 f'<td class="col-gross">{money(tA)}</td>'
                 f'<td style="text-align:right;color:#f59e0b">{money(tB)}</td>'
                 f'<td class="col-net">{money(tC)}</td>'
                 f'</tr></tfoot></table>')
        html += f'<div style="font-size:11px;color:#64748b;margin-top:6px">ℹ Showing {len(common)} overlapping months.</div>'
        st.markdown(html, unsafe_allow_html=True)

# ── Transaction Search — Statement B ─────────────────────────────────────────
if st.session_state.txns_b:
    st.markdown(
        '<div style="margin-top:16px;font-size:10px;letter-spacing:2px;color:#f59e0b;'
        'text-transform:uppercase;margin-bottom:6px">Transaction Search — Statement 2</div>',
        unsafe_allow_html=True,
    )
    search_b = st.text_input(
        "Search keyword (statement 2)",
        key="search_b",
        placeholder="e.g. salary, transfer, POS...",
        label_visibility="collapsed",
    )
    if search_b and search_b.strip():
        kw_b = search_b.strip().lower()
        matches_b = [t for t in st.session_state.txns_b if kw_b in t["narration"].lower()]
        if matches_b:
            total_b = sum(t["amount"] for t in matches_b)
            st.markdown(
                f'<div style="font-size:11px;color:#64748b;margin-bottom:6px">'
                f'Found <span style="color:#f59e0b;font-weight:700">{len(matches_b)}</span> credit(s) '
                f'matching <em>"{search_b}"</em> — '
                f'Total: <span style="color:#34d399;font-weight:700">{money(total_b)}</span></div>',
                unsafe_allow_html=True,
            )
            _CAT_COLOUR = {
                "real_credit": "#34d399", "self_transfer": "#fb923c",
                "reversal": "#a78bfa", "non_business": "#64748b", "loan_disbursal": "#fbbf24",
            }
            rows_b_html = ""
            for t in matches_b[:100]:
                clr = _CAT_COLOUR.get(t["category"], "#e2e8f0")
                cat_label = t["category"].replace("_", " ").title()
                rows_b_html += (
                    f'<tr><td style="color:#f59e0b">{t["ym"]}</td>'
                    f'<td style="color:#34d399;text-align:right">{money(t["amount"])}</td>'
                    f'<td><span style="background:rgba(255,255,255,.05);padding:1px 6px;'
                    f'border-radius:3px;font-size:9px;color:{clr}">{cat_label}</span></td>'
                    f'<td style="color:#94a3b8;font-size:11px">{t["narration"][:80]}</td></tr>'
                )
            if len(matches_b) > 100:
                rows_b_html += f'<tr><td colspan="4" style="color:#64748b;font-size:10px;padding-top:6px">... and {len(matches_b)-100} more</td></tr>'
            st.markdown(
                f'<table class="preview-table" style="margin-top:4px">'
                f'<thead><tr>'
                f'<th style="text-align:left">Month</th>'
                f'<th style="text-align:right">Amount</th>'
                f'<th style="text-align:left">Category</th>'
                f'<th style="text-align:left">Narration</th>'
                f'</tr></thead><tbody>{rows_b_html}</tbody></table>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="color:#64748b;font-size:12px;padding:8px 0">No credit transactions found matching <em>"{search_b}"</em>.</div>',
                unsafe_allow_html=True,
            )


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
                     f'<td style="color:#fbbf24;font-weight:700">{mo_str}</td>'
                     f'<td style="color:#64748b;font-size:10px">{rule}</td>'
                     f'</tr>')
        html += (f'</tbody><tfoot><tr>'
                 f'<td colspan="6" style="color:#64748b">Total monthly external obligations</td>'
                 f'<td style="color:#fbbf24;font-weight:700">{money(total)}</td>'
                 f'<td>{len(data["bad_credit_accounts"])} bad flag(s)' if data["bad_credit_accounts"] else '<td>Clear'
                 + '</td></tr></tfoot></table>')
        st.markdown(html, unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 02 — MONTHLY INFLOWS (editable)
# ════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown('<div class="sel-section-title">02 — Monthly Inflows (Last 6 Months)</div>', unsafe_allow_html=True)
st.caption("Gross credits auto-filled from bank statement. Adjust deductions or add extra manual deductions below.")

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

# ── Sync prefill into session state so Streamlit widget cache is updated ──
# This fixes the bug where Oct 25 shows ₦0 because Streamlit cached the
# previous widget value. We push fresh values into st.session_state BEFORE
# the widgets render so they pick up the correct numbers.
if prefill:
    for i, r in enumerate(prefill[:6]):
        gross_key  = f"gross_{i}"
        deduct_key = f"deduct_{i}"
        count_key  = f"count_{i}"
        # Only overwrite if the extraction just happened (rows_a was just set)
        # Use a fingerprint to detect when new data arrives
        fp = f"{r['ym']}_{r.get('gross',0):.0f}"
        if st.session_state.get(f"fp_{i}") != fp:
            st.session_state[gross_key]  = float(r.get("gross", 0))
            st.session_state[deduct_key] = float(r.get("deductions", 0))
            st.session_state[count_key]  = max(1, int(r.get("count", 12)))
            st.session_state[f"fp_{i}"]  = fp

# Build default month labels
def default_months():
    months = []
    for i in range(1, 7):
        month = today.month - (7 - i)
        year  = today.year
        while month <= 0:
            month += 12
            year  -= 1
        months.append((f"{year}-{str(month).zfill(2)}", ym_label(f"{year}-{str(month).zfill(2)}")))
    return months

default_m = default_months()
inflow_data = []

# ── Column header row ─────────────────────────────────────────────────────────
h1, h2, h3, h4, h5, h6 = st.columns([1.2, 1.8, 1.5, 1.5, 1.5, 0.8])
with h1: st.markdown('<div style="font-size:9px;letter-spacing:2px;color:#64748b;text-transform:uppercase">Month</div>', unsafe_allow_html=True)
with h2: st.markdown('<div style="font-size:9px;letter-spacing:2px;color:#64748b;text-transform:uppercase">Gross Credit ₦</div>', unsafe_allow_html=True)
with h3: st.markdown('<div style="font-size:9px;letter-spacing:2px;color:#64748b;text-transform:uppercase">Deductions ₦</div>', unsafe_allow_html=True)
with h4: st.markdown('<div style="font-size:9px;letter-spacing:2px;color:#fb923c;text-transform:uppercase">Extra Deduction ₦</div>', unsafe_allow_html=True)
with h5: st.markdown('<div style="font-size:9px;letter-spacing:2px;color:#34d399;text-transform:uppercase">Net Inflow ₦</div>', unsafe_allow_html=True)
with h6: st.markdown('<div style="font-size:9px;letter-spacing:2px;color:#64748b;text-transform:uppercase">Count</div>', unsafe_allow_html=True)

for i in range(6):
    if prefill and i < len(prefill):
        r     = prefill[i]
        label = r["label"]
    else:
        label = default_m[i][1]

    c1, c2, c3, c4, c5, c6 = st.columns([1.2, 1.8, 1.5, 1.5, 1.5, 0.8])
    with c1:
        st.markdown(f'<div style="padding-top:8px;font-weight:700;color:#10b981">{label}</div>',
                    unsafe_allow_html=True)
    with c2:
        g = st.number_input("Gross Credit (₦)", min_value=0.0, step=1000.0,
                            key=f"gross_{i}", label_visibility="collapsed")
    with c3:
        d = st.number_input("Deductions (₦)", min_value=0.0, step=1000.0,
                            key=f"deduct_{i}", label_visibility="collapsed")
    with c4:
        x = st.number_input("Extra Deduction (₦)", min_value=0.0, step=1000.0,
                            key=f"extra_{i}", label_visibility="collapsed")
    with c5:
        net = max(g - d - x, 0)
        st.markdown(
            f'<div style="color:#34d399;font-weight:700;padding-top:8px;font-size:13px">{money(net)}</div>',
            unsafe_allow_html=True,
        )
    with c6:
        cnt = st.number_input("Count", min_value=1, max_value=9999,
                              key=f"count_{i}", label_visibility="collapsed")
    inflow_data.append({"label": label, "gross": g, "deduct": d, "extra": x, "net": net, "count": cnt})

st.markdown(
    '<div style="font-size:10px;color:#64748b;margin-top:6px;padding:8px 12px;'
    'background:rgba(0,0,0,.2);border-left:2px solid #1a3d2b;border-radius:2px">'
    '<strong style="color:#fb923c">Extra Deduction</strong> — use this to manually subtract any amount you\'ve identified '
    'from the search above (e.g. a recurring transfer you want excluded from income). '
    'Auto Deductions are pre-filled from the bank statement parser (reversals, loan disbursals, non-business).'
    '</div>',
    unsafe_allow_html=True,
)

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
            st.markdown('<div style="font-size:10px;letter-spacing:2px;color:#f59e0b;text-transform:uppercase;margin-bottom:8px">Requested Loan Analysis</div>', unsafe_allow_html=True)
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
            st.markdown('<div style="font-size:10px;letter-spacing:2px;color:#64748b;text-transform:uppercase;margin-bottom:6px">Classification Audit — All Tagged Credits</div>', unsafe_allow_html=True)

            audit_rows = []
            src_rows = (st.session_state.rows_a or [])
            today_ym = today.strftime("%Y-%m")
            for r in src_rows:
                if r["ym"] >= today_ym or r["gross"] == 0:
                    continue
                # Self-transfers are now informational (not deducted)
                if r.get("self_transfer", 0) > 0:
                    audit_rows.append({
                        "Month": r["label"],
                        "Category": "Self Deposit (info only)",
                        "Deducted": False,
                        "Amount": r["self_transfer"],
                    })
                for cat in ["reversal", "non_business", "loan_disbursal"]:
                    if r.get(cat, 0) > 0:
                        audit_rows.append({
                            "Month": r["label"],
                            "Category": cat.replace("_", " ").title(),
                            "Deducted": True,
                            "Amount": r[cat],
                        })
            # Extra manual deductions from grid
            for i, row in enumerate(inflow_data):
                if row.get("extra", 0) > 0:
                    audit_rows.append({
                        "Month": row["label"],
                        "Category": "Manual Deduction",
                        "Deducted": True,
                        "Amount": row["extra"],
                    })

            if audit_rows:
                df = pd.DataFrame(audit_rows)
                st.dataframe(
                    df, hide_index=True, use_container_width=True,
                    column_config={
                        "Amount": st.column_config.NumberColumn("Amount", format="₦%d"),
                        "Deducted": st.column_config.CheckboxColumn("Deducted from Eligible?"),
                    },
                )
                st.download_button(
                    "Download Classification Audit CSV",
                    df.to_csv(index=False).encode("utf-8"),
                    file_name="sel_classification_audit.csv",
                    mime="text/csv",
                )




