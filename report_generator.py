"""
PDF Report Generator — SEL Loan Eligibility Calculator
Uses ReportLab Platypus engine.
White-background professional layout with Emerald + Gold brand colours.
"""
from __future__ import annotations

import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ── Brand palette (print-friendly: white background, emerald + gold accents) ──
C_EMERALD  = colors.HexColor("#10b981")
C_GREEN    = colors.HexColor("#34d399")
C_GOLD     = colors.HexColor("#f59e0b")
C_DARK     = colors.HexColor("#0d2818")
C_SURFACE  = colors.HexColor("#f0fdf4")
C_ROW_ALT  = colors.HexColor("#f7fdfb")
C_BORDER   = colors.HexColor("#d1fae5")
C_TEXT     = colors.HexColor("#0f172a")
C_MUTED    = colors.HexColor("#64748b")
C_RED      = colors.HexColor("#ef4444")
C_WHITE    = colors.white


# ── Helpers ────────────────────────────────────────────────────────────────────
def _money(v: float) -> str:
    """Full NGN amount (Naira sign avoided — Helvetica lacks the glyph)."""
    return f"NGN {v:,.0f}"


def _compact(v: float) -> str:
    """Compact amount for summary chips."""
    if v >= 1_000_000:
        return f"NGN {v / 1_000_000:.1f}M"
    return f"NGN {v / 1_000:.0f}K"


def _pct(v) -> str:
    return "--" if v is None else f"{v * 100:.2f}%"


# ── Style factory ─────────────────────────────────────────────────────────────
def _ps(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


# ── Public API ─────────────────────────────────────────────────────────────────
def generate_pdf_report(
    account_name: str,
    bank: str,
    rows: list[dict],           # monthly_analysis() output
    result: dict | None = None, # calculate_eligibility() output (optional)
    req_loan: float = 0,
) -> bytes:
    """
    Build a PDF report and return raw bytes.

    Parameters
    ----------
    account_name : Account holder name from bank statement
    bank         : Detected bank label (e.g. 'Fidelity', 'OPay_v2')
    rows         : Monthly analysis rows — only last 6 completed months used
    result       : Eligibility result dict — omit for statement-only report
    req_loan     : Requested loan amount (optional, used only if result present)
    """
    buf = BytesIO()
    W   = A4[0] - 36 * mm   # usable width

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=14 * mm,  bottomMargin=14 * mm,
    )
    story: list = []
    today_str = datetime.date.today().strftime("%d %B %Y")

    # ── Styles ────────────────────────────────────────────────────────────────
    ST_TITLE  = _ps("title",  fontName="Helvetica-Bold", fontSize=20, textColor=C_TEXT,    leading=24)
    ST_SUB    = _ps("sub",    fontName="Helvetica",      fontSize=10, textColor=C_MUTED,   leading=13)
    ST_SEC    = _ps("sec",    fontName="Helvetica-Bold", fontSize=8,  textColor=C_EMERALD, leading=10,
                              spaceBefore=10, spaceAfter=5)
    ST_FOOT   = _ps("foot",   fontName="Helvetica",      fontSize=8,  textColor=C_MUTED,   leading=11,
                              alignment=TA_CENTER)

    # ── Top accent bar ────────────────────────────────────────────────────────
    story.append(Table([[""]], colWidths=[W], rowHeights=[3],
                        style=TableStyle([("BACKGROUND", (0,0), (-1,-1), C_EMERALD)])))
    story.append(Spacer(1, 6 * mm))

    # ── Title block ───────────────────────────────────────────────────────────
    story.append(Paragraph("SEL Loan Eligibility Report", ST_TITLE))
    story.append(Spacer(1, 1 * mm))
    story.append(Paragraph(f"Generated {today_str}", ST_SUB))
    story.append(Spacer(1, 4 * mm))

    # ── Account info grid ─────────────────────────────────────────────────────
    import datetime as _dt
    today_ym     = _dt.date.today().strftime("%Y-%m")
    display_rows = [r for r in rows if r["ym"] < today_ym and r["gross"] > 0][-6:]
    period_start = display_rows[0]["label"]  if display_rows else "—"
    period_end   = display_rows[-1]["label"] if display_rows else "—"
    bank_label   = bank.replace("_", " ") if bank else "—"

    info_data = [
        ["Account Holder", account_name or "—", "Bank", bank_label],
        ["Statement Period", f"{period_start} – {period_end}", "Report Date", today_str],
    ]
    _hw  = W / 2
    info = Table(info_data, colWidths=[28*mm, _hw - 28*mm, 25*mm, _hw - 25*mm])
    info.setStyle(TableStyle([
        ("FONTNAME",        (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",        (2, 0), (2, -1), "Helvetica-Bold"),
        ("TEXTCOLOR",       (0, 0), (0, -1), C_MUTED),
        ("TEXTCOLOR",       (2, 0), (2, -1), C_MUTED),
        ("TEXTCOLOR",       (1, 0), (-1, -1), C_TEXT),
        ("FONTSIZE",        (0, 0), (-1, -1), 9),
        ("TOPPADDING",      (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",   (0, 0), (-1, -1), 3),
    ]))
    story.append(info)
    story.append(Spacer(1, 3 * mm))
    story.append(HRFlowable(width=W, thickness=1, color=C_BORDER))

    # ── Monthly Breakdown Table ───────────────────────────────────────────────
    story.append(Paragraph("MONTHLY INCOME BREAKDOWN", ST_SEC))

    has_self = any(r.get("self_transfer", 0) > 0 for r in display_rows)
    has_rev  = any(r.get("reversal", 0)       > 0 for r in display_rows)
    has_nb   = any(r.get("non_business", 0)   > 0 for r in display_rows)
    has_loan = any(r.get("loan_disbursal", 0) > 0 for r in display_rows)

    headers = ["Month", "Gross Inflow"]
    if has_self: headers.append("Self Deposits")
    if has_rev:  headers.append("Reversals")
    if has_nb:   headers.append("Non-Business")
    if has_loan: headers.append("Loan Disbursals")
    headers.append("Eligible Income")
    n_cols = len(headers)

    t_gross = t_self = t_rev = t_nb = t_loan = t_net = 0.0
    tbl_data = [headers]
    for r in display_rows:
        t_gross += r["gross"]
        t_self  += r.get("self_transfer", 0)
        t_rev   += r.get("reversal", 0)
        t_nb    += r.get("non_business", 0)
        t_loan  += r.get("loan_disbursal", 0)
        t_net   += r["eligible_income"]
        row = [r["label"], _money(r["gross"])]
        if has_self: row.append(_money(r["self_transfer"]) if r.get("self_transfer", 0) > 0 else "—")
        if has_rev:  row.append(f"-{_money(r['reversal'])}"        if r.get("reversal", 0)       > 0 else "—")
        if has_nb:   row.append(f"-{_money(r['non_business'])}"    if r.get("non_business", 0)   > 0 else "—")
        if has_loan: row.append(f"-{_money(r['loan_disbursal'])}"  if r.get("loan_disbursal", 0) > 0 else "—")
        row.append(_money(r["eligible_income"]))
        tbl_data.append(row)

    # Totals row
    totals = ["TOTAL", _money(t_gross)]
    if has_self: totals.append(_money(t_self) if t_self > 0 else "—")
    if has_rev:  totals.append(f"-{_money(t_rev)}"  if t_rev  > 0 else "—")
    if has_nb:   totals.append(f"-{_money(t_nb)}"   if t_nb   > 0 else "—")
    if has_loan: totals.append(f"-{_money(t_loan)}" if t_loan > 0 else "—")
    totals.append(_money(t_net))
    tbl_data.append(totals)

    # Distribute column widths: month fixed, eligible fixed, rest equal
    mid_cols = n_cols - 2
    mid_w    = (W - 22 * mm - 35 * mm) / max(mid_cols, 1)
    col_ws   = [22 * mm] + [mid_w] * mid_cols + [35 * mm]

    bt = Table(tbl_data, colWidths=col_ws, repeatRows=1)
    n_data = len(tbl_data)
    bt.setStyle(TableStyle([
        # Header
        ("BACKGROUND",     (0, 0), (-1, 0),    C_DARK),
        ("TEXTCOLOR",      (0, 0), (-1, 0),    C_WHITE),
        ("FONTNAME",       (0, 0), (-1, 0),    "Helvetica-Bold"),
        ("FONTSIZE",       (0, 0), (-1, 0),    8),
        ("ALIGN",          (0, 0), (-1, 0),    "CENTER"),
        ("TOPPADDING",     (0, 0), (-1, 0),    6),
        ("BOTTOMPADDING",  (0, 0), (-1, 0),    6),
        # Data rows
        ("FONTNAME",       (0, 1), (-1, n_data - 2), "Helvetica"),
        ("FONTSIZE",       (0, 1), (-1, n_data - 2), 9),
        ("TOPPADDING",     (0, 1), (-1, n_data - 2), 5),
        ("BOTTOMPADDING",  (0, 1), (-1, n_data - 2), 5),
        ("ALIGN",          (0, 1), (0, n_data - 2),  "LEFT"),
        ("ALIGN",          (1, 1), (-1, n_data - 2), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, n_data - 2), [C_WHITE, C_ROW_ALT]),
        ("TEXTCOLOR",      (-1, 1), (-1, n_data - 2), C_EMERALD),
        # Totals row
        ("BACKGROUND",     (0, -1), (-1, -1), C_SURFACE),
        ("FONTNAME",       (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE",       (0, -1), (-1, -1), 9),
        ("ALIGN",          (0, -1), (0, -1),  "LEFT"),
        ("ALIGN",          (1, -1), (-1, -1), "RIGHT"),
        ("TEXTCOLOR",      (-1, -1), (-1, -1), C_EMERALD),
        ("TOPPADDING",     (0, -1), (-1, -1), 6),
        ("BOTTOMPADDING",  (0, -1), (-1, -1), 6),
        ("LINEABOVE",      (0, -1), (-1, -1), 1.5, C_EMERALD),
        # Grid
        ("GRID",           (0, 0), (-1, -1), 0.5, C_BORDER),
        ("LINEBELOW",      (0, 0), (-1, 0),  1.5, C_EMERALD),
    ]))
    story.append(bt)

    # ── Income Summary chips ──────────────────────────────────────────────────
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("INCOME SUMMARY", ST_SEC))

    eligible_vals = [r["eligible_income"] for r in display_rows]
    avg6  = sum(eligible_vals) / len(eligible_vals) if eligible_vals else 0
    avg3  = sum(eligible_vals[-3:]) / min(3, len(eligible_vals)) if eligible_vals else 0
    delta = eligible_vals[-1] - eligible_vals[0] if len(eligible_vals) >= 2 else 0
    trend_str = (("UP " + _compact(abs(delta)))   if delta > 0
                 else ("DOWN " + _compact(abs(delta))) if delta < 0
                 else "STABLE")

    sum_data = [[
        "6-Month Avg",  _compact(avg6),
        "3-Month Avg",  _compact(avg3),
        "Income Trend", trend_str,
    ]]
    cw3 = W / 3
    st_tbl = Table(sum_data,
                   colWidths=[24*mm, cw3 - 24*mm, 24*mm, cw3 - 24*mm, 24*mm, cw3 - 24*mm])
    st_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_SURFACE),
        ("FONTNAME",      (0, 0), (0, -1),  "Helvetica-Bold"),
        ("FONTNAME",      (2, 0), (2, -1),  "Helvetica-Bold"),
        ("FONTNAME",      (4, 0), (4, -1),  "Helvetica-Bold"),
        ("TEXTCOLOR",     (0, 0), (0, -1),  C_MUTED),
        ("TEXTCOLOR",     (2, 0), (2, -1),  C_MUTED),
        ("TEXTCOLOR",     (4, 0), (4, -1),  C_MUTED),
        ("FONTNAME",      (1, 0), (1, -1),  "Helvetica-Bold"),
        ("FONTSIZE",      (1, 0), (1, -1),  11),
        ("TEXTCOLOR",     (1, 0), (1, -1),  C_EMERALD),
        ("FONTNAME",      (3, 0), (3, -1),  "Helvetica-Bold"),
        ("FONTSIZE",      (3, 0), (3, -1),  11),
        ("TEXTCOLOR",     (3, 0), (3, -1),  C_GOLD),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("FONTSIZE",      (5, 0), (5, -1),  10),
        ("TEXTCOLOR",     (5, 0), (5, -1),
         C_EMERALD if delta >= 0 else C_RED),
        ("FONTNAME",      (5, 0), (5, -1),  "Helvetica-Bold"),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("GRID",          (0, 0), (-1, -1), 0.5, C_BORDER),
    ]))
    story.append(st_tbl)

    # ── Eligibility Result (optional) ─────────────────────────────────────────
    if result:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("ELIGIBILITY RESULT", ST_SEC))

        approved  = result.get("approved", False)
        verdict   = "APPROVED" if approved else "BELOW MINIMUM"
        v_bg      = colors.HexColor("#f0fdf4") if approved else colors.HexColor("#fef2f2")
        v_border  = C_EMERALD if approved else C_RED
        v_text    = colors.HexColor("#065f46") if approved else colors.HexColor("#991b1b")
        tick      = "✓" if approved else "✗"
        verdict_p = Paragraph(
            f'<font name="Helvetica-Bold" size="13" color="{"#065f46" if approved else "#991b1b"}">'
            f'{tick}  {verdict}</font>',
            _ps("vp", leading=18),
        )
        story.append(Table(
            [[verdict_p]],
            colWidths=[W],
            style=TableStyle([
                ("BACKGROUND",    (0, 0), (-1, -1), v_bg),
                ("TOPPADDING",    (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING",   (0, 0), (-1, -1), 14),
                ("LINEBEFORE",    (0, 0), (-1, -1), 4, v_border),
            ]),
        ))
        story.append(Spacer(1, 3 * mm))

        freq       = result.get("repayment_frequency", "—") or "—"
        tenor_val  = result.get("tenor")
        tenor_disp = f"{tenor_val} months" if tenor_val else "—"
        res_rows = [
            ["Max Loan Amount",     _money(result.get("max_loan", 0)),
             "DTI",                 _pct(result.get("dti"))],
            ["Interest Rate",       _pct(result.get("interest_rate")),
             "Repayment Frequency", freq],
            ["Repayment / Period",  _money(result.get("max_repayment_display", 0)),
             "Applicable Turnover", _money(result.get("applicable_turnover", 0))],
            ["Total Net Income",    _money(result.get("total_net", 0)),
             "Tenor",               tenor_disp],
        ]
        if req_loan > 0 and "requested" in result:
            req = result["requested"]
            res_rows[-1] = [
                "Total Net Income", _money(result.get("total_net", 0)),
                "Tenor",            tenor_disp,
            ]
            res_rows.append([
                "Requested Loan",  _money(req_loan),
                "Requested DTI",   _pct(req.get("dti")),
            ])

        qw = W / 4
        rt = Table(res_rows, colWidths=[qw * 0.8, qw * 1.2, qw * 0.8, qw * 1.2])
        rt.setStyle(TableStyle([
            ("FONTNAME",        (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME",        (2, 0), (2, -1), "Helvetica-Bold"),
            ("TEXTCOLOR",       (0, 0), (0, -1), C_MUTED),
            ("TEXTCOLOR",       (2, 0), (2, -1), C_MUTED),
            ("TEXTCOLOR",       (1, 0), (-1, -1), C_TEXT),
            ("FONTSIZE",        (0, 0), (-1, -1), 9),
            # Highlight max loan
            ("FONTNAME",        (1, 0), (1, 0),   "Helvetica-Bold"),
            ("FONTSIZE",        (1, 0), (1, 0),   12),
            ("TEXTCOLOR",       (1, 0), (1, 0),   C_EMERALD),
            ("TOPPADDING",      (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",   (0, 0), (-1, -1), 5),
            ("ROWBACKGROUNDS",  (0, 0), (-1, -1), [C_WHITE, C_ROW_ALT]),
            ("LINEBELOW",       (0, 0), (-1, -2), 0.5, C_BORDER),
        ]))
        story.append(rt)

        # ── Repayment Schedule ────────────────────────────────────────────
        if result.get("approved") and result.get("interest_rate") and result.get("max_loan", 0) > 0:
            _loan_amt = float(result["max_loan"])
            _m_rate   = float(result["interest_rate"])
            _m_pmt    = float(result.get("max_repayment_monthly", 0))
            _n_per    = int(result.get("tenor", 6))
            _freq     = result.get("repayment_frequency", "Monthly")

            story.append(Spacer(1, 4 * mm))
            story.append(Paragraph("REPAYMENT SCHEDULE", ST_SEC))

            _freq_note = "Weekly payments (schedule shows monthly equivalent)" if _freq == "Weekly" else "Monthly payments"
            story.append(Paragraph(
                f'Loan: {_money(_loan_amt)}  |  Rate: {_pct(_m_rate)}/month  |  {_freq_note}',
                _ps("sched_note", fontName="Helvetica", fontSize=8,
                    textColor=C_MUTED, leading=11, spaceAfter=4),
            ))

            # Build amortization rows
            _sched_data = [["Period", "Opening Balance", "Payment", "Interest", "Principal", "Closing Balance"]]
            _bal = _loan_amt
            _total_pmt = _total_int = _total_prin = 0.0
            for _p in range(1, _n_per + 1):
                _int   = _bal * _m_rate
                _prin  = _m_pmt - _int
                _close = max(_bal - _prin, 0.0)
                _total_pmt  += _m_pmt
                _total_int  += _int
                _total_prin += _prin
                _sched_data.append([
                    str(_p),
                    _money(_bal),
                    _money(_m_pmt),
                    _money(_int),
                    _money(_prin),
                    _money(_close),
                ])
                _bal = _close
            _sched_data.append(["TOTAL", "—", _money(_total_pmt), _money(_total_int), _money(_total_prin), "—"])

            _n_s = len(_sched_data)
            _cw_s = [10 * mm] + [(W - 10 * mm) / 5] * 5
            _st = Table(_sched_data, colWidths=_cw_s, repeatRows=1)
            _st.setStyle(TableStyle([
                # Header
                ("BACKGROUND",     (0, 0),  (-1, 0),         C_DARK),
                ("TEXTCOLOR",      (0, 0),  (-1, 0),         C_WHITE),
                ("FONTNAME",       (0, 0),  (-1, 0),         "Helvetica-Bold"),
                ("FONTSIZE",       (0, 0),  (-1, 0),         7),
                ("ALIGN",          (0, 0),  (-1, 0),         "CENTER"),
                ("TOPPADDING",     (0, 0),  (-1, 0),         5),
                ("BOTTOMPADDING",  (0, 0),  (-1, 0),         5),
                # Data rows
                ("FONTNAME",       (0, 1),  (-1, _n_s - 2),  "Helvetica"),
                ("FONTSIZE",       (0, 1),  (-1, _n_s - 2),  8),
                ("TOPPADDING",     (0, 1),  (-1, _n_s - 2),  4),
                ("BOTTOMPADDING",  (0, 1),  (-1, _n_s - 2),  4),
                ("ALIGN",          (0, 1),  (0, -1),         "CENTER"),
                ("ALIGN",          (1, 1),  (-1, -1),        "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1),  (-1, _n_s - 2),  [C_WHITE, C_ROW_ALT]),
                ("TEXTCOLOR",      (2, 1),  (2, _n_s - 2),   C_EMERALD),  # payment
                ("TEXTCOLOR",      (3, 1),  (3, _n_s - 2),   C_RED),      # interest
                ("TEXTCOLOR",      (5, 1),  (5, _n_s - 2),   C_MUTED),    # closing
                # Totals row
                ("BACKGROUND",     (0, -1), (-1, -1),        C_SURFACE),
                ("FONTNAME",       (0, -1), (-1, -1),        "Helvetica-Bold"),
                ("FONTSIZE",       (0, -1), (-1, -1),        8),
                ("LINEABOVE",      (0, -1), (-1, -1),        1.5, C_EMERALD),
                ("TEXTCOLOR",      (2, -1), (2, -1),         C_EMERALD),
                ("TEXTCOLOR",      (3, -1), (3, -1),         C_RED),
                # Grid
                ("GRID",           (0, 0),  (-1, -1),        0.5, C_BORDER),
                ("LINEBELOW",      (0, 0),  (-1, 0),         1.5, C_EMERALD),
            ]))
            story.append(_st)

            _cost_pct = (_total_int / _loan_amt * 100) if _loan_amt else 0
            story.append(Spacer(1, 2 * mm))
            story.append(Paragraph(
                f'Total cost of credit: {_money(_total_int)} '
                f'({_cost_pct:.1f}% of principal) over {_n_per} months.',
                _ps("cost_note", fontName="Helvetica", fontSize=8,
                    textColor=C_RED, leading=10),
            ))

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 6 * mm))
    story.append(HRFlowable(width=W, thickness=1, color=C_BORDER))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        f"Generated by SEL Loan Eligibility Calculator  |  {today_str}  |  "
        f"Built by Kenechukwu Kvic7 (TM)",
        ST_FOOT,
    ))

    doc.build(story)
    return buf.getvalue()
