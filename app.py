from __future__ import annotations
import datetime
import math
import pandas as pd
import streamlit as st
from parser import (
    monthly_analysis, parse_transactions, parse_firstcentral,
    ym_label, CreditAccount,
    extract_stated_totals, verify_extraction_accuracy,
)
from sel_rules import calculate_eligibility, get_interest_rate, get_dti, loan_limits
from report_generator import generate_pdf_report

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
    --muted: #94a3b8; --gold: #fbbf24; --orange: #fb923c;
  }

  /* Global */
  .stApp { background: var(--bg) !important; color: var(--text) !important; font-family: 'Space Mono', monospace !important; }
  .block-container { padding: 2rem 2rem 4rem !important; max-width: 1000px !important; }

  /* Headers */
  h1 { font-family: 'DM Serif Display', serif !important; color: #fff !important; }
  h1 em { color: var(--accent) !important; }
  h2, h3 { font-family: 'Space Mono', monospace !important; color: var(--accent) !important;
           font-size: 12px !important; letter-spacing: 3px !important; text-transform: uppercase !important; font-weight: 700 !important; }

  /* Sections */
  .sel-section { background: var(--surface); border: 1px solid var(--border);
                 border-radius: 4px; padding: 24px; margin-bottom: 20px; }
  .sel-section-title { font-size: 15px; letter-spacing: 2px; color: #e2e8f0;
                       text-transform: uppercase; border-bottom: 2px solid var(--accent);
                       padding-bottom: 10px; margin-bottom: 16px; font-weight: 800; }
  .sel-caption { font-size: 13px; color: #cbd5e1; font-weight: 600;
                 margin: -8px 0 14px 0; line-height: 1.6; }

  /* Metric cards */
  .sel-card { background: var(--surface2); border: 1px solid var(--border);
              border-radius: 3px; padding: 14px; }
  .sel-card.highlight { border-color: var(--accent); box-shadow: 0 0 20px rgba(16,185,129,.1); }
  .sel-label { font-size: 11px; letter-spacing: 2px; color: var(--muted);
               text-transform: uppercase; margin-bottom: 4px; font-weight: 600; }
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
  .preview-table th { font-size: 12px; letter-spacing: 1px; color: #e2e8f0;
                      text-transform: uppercase; padding: 7px 10px; font-weight: 800;
                      border-bottom: 2px solid var(--accent); text-align: right; }
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
  .credit-table th { font-size: 10px; letter-spacing: 1px; color: var(--muted);
                     text-transform: uppercase; padding: 8px; font-weight: 700;
                     border-bottom: 1px solid var(--border); text-align: left; }
  .credit-table td { padding: 8px; border-bottom: 1px solid rgba(26,61,43,.4); vertical-align: top; }
  .credit-table tfoot td { border-top: 1px solid var(--border); font-weight: 700; }

  /* Sidebar */
  [data-testid="stSidebar"] { background: var(--surface) !important;
                               border-right: 1px solid var(--border) !important; }
  [data-testid="stSidebar"] label { color: var(--muted) !important; font-size: 11px !important;
                                    letter-spacing: 1px !important; text-transform: uppercase !important; font-weight: 600 !important; }

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
  [data-testid="stMetricLabel"] { color: var(--muted) !important; font-size: 11px !important;
                                   letter-spacing: 2px !important; text-transform: uppercase !important; font-weight: 600 !important; }
  [data-testid="stMetricValue"] { color: var(--accent) !important; font-size: 20px !important; }

  /* Divider */
  hr { border: none !important; border-top: 1px solid var(--border) !important; margin: 28px 0 !important; }

  /* Caption */
  [data-testid="stCaptionContainer"] { color: var(--muted) !important; font-size: 12px !important; }

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

  /* Widget labels — Location, Product Type, Tenor, Loan Amount, etc. */
  [data-testid="stWidgetLabel"] p,
  [data-testid="stWidgetLabel"] label,
  .stSelectbox label, .stNumberInput label,
  .stTextInput label, .stRadio label > div > p,
  div[data-testid="stSelectbox"] > label,
  div[data-testid="stNumberInput"] > label {
    font-size: 12px !important; font-weight: 700 !important;
    color: #e2e8f0 !important; letter-spacing: 0.5px !important;
  }

  /* Inflow grid header (legacy class — kept for compatibility) */
  .inflow-grid-header { font-size: 12px; letter-spacing: 1.5px; color: #e2e8f0;
                        text-transform: uppercase; font-weight: 800; padding-bottom: 4px; }

  /* Radio option labels (SEL / SME toggle) */
  [data-testid="stRadio"] label span,
  [data-testid="stRadio"] > div > label > div > p {
    font-size: 14px !important; font-weight: 700 !important;
    color: #e2e8f0 !important; letter-spacing: 1px !important;
  }
  /* Radio group title (hidden but keep consistent) */
  [data-testid="stRadio"] > label { display: none !important; }

  /* Selectbox option text */
  [data-testid="stSelectbox"] > div > div { color: #e2e8f0 !important; font-weight: 600 !important; }

  /* Download button */
  [data-testid="stDownloadButton"] button {
    background: rgba(16,185,129,.06) !important;
    border: 1px solid rgba(16,185,129,.35) !important;
    color: var(--green) !important;
    font-size: 11px !important;
    letter-spacing: 1px !important;
    font-weight: 600 !important;
  }
  [data-testid="stDownloadButton"] button:hover {
    background: rgba(16,185,129,.12) !important;
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


# ── Excel Export Helper ────────────────────────────────────────────────────────
def generate_xlsx(rows: list[dict], result: dict | None = None,
                  account_name: str = "", bank: str = "") -> bytes:
    """Generate a formatted .xlsx with monthly breakdown + optional eligibility sheet."""
    from io import BytesIO
    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()

    # ── Palette ──────────────────────────────────────────────────────────────
    DARK   = "0D2818"
    MID    = "0F1A15"
    LIGHT  = "162019"
    ACCENT = "10B981"
    GREEN  = "34D399"
    GOLD   = "F59E0B"
    ORANGE = "FB923C"
    PURPLE = "A78BFA"
    MUTED  = "64748B"
    WHITE  = "E2E8F0"
    RED    = "F87171"

    def hdr_font(color=WHITE):  return Font(name="Calibri", bold=True, color=color, size=10)
    def body_font(color=WHITE): return Font(name="Calibri", color=color, size=10)
    def bold_font(color=WHITE): return Font(name="Calibri", bold=True, color=color, size=10)
    def fill(hex_color):        return PatternFill("solid", fgColor=hex_color)
    def center():               return Alignment(horizontal="center", vertical="center")
    def right():                return Alignment(horizontal="right", vertical="center")
    def left():                 return Alignment(horizontal="left",  vertical="center")
    def thin_border():
        s = Side(style="thin", color="1A3D2B")
        return Border(bottom=s)

    today_ym = datetime.date.today().strftime("%Y-%m")
    display_rows = [r for r in rows if r.get("ym","") < today_ym and r.get("gross",0) > 0][-12:]

    # ── Sheet 1: Monthly Breakdown ────────────────────────────────────────────
    ws = wb.active
    ws.title = "Monthly Breakdown"
    ws.sheet_properties.tabColor = ACCENT

    # Info rows
    ws["A1"] = "SEL Loan Eligibility Calculator"
    ws["A1"].font = Font(name="Calibri", bold=True, color=ACCENT, size=13)
    ws["A2"] = f"Account: {account_name}   |   Bank: {bank}   |   Generated: {datetime.date.today()}"
    ws["A2"].font = body_font(MUTED)
    ws.merge_cells("A1:H1")
    ws.merge_cells("A2:H2")

    # Column headers
    has_self  = any(r.get("self_transfer",0)  > 0 for r in display_rows)
    has_rev   = any(r.get("reversal",0)       > 0 for r in display_rows)
    has_nb    = any(r.get("non_business",0)   > 0 for r in display_rows)
    has_loan  = any(r.get("loan_disbursal",0) > 0 for r in display_rows)

    base_cols = ["Month", "Total Inflow (NGN)"]
    col_colors = [WHITE, GREEN]
    if has_self:  base_cols.append("Self Deposits (NGN)");  col_colors.append(ORANGE)
    if has_rev:   base_cols.append("Reversals (NGN)");      col_colors.append(PURPLE)
    if has_nb:    base_cols.append("Non-Business (NGN)");   col_colors.append(MUTED)
    if has_loan:  base_cols.append("Loan Disbursals (NGN)");col_colors.append(GOLD)
    base_cols.append("Eligible Income (NGN)")
    col_colors.append(ACCENT)

    HDR_ROW = 4
    for ci, (colname, colclr) in enumerate(zip(base_cols, col_colors), 1):
        cell = ws.cell(row=HDR_ROW, column=ci, value=colname)
        cell.fill = fill(DARK)
        cell.font = hdr_font(colclr)
        cell.alignment = right() if ci > 1 else left()
        cell.border = thin_border()
        ws.column_dimensions[get_column_letter(ci)].width = 22 if ci > 1 else 12

    # Data rows
    t_gross = t_self = t_rev = t_nb = t_loan = t_net = 0.0
    for ri, r in enumerate(display_rows):
        row_num = HDR_ROW + 1 + ri
        row_fill = fill(MID) if ri % 2 == 0 else fill(LIGHT)
        vals = [r.get("label", r.get("ym","")), r.get("gross",0)]
        if has_self:  vals.append(r.get("self_transfer",0))
        if has_rev:   vals.append(r.get("reversal",0))
        if has_nb:    vals.append(r.get("non_business",0))
        if has_loan:  vals.append(r.get("loan_disbursal",0))
        vals.append(r.get("eligible_income",0))
        t_gross += r.get("gross",0); t_self += r.get("self_transfer",0)
        t_rev   += r.get("reversal",0); t_nb += r.get("non_business",0)
        t_loan  += r.get("loan_disbursal",0); t_net += r.get("eligible_income",0)
        for ci, (val, clr) in enumerate(zip(vals, col_colors), 1):
            cell = ws.cell(row=row_num, column=ci, value=val)
            cell.fill = row_fill
            cell.font = body_font(clr)
            cell.alignment = right() if ci > 1 else left()
            if ci > 1 and isinstance(val, (int, float)):
                cell.number_format = '#,##0.00'

    # Totals row
    tot_row = HDR_ROW + 1 + len(display_rows)
    tot_vals = ["TOTAL", t_gross]
    if has_self:  tot_vals.append(t_self)
    if has_rev:   tot_vals.append(t_rev)
    if has_nb:    tot_vals.append(t_nb)
    if has_loan:  tot_vals.append(t_loan)
    tot_vals.append(t_net)
    for ci, (val, clr) in enumerate(zip(tot_vals, col_colors), 1):
        cell = ws.cell(row=tot_row, column=ci, value=val)
        cell.fill = fill("0A2E1F")
        cell.font = bold_font(ACCENT if ci == len(tot_vals) else clr)
        cell.alignment = right() if ci > 1 else left()
        if ci > 1 and isinstance(val, (int, float)):
            cell.number_format = '#,##0.00'

    # ── Sheet 2: Eligibility Summary (if result provided) ──────────────────
    if result:
        ws2 = wb.create_sheet("Eligibility Summary")
        ws2.sheet_properties.tabColor = GOLD
        _tenor_v = result.get("tenor")
        pairs = [
            ("Decision",             "Approved" if result.get("approved") else "Below Minimum"),
            ("Max Loan Amount",      result.get("max_loan", 0)),
            ("Tenor (Months)",       _tenor_v if _tenor_v else "—"),
            ("Applicable Turnover",  result.get("applicable_turnover", 0)),
            ("Total Eligible Net",   result.get("total_net", 0)),
            ("DTI",                  f"{result.get('dti',0)*100:.2f}%"),
            ("Interest Rate",        f"{(result.get('interest_rate') or 0)*100:.2f}%"),
            ("Repayment Frequency",  result.get("repayment_frequency","")),
            ("Max Repayment/Period", result.get("max_repayment_display", 0)),
            ("Max Total Repayment",  result.get("max_total_repayment", 0)),
        ]
        ws2["A1"] = "Eligibility Summary"
        ws2["A1"].font = Font(name="Calibri", bold=True, color=ACCENT, size=13)
        ws2.column_dimensions["A"].width = 28
        ws2.column_dimensions["B"].width = 20
        for ri2, (label, val) in enumerate(pairs, 3):
            lc = ws2.cell(row=ri2, column=1, value=label)
            vc = ws2.cell(row=ri2, column=2, value=val)
            lc.font = body_font(MUTED); lc.fill = fill(MID)
            row_clr = GREEN if (label == "Decision" and result.get("approved")) else (RED if label == "Decision" else WHITE)
            vc.font = bold_font(row_clr); vc.fill = fill(LIGHT)
            vc.alignment = right()
            if isinstance(val, (int, float)) and val > 1:
                vc.number_format = '#,##0.00'

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── What-if Reverse Calculator ────────────────────────────────────────────────
def required_income_for_loan(target_loan: float, tenor: int,
                              location: str, product_type: str,
                              other_loans: float = 0,
                              manual_rate_pct: float | None = None) -> dict:
    """Reverse-calculate the monthly income needed to qualify for target_loan."""
    rate = (manual_rate_pct / 100) if (manual_rate_pct and manual_rate_pct > 0) \
           else get_interest_rate(target_loan, location, product_type)
    if not rate:
        return {"ok": False, "reason": "Rate unavailable for this amount/product combo"}
    min_loan, max_loan = loan_limits(location, product_type)
    if target_loan < min_loan or target_loan > max_loan:
        return {"ok": False, "reason": f"Target ₦{target_loan:,.0f} outside product range {money(min_loan)}–{money(max_loan)}"}
    pmt = target_loan * rate / (1 - math.pow(1 + rate, -tenor))
    # Use a safe total_net=5M to get non-zero DTI (avoids 0.0 from RENEWAL <801k edge case)
    dti = get_dti(total_net=5_000_000, product_type=product_type, location=location)
    if not dti:
        return {"ok": False, "reason": "DTI is zero for this product — check product/location combo"}
    required_turnover = (pmt + other_loans) / dti
    freq = "Weekly (est.)" if required_turnover >= 200_000 else "Monthly"
    return {
        "ok": True, "rate": rate, "pmt": pmt, "dti": dti,
        "required_turnover": required_turnover,
        "repayment_frequency": freq,
    }


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
  <div style="font-size:13px;color:#cbd5e1;margin-top:6px;font-weight:700;letter-spacing:0.5px">
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
# PRODUCT SELECTOR
# ════════════════════════════════════════════════════════════════════════════
st.markdown(
    '<div style="font-size:15px;font-weight:800;color:#e2e8f0;letter-spacing:1px;'
    'text-transform:uppercase;margin-bottom:6px">Product</div>',
    unsafe_allow_html=True,
)
_product = st.radio(
    "Product",
    options=["SEL", "SME"],
    horizontal=True,
    key="product",
    help="SEL uses 6 months of bank statement data. SME uses 12 months.",
)
N_MONTHS = 6 if _product == "SEL" else 12
st.markdown(
    f'<div style="font-size:13px;font-weight:700;color:#34d399;margin:-8px 0 24px 0;">'
    f'{"▶ 6-month analysis window" if _product == "SEL" else "▶ 12-month analysis window"}'
    f'</div>',
    unsafe_allow_html=True,
)


# ════════════════════════════════════════════════════════════════════════════
# SECTION 00 — FIRST BANK STATEMENT
# ════════════════════════════════════════════════════════════════════════════
st.markdown('<div class="sel-section-title">00 — Bank Statement Auto-Fill &nbsp;<span style="color:#94a3b8;font-size:11px">— Optional</span></div>', unsafe_allow_html=True)
st.markdown('<div class="sel-caption">Upload PDF bank statement. Credits are automatically classified into real income vs recycled amounts.</div>', unsafe_allow_html=True)

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
              and r["gross"] > 0][-N_MONTHS:]
    if rows_a:
        has_self  = any(r["self_transfer"]  > 0 for r in rows_a)
        has_rev   = any(r["reversal"]       > 0 for r in rows_a)
        has_nb    = any(r["non_business"]   > 0 for r in rows_a)
        has_loan  = any(r["loan_disbursal"] > 0 for r in rows_a)

        # ── Business Type Inference ───────────────────────────────────────
        if st.session_state.txns_a:
            _narr_lc = [t["narration"].lower() for t in st.session_state.txns_a]
            _biz_scores: dict[str, tuple] = {
                "Salary Earner":       (["salary","payroll","wage","payslip","staff pay"],              "💼"),
                "E-commerce":          (["flutterwave","paystack","stripe","shopify","jumia","konga","selar","paypal","remita"], "🛒"),
                "Food & Beverage":     (["food","restaurant","kitchen","catering","eatery","canteen","bakery","snack","meal","chop"], "🍽"),
                "Logistics":           (["delivery","dispatch","logistics","courier","shipping","transport","freight"], "🚚"),
                "POS / Agent Banking": (["pos ","mpos","terminal","agent banking"],                     "🏧"),
                "Professional Svcs":   (["consulting","invoice","professional","retainer","service fee","legal fee"], "📋"),
                "Real Estate":         (["rent","property","estate","tenancy","lease","landlord"],      "🏢"),
                "Trader / Market":     (["market","goods","supply","wholesale","retail","merchandise","dealer"], "🛍"),
            }
            _scored = {
                label: (sum(1 for n in _narr_lc if any(k in n for k in kws)), icon)
                for label, (kws, icon) in _biz_scores.items()
            }
            _best_biz  = max(_scored, key=lambda x: _scored[x][0])
            _best_cnt, _best_icon = _scored[_best_biz]
            if _best_cnt >= 3:
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">'
                    f'<div style="font-size:9px;letter-spacing:2px;color:#64748b;text-transform:uppercase">Business Type</div>'
                    f'<div style="background:rgba(16,185,129,.12);border:1px solid rgba(16,185,129,.3);'
                    f'border-radius:20px;padding:3px 14px;font-size:12px;font-weight:700;color:#10b981">'
                    f'{_best_icon} {_best_biz}</div>'
                    f'<div style="font-size:10px;color:#64748b">{_best_cnt} matching narrations</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

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
                '⚑ Self Deposits (OWealth, Renflex, Renvault, savings round-trips) are <strong>deducted</strong> from eligible income as they are not business income.</div>',
                unsafe_allow_html=True,
            )

        # ── Income Trend Chart ────────────────────────────────────────────
        _scale = max(
            max(r["gross"]            for r in rows_a),
            max(r["eligible_income"]  for r in rows_a),
        ) or 1
        BAR_H = 110  # max bar height in px

        def _fmt_v(v: float) -> str:
            return f"₦{v/1_000_000:.1f}m" if v >= 1_000_000 else f"₦{v/1_000:.0f}k"

        avg6 = sum(r["eligible_income"] for r in rows_a) / len(rows_a)
        avg3 = sum(r["eligible_income"] for r in rows_a[-3:]) / min(3, len(rows_a))

        # Trend: compare last month to first month
        trend_delta = rows_a[-1]["eligible_income"] - rows_a[0]["eligible_income"]
        trend_icon  = "▲" if trend_delta > 0 else "▼" if trend_delta < 0 else "▬"
        trend_col   = "#34d399" if trend_delta > 0 else "#f87171" if trend_delta < 0 else "#6b7f74"

        bars_html = ""
        for r in rows_a:
            g_px = int(r["gross"]           / _scale * BAR_H) if r["gross"]           > 0 else 0
            e_px = int(r["eligible_income"] / _scale * BAR_H) if r["eligible_income"] > 0 else 0
            bars_html += (
                f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;min-width:0">'
                # amount label
                f'<div style="font-size:9px;color:#34d399;margin-bottom:4px;white-space:nowrap">'
                f'{_fmt_v(r["eligible_income"])}</div>'
                # bar stack
                f'<div style="width:70%;height:{BAR_H}px;position:relative">'
                f'<div style="position:absolute;bottom:0;left:0;right:0;height:{g_px}px;'
                f'background:rgba(16,185,129,.13);border-radius:2px 2px 0 0"></div>'
                f'<div style="position:absolute;bottom:0;left:0;right:0;height:{e_px}px;'
                f'background:linear-gradient(180deg,#34d399 0%,#10b981 100%);border-radius:2px 2px 0 0"></div>'
                f'</div>'
                # month label
                f'<div style="font-size:9px;color:#6b7f74;margin-top:6px;white-space:nowrap">'
                f'{r["label"]}</div>'
                f'</div>'
            )

        st.markdown(
            f'<div style="margin-top:20px;padding:16px 14px 14px;background:rgba(0,0,0,.18);'
            f'border:1px solid #1a3d2b;border-radius:4px">'
            # header
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:14px">'
            f'<div style="font-size:9px;letter-spacing:2px;color:#6b7f74;text-transform:uppercase">Income Trend</div>'
            f'<div style="font-size:10px;color:{trend_col};letter-spacing:1px">'
            f'{trend_icon} {_fmt_v(abs(trend_delta))} vs first month</div>'
            f'</div>'
            # bars row
            f'<div style="display:flex;align-items:flex-end;gap:6px">{bars_html}</div>'
            # averages + legend footer
            f'<div style="display:flex;gap:24px;margin-top:12px;padding-top:10px;border-top:1px solid #1a3d2b;align-items:center">'
            f'<div><div style="font-size:8px;letter-spacing:2px;color:#6b7f74;text-transform:uppercase;margin-bottom:2px">6-mo avg</div>'
            f'<div style="font-size:13px;font-weight:700;color:#10b981">{_fmt_v(avg6)}</div></div>'
            f'<div><div style="font-size:8px;letter-spacing:2px;color:#6b7f74;text-transform:uppercase;margin-bottom:2px">3-mo avg</div>'
            f'<div style="font-size:13px;font-weight:700;color:#fbbf24">{_fmt_v(avg3)}</div></div>'
            f'<div style="margin-left:auto;display:flex;gap:12px;align-items:center">'
            f'<span style="font-size:9px;color:#6b7f74">'
            f'<span style="display:inline-block;width:8px;height:8px;'
            f'background:linear-gradient(180deg,#34d399,#10b981);border-radius:1px;'
            f'margin-right:3px;vertical-align:middle"></span>Eligible</span>'
            f'<span style="font-size:9px;color:#6b7f74">'
            f'<span style="display:inline-block;width:8px;height:8px;'
            f'background:rgba(16,185,129,.2);border-radius:1px;'
            f'margin-right:3px;vertical-align:middle"></span>Gross</span>'
            f'</div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Download buttons ──────────────────────────────────────────────
        _pdf_stmt = generate_pdf_report(
            account_name = st.session_state.name_a or "Account Holder",
            bank         = st.session_state.bank_a or "Bank",
            rows         = rows_a,
        )
        _xlsx_stmt = generate_xlsx(
            rows         = rows_a,
            account_name = st.session_state.name_a or "Account Holder",
            bank         = st.session_state.bank_a or "Bank",
        )
        _safe_stmt = (st.session_state.name_a or "statement").replace(" ", "_").lower()
        _dl1, _dl2 = st.columns(2)
        with _dl1:
            st.download_button(
                label     = "⬇  Download Statement Analysis (PDF)",
                data      = _pdf_stmt,
                file_name = f"SEL_Statement_{_safe_stmt}_{datetime.date.today():%Y%m%d}.pdf",
                mime      = "application/pdf",
                key       = "dl_statement_pdf",
                use_container_width=True,
            )
        with _dl2:
            st.download_button(
                label     = "⬇  Download Monthly Breakdown (Excel)",
                data      = _xlsx_stmt,
                file_name = f"SEL_Breakdown_{_safe_stmt}_{datetime.date.today():%Y%m%d}.xlsx",
                mime      = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key       = "dl_statement_xlsx",
                use_container_width=True,
            )

        # ── Income Consistency Score ──────────────────────────────────────
        import statistics as _stat_mod
        _ei_vals = [r["eligible_income"] for r in rows_a if r["eligible_income"] > 0]
        if len(_ei_vals) >= 2:
            _ei_mean = sum(_ei_vals) / len(_ei_vals)
            _ei_std  = _stat_mod.stdev(_ei_vals)
            _cv      = _ei_std / _ei_mean if _ei_mean else 0
            _peak    = max(_ei_vals)
            _trough  = min(_ei_vals)
            _cliff   = _peak / _trough if _trough > 0 else 99
            if _cv < 0.30:
                _cs_label, _cs_col = "Stable",   "#34d399"
            elif _cv < 0.60:
                _cs_label, _cs_col = "Moderate", "#f59e0b"
            else:
                _cs_label, _cs_col = "Volatile", "#f87171"
            _cliff_html = ""
            if _cliff > 5 and len(_ei_vals) >= 3:
                _cliff_html = (
                    f'<span style="margin-left:8px;background:rgba(248,113,113,.12);'
                    f'border:1px solid rgba(248,113,113,.3);border-radius:4px;'
                    f'padding:2px 8px;font-size:10px;color:#f87171">⚠ Cliff {_cliff:.1f}× peak vs trough</span>'
                )
            st.markdown(
                f'<div style="display:flex;align-items:center;flex-wrap:wrap;gap:12px;margin:10px 0;'
                f'padding:10px 14px;background:rgba(255,255,255,.03);border-radius:6px;'
                f'border-left:3px solid {_cs_col}">'
                f'<div style="font-size:9px;letter-spacing:2px;color:#64748b;text-transform:uppercase">Income Consistency</div>'
                f'<div style="font-size:13px;font-weight:700;color:{_cs_col}">● {_cs_label}</div>'
                f'<div style="font-size:11px;color:#6b7f74">CV {_cv:.0%}</div>'
                f'<div style="font-size:11px;color:#6b7f74">Peak/Trough {_cliff:.1f}×</div>'
                f'{_cliff_html}'
                f'</div>',
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


# ── Statement Intelligence Panel ─────────────────────────────────────────────
if st.session_state.txns_a:
    import re as _re_intel, statistics as _stat_intel
    from collections import defaultdict as _ddict_intel

    _txns_i  = st.session_state.txns_a
    _intel   = []   # each item is an HTML string for a panel card

    # ── Income Source Breakdown ───────────────────────────────────────────
    _tot_amt = sum(t["amount"] for t in _txns_i)
    if _tot_amt > 0:
        _src = [
            ("Bank Transfers",     ["transfer","trf/"," nip "," neft ","instant payment","mobile transfer"], "#34d399"),
            ("Aggregator/Fintech", ["settlement","flutterwave","paystack","remita","interswitch","nibss","squad","stripe","selar"], "#a78bfa"),
            ("Salary / Payroll",   ["salary","payroll","wage","payslip"], "#fbbf24"),
            ("POS / Terminal",     ["pos ","mpos","terminal","agent banking"], "#fb923c"),
        ]
        _src_amts = []
        _accounted = 0.0
        for _lbl, _kws, _clr in _src:
            _a = sum(t["amount"] for t in _txns_i if any(k in t["narration"].lower() for k in _kws))
            _src_amts.append((_lbl, _a, _clr))
            _accounted += _a
        _src_amts.append(("Other", max(_tot_amt - _accounted, 0), "#64748b"))
        _src_amts = [x for x in _src_amts if x[1] > 0]

        _pos_pct = next((a / _tot_amt for l, a, c in _src_amts if l == "POS / Terminal"), 0)
        _bars = ""
        for _lbl, _amt, _clr in _src_amts:
            _p = _amt / _tot_amt
            _bars += (
                f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:5px">'
                f'<div style="width:120px;font-size:10px;color:#94a3b8;text-align:right">{_lbl}</div>'
                f'<div style="flex:1;background:rgba(255,255,255,.06);border-radius:3px;height:9px">'
                f'<div style="width:{_p:.0%};background:{_clr};border-radius:3px;height:9px"></div></div>'
                f'<div style="width:34px;font-size:10px;color:{_clr};font-weight:700">{_p:.0%}</div>'
                f'</div>'
            )
        _pos_flag = ""
        if _pos_pct >= 0.5:
            _pos_flag = (
                f'<div style="margin-top:8px;padding:6px 10px;background:rgba(251,146,60,.08);'
                f'border-left:3px solid #fb923c;border-radius:3px;font-size:10px;color:#fb923c">'
                f'⚠ POS-heavy — {_pos_pct:.0%} from terminal settlements. '
                f'Confirm business operations drive these credits, not float cycling.</div>'
            )
        _intel.append(
            f'<div style="flex:1;min-width:240px">'
            f'<div style="font-size:9px;letter-spacing:2px;color:#64748b;text-transform:uppercase;margin-bottom:8px">Income Source Breakdown</div>'
            f'{_bars}{_pos_flag}</div>'
        )

    # ── Recurring Income Detection ────────────────────────────────────────
    def _nkey(n: str) -> str:
        n = _re_intel.sub(r"[\d/\-:.,]", "", n.lower()).strip()
        return _re_intel.sub(r"\s+", " ", n)[:35]

    _grp_i: dict = _ddict_intel(lambda: _ddict_intel(float))
    for _t in _txns_i:
        _k = _nkey(_t["narration"])
        if len(_k) >= 6:
            _grp_i[_k][_t["ym"]] += _t["amount"]

    _recur = []
    for _k, _ym_map in _grp_i.items():
        if len(_ym_map) >= 3:
            _amts = list(_ym_map.values())
            _mean_a = sum(_amts) / len(_amts)
            if _mean_a > 0 and all(abs(a - _mean_a) / _mean_a <= 0.45 for a in _amts):
                _rep = next((t["narration"] for t in _txns_i if _nkey(t["narration"]) == _k), _k)
                _recur.append({"narr": _rep[:45], "months": len(_ym_map), "avg": _mean_a})
    _recur.sort(key=lambda x: -x["avg"])

    if _recur:
        _rrows = "".join(
            f'<tr>'
            f'<td style="color:#94a3b8;font-size:11px;padding:3px 4px">{rx["narr"]}</td>'
            f'<td style="color:#34d399;text-align:right;font-weight:700;padding:3px 4px;white-space:nowrap">{money(rx["avg"])}/mo</td>'
            f'<td style="color:#10b981;text-align:center;padding:3px 4px">{rx["months"]}mo</td>'
            f'</tr>'
            for rx in _recur[:8]
        )
        _intel.append(
            f'<div style="flex:1;min-width:240px">'
            f'<div style="font-size:9px;letter-spacing:2px;color:#34d399;text-transform:uppercase;margin-bottom:8px">Recurring Income Detected</div>'
            f'<table style="width:100%;border-collapse:collapse">'
            f'<thead><tr>'
            f'<th style="font-size:9px;color:#64748b;text-align:left;padding-bottom:4px">Narration</th>'
            f'<th style="font-size:9px;color:#64748b;text-align:right;padding-bottom:4px">Avg/Month</th>'
            f'<th style="font-size:9px;color:#64748b;text-align:center;padding-bottom:4px">Seen</th>'
            f'</tr></thead><tbody>{_rrows}</tbody></table>'
            f'</div>'
        )

    # ── Loan Cycling Flag ─────────────────────────────────────────────────
    # Signal 1: narration keywords in credit transactions
    _loan_kw = ["loan","disburs","credit facility","lending","overdraft","cash advance","borrow","float"]
    _loan_txns = [t for t in _txns_i if any(k in t["narration"].lower() for k in _loan_kw)]
    _loan_mos: dict = _ddict_intel(float)
    for _lt in _loan_txns:
        _loan_mos[_lt["ym"]] += _lt["amount"]

    # Signal 2: parser already flagged loan_disbursal category in rows
    _parser_loan_months = {
        r["ym"]: r["loan_disbursal"]
        for r in (st.session_state.rows_a or [])
        if r.get("loan_disbursal", 0) > 0
    }
    _all_loan_mos = set(_loan_mos) | set(_parser_loan_months)
    _loan_total = sum(_loan_mos.values()) + sum(_parser_loan_months.values())

    if len(_all_loan_mos) >= 2:
        # Show per-month detail
        _detail_rows = ""
        for _ym in sorted(_all_loan_mos):
            _narr_amt  = _loan_mos.get(_ym, 0)
            _parse_amt = _parser_loan_months.get(_ym, 0)
            _mo_total  = max(_narr_amt, _parse_amt)  # avoid double-counting same txns
            _lbl = ym_label(_ym)
            _detail_rows += (
                f'<tr>'
                f'<td style="color:#94a3b8;font-size:11px;padding:2px 4px">{_lbl}</td>'
                f'<td style="color:#f87171;font-weight:700;text-align:right;padding:2px 4px;white-space:nowrap">{money(_mo_total)}</td>'
                f'</tr>'
            )
        _intel.append(
            f'<div style="flex:1;min-width:240px">'
            f'<div style="padding:10px 14px;background:rgba(248,113,113,.07);'
            f'border-left:3px solid #f87171;border-radius:4px">'
            f'<div style="font-size:9px;letter-spacing:2px;color:#f87171;text-transform:uppercase;margin-bottom:6px">⚠ Loan Cycling Signal</div>'
            f'<div style="font-size:11px;color:#94a3b8;margin-bottom:8px">'
            f'Loan-like credits detected in <span style="color:#f87171;font-weight:700">{len(_all_loan_mos)} months</span>. '
            f'Verify disbursements are not recycling through the account.</div>'
            f'<table style="width:100%;border-collapse:collapse">{_detail_rows}</table>'
            f'</div></div>'
        )

    if _intel:
        st.markdown("---")
        st.markdown(
            '<div style="font-size:10px;letter-spacing:2px;color:#f59e0b;'
            'text-transform:uppercase;margin-bottom:14px">Statement Intelligence</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="display:flex;flex-wrap:wrap;gap:24px">{"".join(_intel)}</div>',
            unsafe_allow_html=True,
        )

# ════════════════════════════════════════════════════════════════════════════
# SECTION 00B — SECOND BANK STATEMENT
# ════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown('<div class="sel-section-title">00B — Second Bank Statement &nbsp;<span style="color:#94a3b8;font-size:11px">— Optional</span></div>', unsafe_allow_html=True)
st.markdown('<div class="sel-caption">Upload a second bank statement. Nets are merged month-by-month across all available months from either statement.</div>', unsafe_allow_html=True)

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

# Show Statement B own analysis
if st.session_state.rows_b:
    import datetime as _dt2
    _today_b = _dt2.date.today().strftime("%Y-%m")
    rows_b_own = [r for r in st.session_state.rows_b if r["ym"] < _today_b and r["gross"] > 0][-N_MONTHS:]
    if rows_b_own:
        _bname = st.session_state.bank_b or "Statement 2"
        _bacco = st.session_state.name_b or ""
        st.markdown(
            f'<div style="font-size:10px;letter-spacing:2px;color:#f59e0b;text-transform:uppercase;'
            f'margin:16px 0 6px">▷ Statement 2 Breakdown — {_bname}'
            f'{(" — " + _bacco) if _bacco else ""}</div>',
            unsafe_allow_html=True,
        )
        _b_has_rev  = any(r["reversal"]       > 0 for r in rows_b_own)
        _b_has_nb   = any(r["non_business"]   > 0 for r in rows_b_own)
        _b_has_loan = any(r["loan_disbursal"] > 0 for r in rows_b_own)
        _b_hdr = ('<tr>'
                  '<th class="col-gross" style="text-align:left">Month</th>'
                  '<th class="col-gross">Total Inflow</th>')
        if _b_has_rev:   _b_hdr += '<th class="col-rev">Reversals</th>'
        if _b_has_nb:    _b_hdr += '<th class="col-nonbiz">Non-Business</th>'
        if _b_has_loan:  _b_hdr += '<th class="col-loan">Loan Disbursals</th>'
        _b_hdr += '<th class="col-net">Eligible Income</th></tr>'
        _b_body = ""
        _btg = _btr = _btn = _btl = _bte = 0.0
        for r in rows_b_own:
            _btg += r["gross"]; _btr += r["reversal"]; _btn += r["non_business"]
            _btl += r["loan_disbursal"]; _bte += r["eligible_income"]
            _b_body += f'<tr><td>{r["label"]}</td><td class="col-gross">{money(r["gross"])}</td>'
            if _b_has_rev:   _b_body += f'<td class="col-rev">{("-"+money(r["reversal"])) if r["reversal"] > 0 else "—"}</td>'
            if _b_has_nb:    _b_body += f'<td class="col-nonbiz">{("-"+money(r["non_business"])) if r["non_business"] > 0 else "—"}</td>'
            if _b_has_loan:  _b_body += f'<td class="col-loan">{("-"+money(r["loan_disbursal"])) if r["loan_disbursal"] > 0 else "—"}</td>'
            _b_body += f'<td class="col-net">{money(r["eligible_income"])}</td></tr>'
        _b_foot = (f'<tfoot><tr>'
                   f'<td style="color:#64748b;font-size:10px;text-transform:uppercase">Total</td>'
                   f'<td class="col-gross" style="border-top:1px solid #1a3d2b">{money(_btg)}</td>')
        if _b_has_rev:   _b_foot += f'<td class="col-rev" style="border-top:1px solid #1a3d2b">{("-"+money(_btr)) if _btr > 0 else "—"}</td>'
        if _b_has_nb:    _b_foot += f'<td class="col-nonbiz" style="border-top:1px solid #1a3d2b">{("-"+money(_btn)) if _btn > 0 else "—"}</td>'
        if _b_has_loan:  _b_foot += f'<td class="col-loan" style="border-top:1px solid #1a3d2b">{("-"+money(_btl)) if _btl > 0 else "—"}</td>'
        _b_foot += (f'<td class="col-net" style="border-top:1px solid #1a3d2b;font-size:14px">{money(_bte)}</td>'
                    f'</tr></tfoot>')
        st.markdown(
            f'<table class="preview-table"><thead>{_b_hdr}</thead><tbody>{_b_body}</tbody>{_b_foot}</table>',
            unsafe_allow_html=True,
        )

# Show merged preview
if st.session_state.rows_a and st.session_state.rows_b:
    import datetime
    today = datetime.date.today().strftime("%Y-%m")
    rows_a_map = {r["ym"]: r for r in st.session_state.rows_a if r["ym"] < today and r["gross"] > 0}
    rows_b_map = {r["ym"]: r for r in st.session_state.rows_b if r["ym"] < today and r["gross"] > 0}
    # Union — keep all months from either statement
    all_months = sorted(set(rows_a_map) | set(rows_b_map))[-N_MONTHS:]

    if all_months:
        st.markdown('<div style="font-size:10px;letter-spacing:2px;color:#34d399;text-transform:uppercase;margin:12px 0 6px">▷ Merged Result (Union of Both Statements)</div>', unsafe_allow_html=True)
        html = ('<table class="preview-table"><thead><tr>'
                '<th style="text-align:left">Month</th>'
                '<th class="col-gross">Statement 1 Net</th>'
                '<th style="text-align:right;color:#f59e0b">Statement 2 Net</th>'
                '<th class="col-net">Combined Net</th>'
                '</tr></thead><tbody>')
        tA = tB = tC = 0
        for ym in all_months:
            rA = rows_a_map.get(ym)
            rB = rows_b_map.get(ym)
            netA = rA["eligible_income"] if rA else 0
            netB = rB["eligible_income"] if rB else 0
            combined = netA + netB
            tA += netA; tB += netB; tC += combined
            # Mark months where only one statement has data
            _src = ""
            if rA and not rB:
                _src = ' <span style="font-size:9px;color:#64748b">(Stmt 1 only)</span>'
            elif rB and not rA:
                _src = ' <span style="font-size:9px;color:#f59e0b">(Stmt 2 only)</span>'
            html += (f'<tr><td>{ym_label(ym)}{_src}</td>'
                     f'<td class="col-gross">{money(netA) if netA else "—"}</td>'
                     f'<td style="text-align:right;color:#f59e0b">{money(netB) if netB else "—"}</td>'
                     f'<td class="col-net">{money(combined)}</td></tr>')
        html += (f'</tbody><tfoot><tr>'
                 f'<td style="color:#64748b;font-size:10px;text-transform:uppercase">Total</td>'
                 f'<td class="col-gross">{money(tA)}</td>'
                 f'<td style="text-align:right;color:#f59e0b">{money(tB)}</td>'
                 f'<td class="col-net">{money(tC)}</td>'
                 f'</tr></tfoot></table>')
        html += (f'<div style="font-size:11px;color:#64748b;margin-top:6px">'
                 f'ℹ {len(all_months)} months shown — all months from either statement are included.</div>')
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
st.markdown('<div class="sel-section-title">01 — FirstCentral Credit Report &nbsp;<span style="color:#94a3b8;font-size:11px">— External obligations</span></div>', unsafe_allow_html=True)
st.markdown('<div class="sel-caption">Upload the FirstCentral consumer credit report. Closed accounts are ignored. Open performing accounts feed into monthly obligations.</div>', unsafe_allow_html=True)

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
st.markdown(f'<div class="sel-section-title">02 — Monthly Inflows (Last {N_MONTHS} Months)</div>', unsafe_allow_html=True)
st.markdown('<div class="sel-caption">Gross credits auto-filled from bank statement. Adjust deductions or add extra manual deductions below.</div>', unsafe_allow_html=True)

import datetime
today = datetime.date.today()

# Determine which rows to pre-fill
def get_prefill_rows():
    """Get last N_MONTHS completed months from merged or single statement.
    Uses UNION of both statements so that months present in only one
    statement are still included (not dropped by an intersection filter).
    """
    today_ym = today.strftime("%Y-%m")
    if st.session_state.rows_a and st.session_state.rows_b:
        rows_a_map = {r["ym"]: r for r in st.session_state.rows_a if r["ym"] < today_ym and r["gross"] > 0}
        rows_b_map = {r["ym"]: r for r in st.session_state.rows_b if r["ym"] < today_ym and r["gross"] > 0}
        # Union — include every month present in either statement
        all_months = sorted(set(rows_a_map) | set(rows_b_map))[-N_MONTHS:]
        if all_months:
            merged = []
            for ym in all_months:
                rA = rows_a_map.get(ym)
                rB = rows_b_map.get(ym)
                eiA = rA["eligible_income"] if rA else 0
                eiB = rB["eligible_income"] if rB else 0
                merged.append({
                    "ym": ym, "label": ym_label(ym),
                    "gross": eiA + eiB,
                    "deductions": 0,
                    "count": max(rA["count"] if rA else 0, rB["count"] if rB else 0),
                })
            return merged
    elif st.session_state.rows_a:
        return [r for r in st.session_state.rows_a if r["ym"] < today_ym and r["gross"] > 0][-N_MONTHS:]
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

# Build default month labels (N_MONTHS entries, oldest first)
def default_months():
    months = []
    for i in range(1, N_MONTHS + 1):
        month = today.month - (N_MONTHS + 1 - i)
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
with h1: st.markdown('<div style="font-size:12px;letter-spacing:1.5px;color:#e2e8f0;text-transform:uppercase;font-weight:800;padding-bottom:4px;border-bottom:2px solid #10b981">Month</div>', unsafe_allow_html=True)
with h2: st.markdown('<div style="font-size:12px;letter-spacing:1.5px;color:#e2e8f0;text-transform:uppercase;font-weight:800;padding-bottom:4px;border-bottom:2px solid #10b981">Gross Credit ₦</div>', unsafe_allow_html=True)
with h3: st.markdown('<div style="font-size:12px;letter-spacing:1.5px;color:#e2e8f0;text-transform:uppercase;font-weight:800;padding-bottom:4px;border-bottom:2px solid #10b981">Deductions ₦</div>', unsafe_allow_html=True)
with h4: st.markdown('<div style="font-size:12px;letter-spacing:1.5px;color:#fb923c;text-transform:uppercase;font-weight:800;padding-bottom:4px;border-bottom:2px solid #fb923c">Extra Deduction ₦</div>', unsafe_allow_html=True)
with h5: st.markdown('<div style="font-size:12px;letter-spacing:1.5px;color:#34d399;text-transform:uppercase;font-weight:800;padding-bottom:4px;border-bottom:2px solid #34d399">Net Inflow ₦</div>', unsafe_allow_html=True)
with h6: st.markdown('<div style="font-size:12px;letter-spacing:1.5px;color:#e2e8f0;text-transform:uppercase;font-weight:800;padding-bottom:4px;border-bottom:2px solid #10b981">Count</div>', unsafe_allow_html=True)

for i in range(N_MONTHS):
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
    '<div style="font-size:12px;color:#cbd5e1;font-weight:600;margin-top:8px;padding:10px 14px;'
    'background:rgba(0,0,0,.25);border-left:3px solid #fb923c;border-radius:3px;line-height:1.6">'
    '<strong style="color:#fb923c;font-size:13px">Extra Deduction</strong> — use this to manually subtract any amount you\'ve identified '
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

# ── What-if Reverse Calculator ────────────────────────────────────────────────
with st.expander("🔁  What-if: How much income is needed to qualify for ₦X?", expanded=False):
    st.markdown(
        '<div style="font-size:12px;color:#cbd5e1;font-weight:600;margin-bottom:12px;line-height:1.6">'
        'Enter a target loan amount below. The table shows the minimum monthly income '
        'required to qualify, across all tenors — using the product/location settings above.</div>',
        unsafe_allow_html=True,
    )
    wi_target = st.number_input("Target Loan Amount (₦)", min_value=0.0, value=1_000_000.0,
                                 step=50_000.0, key="wi_target")
    if wi_target > 0:
        wi_rows = []
        for _wt in range(2, 13):
            _wr = required_income_for_loan(
                target_loan=wi_target, tenor=_wt,
                location=location, product_type=prod_type,
                other_loans=other_loans,
                manual_rate_pct=manual_rate if manual_rate > 0 else None,
            )
            if _wr["ok"]:
                wi_rows.append({
                    "Tenor": f"{'▶ ' if _wt == tenor else ''}{_wt} mo{'  ◀' if _wt == tenor else ''}",
                    "Rate":             f"{_wr['rate']*100:.2f}%",
                    "Monthly PMT":      money(_wr["pmt"]),
                    "Min Monthly Income": money(_wr["required_turnover"]),
                    "DTI Used":         f"{_wr['dti']*100:.0f}%",
                })
            else:
                wi_rows.append({
                    "Tenor":              f"{_wt} mo",
                    "Rate":               "—",
                    "Monthly PMT":        "—",
                    "Min Monthly Income": _wr["reason"],
                    "DTI Used":           "—",
                })
        st.dataframe(pd.DataFrame(wi_rows), hide_index=True, use_container_width=True)
        st.markdown(
            f'<div class="sel-caption">Min Monthly Income = (PMT + other loans) ÷ DTI &nbsp;|&nbsp; '
            f'NTB note: applicable turnover uses trimmed mean — actual income may need to be higher.</div>',
            unsafe_allow_html=True,
        )

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
        # ── Build merged report rows (used by ALL download generators) ──────
        # When two statements are present, merge their per-month data so that
        # PDF/Excel/CSV downloads reflect the combined analysis, not just Stmt A.
        _today_ym = today.strftime("%Y-%m")
        _has_b    = bool(st.session_state.get("rows_b"))

        if _has_b:
            _rA_map = {r["ym"]: r
                       for r in (st.session_state.rows_a or [])
                       if r["ym"] < _today_ym and r["gross"] > 0}
            _rB_map = {r["ym"]: r
                       for r in (st.session_state.rows_b or [])
                       if r["ym"] < _today_ym and r["gross"] > 0}
            # Union — keep all months from either statement
            _all_months = sorted(set(_rA_map) | set(_rB_map))[-N_MONTHS:]
            _report_rows = []
            for _ym in _all_months:
                rA = _rA_map.get(_ym)
                rB = _rB_map.get(_ym)
                eiA = rA["eligible_income"] if rA else 0
                eiB = rB["eligible_income"] if rB else 0
                _report_rows.append({
                    "ym":            _ym,
                    "label":         ym_label(_ym),
                    "gross":         eiA + eiB,
                    "parsed_gross":  (rA.get("parsed_gross", 0) if rA else 0) + (rB.get("parsed_gross", 0) if rB else 0),
                    "eligible_income": eiA + eiB,
                    "self_transfer": (rA.get("self_transfer", 0) if rA else 0) + (rB.get("self_transfer", 0) if rB else 0),
                    "reversal":      (rA.get("reversal", 0)      if rA else 0) + (rB.get("reversal", 0)      if rB else 0),
                    "non_business":  (rA.get("non_business", 0)  if rA else 0) + (rB.get("non_business", 0)  if rB else 0),
                    "loan_disbursal":(rA.get("loan_disbursal", 0)if rA else 0) + (rB.get("loan_disbursal", 0)if rB else 0),
                    "deductions":    (rA.get("deductions", 0)    if rA else 0) + (rB.get("deductions", 0)    if rB else 0),
                    "count":         (rA.get("count", 0)         if rA else 0) + (rB.get("count", 0)         if rB else 0),
                })
            _name_a = st.session_state.name_a or ""
            _name_b = st.session_state.name_b or ""
            _bank_a = st.session_state.bank_a or ""
            _bank_b = st.session_state.bank_b or ""
            _report_name = f"{_name_a} + {_name_b}".strip(" +")
            _report_bank = f"{_bank_a} + {_bank_b}".strip(" +")
        else:
            _report_rows = [r for r in (st.session_state.rows_a or [])
                            if r["ym"] < _today_ym and r["gross"] > 0][-N_MONTHS:]
            _report_name = st.session_state.name_a or "Account Holder"
            _report_bank = st.session_state.bank_a or "Bank"

        # ── Audit src rows: combine both statements ──────────────────────
        _audit_src = (st.session_state.rows_a or []) + (
            st.session_state.rows_b or [] if _has_b else []
        )

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

        # ── Tenor Comparison Table ────────────────────────────────────────
        st.markdown("---")
        st.markdown(
            '<div style="font-size:10px;letter-spacing:2px;color:#f59e0b;'
            'text-transform:uppercase;margin-bottom:8px">Tenor Comparison — all tenors</div>',
            unsafe_allow_html=True,
        )
        _tenor_data = []
        for _t in range(2, 13):
            _tr = calculate_eligibility(
                nets=nets, counts=counts,
                location=location, product_type=prod_type,
                tenor=_t, other_loans=other_loans,
                manual_rate_percent=manual_rate if manual_rate > 0 else None,
            )
            _tenor_data.append({
                "Tenor":              f"{'▶ ' if _t == tenor else ''}{_t} mo{'  ◀' if _t == tenor else ''}",
                "Max Loan":           money(_tr["max_loan"]) if _tr["approved"] else "—",
                "Repayment / Period": money(_tr["max_repayment_display"]),
                "Rate":               pct(_tr["interest_rate"]) if _tr["interest_rate"] else "—",
                "Frequency":          _tr["repayment_frequency"],
                "Status":             "Approved" if _tr["approved"] else "Below min",
            })
        st.dataframe(
            pd.DataFrame(_tenor_data),
            hide_index=True,
            use_container_width=True,
        )

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
            today_ym = today.strftime("%Y-%m")
            for r in _audit_src:
                if r["ym"] >= today_ym or r["gross"] == 0:
                    continue
                # Self-transfers (savings round-trips) are deducted
                if r.get("self_transfer", 0) > 0:
                    audit_rows.append({
                        "Month": r["label"],
                        "Category": "Self Deposit / Savings Round-trip",
                        "Deducted": True,
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
                _xlsx_full = generate_xlsx(
                    rows         = _report_rows,
                    result       = result,
                    account_name = _report_name,
                    bank         = _report_bank,
                )
                _safe_xl = (_report_name or "report").replace(" ", "_").lower()
                _cav1, _cav2 = st.columns(2)
                with _cav1:
                    st.download_button(
                        "⬇  Download Full Report (Excel)",
                        _xlsx_full,
                        file_name=f"SEL_Report_{_safe_xl}_{datetime.date.today():%Y%m%d}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="dl_audit_xlsx",
                        use_container_width=True,
                    )
                with _cav2:
                    # Build CSV with eligibility summary header + audit rows
                    import io as _io
                    _csv_buf = _io.StringIO()
                    # -- Summary section --
                    _csv_buf.write("ELIGIBILITY SUMMARY\r\n")
                    _csv_buf.write(f"Decision,{'Approved' if result.get('approved') else 'Below Minimum'}\r\n")
                    _csv_buf.write(f"Max Loan Amount,{money(result.get('max_loan', 0))}\r\n")
                    _csv_buf.write(f"Tenor (Months),{result.get('tenor', '—')}\r\n")
                    _csv_buf.write(f"DTI,{pct(result.get('dti'))}\r\n")
                    _csv_buf.write(f"Interest Rate,{pct(result.get('interest_rate'))}\r\n")
                    _csv_buf.write(f"Repayment Frequency,{result.get('repayment_frequency', '')}\r\n")
                    _csv_buf.write(f"Repayment / Period,{money(result.get('max_repayment_display', 0))}\r\n")
                    _csv_buf.write(f"Max Total Repayment,{money(result.get('max_total_repayment', 0))}\r\n")
                    _csv_buf.write(f"Applicable Turnover,{money(result.get('applicable_turnover', 0))}\r\n")
                    _csv_buf.write(f"Total Net Income,{money(result.get('total_net', 0))}\r\n")
                    _csv_buf.write("\r\n")
                    # -- Audit rows section --
                    _csv_buf.write("CLASSIFICATION AUDIT\r\n")
                    _csv_buf.write(df.to_csv(index=False))
                    st.download_button(
                        "⬇  Download Audit (CSV)",
                        _csv_buf.getvalue().encode("utf-8"),
                        file_name="sel_classification_audit.csv",
                        mime="text/csv",
                        key="dl_audit_csv",
                        use_container_width=True,
                    )

        # ── Download Full Eligibility Report PDF ──────────────────────────
        st.markdown("---")
        _pdf_full = generate_pdf_report(
            account_name = _report_name,
            bank         = _report_bank,
            rows         = _report_rows if _report_rows else [],
            result       = result,
            req_loan     = req_loan,
        )
        _safe_full = (_report_name or "report").replace(" ", "_").lower()
        st.download_button(
            label               = "⬇  Download Full Eligibility Report (PDF)",
            data                = _pdf_full,
            file_name           = f"SEL_Report_{_safe_full}_{datetime.date.today():%Y%m%d}.pdf",
            mime                = "application/pdf",
            use_container_width = True,
            key                 = "dl_full_pdf",
        )



