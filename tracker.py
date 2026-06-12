"""
PARSIO Analytics Tracker — event + applicant-history logger.

Storage backends
─────────────────
  • Neon / PostgreSQL  — used when a DATABASE_URL connection string is
    configured (Streamlit secret or environment variable). Data persists
    permanently and survives every redeploy.
  • SQLite (fallback)  — used automatically for local development or when
    no DATABASE_URL is set. Note: on Streamlit Cloud the SQLite file is
    EPHEMERAL and wiped on redeploy, so configure DATABASE_URL in prod.

All writes are fire-and-forget: exceptions never bubble up to the app.

Events logged
─────────────
  upload              File submitted for parsing (before parse attempt)
  parse_success       Statement parsed OK
  parse_error         Statement parsing threw an exception
  eligibility_result  Eligibility calculation completed
  download            PDF / Excel / CSV downloaded
  signin              Officer signed in for the day

Usage
─────
  from tracker import track, admin_stats
  track("parse_success", session=sid, bank="GTBank", filename="stmt.pdf",
        txn_count=48, total=1_200_000.0)
"""
# Copyright (c) 2026 Kenechukwu (Kvic7). All rights reserved.
# Proprietary and confidential — see LICENSE. No license granted.

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import sqlite3
from contextlib import suppress

# ── Resolve Postgres connection string (Neon) ────────────────────────────────
# Priority: Streamlit secret  →  environment variable.  Empty → SQLite fallback.
def _resolve_dsn() -> str:
    try:
        import streamlit as st
        # st.secrets may not support .get() in all versions — use [] with try/except
        try:
            v = st.secrets["DATABASE_URL"]
            if v:
                return str(v)
        except (KeyError, AttributeError, FileNotFoundError):
            pass
    except Exception:
        pass
    return os.environ.get("DATABASE_URL", "")


_PG_DSN = _resolve_dsn()
_USE_PG = False
psycopg2 = None  # type: ignore

if _PG_DSN:
    try:
        import psycopg2  # type: ignore
        import psycopg2.extras  # type: ignore
        _USE_PG = True
    except Exception:
        _USE_PG = False

STORAGE_BACKEND = "Neon / PostgreSQL" if _USE_PG else "SQLite (ephemeral)"

# ── SQLite location — sits next to this file (fallback only) ──────────────────
_DB = pathlib.Path(__file__).parent / "sel_analytics.db"


def _now_iso() -> str:
    """UTC timestamp matching the legacy SQLite default format."""
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


# ── Connection helpers ────────────────────────────────────────────────────────
def _connect():
    if _USE_PG:
        return psycopg2.connect(_PG_DSN)          # type: ignore
    conn = sqlite3.connect(str(_DB), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _adapt(sql: str) -> str:
    """Translate the shared SQL dialect to the active backend.

    Tokens:
      JGET(col, key)   → extract a JSON value as text
      JTRUE(col, key)  → boolean predicate: the JSON value equals true
    Placeholders:
      ?  →  %s  (Postgres)
    """
    if _USE_PG:
        sql = re.sub(r"JTRUE\(\s*(\w+)\s*,\s*(\w+)\s*\)",
                     r"((\1::jsonb)->>'\2') = 'true'", sql)
        sql = re.sub(r"JGET\(\s*(\w+)\s*,\s*(\w+)\s*\)",
                     r"(\1::jsonb)->>'\2'", sql)
        sql = sql.replace("?", "%s")
    else:
        sql = re.sub(r"JTRUE\(\s*(\w+)\s*,\s*(\w+)\s*\)",
                     r"json_extract(\1,'$.\2') = 1", sql)
        sql = re.sub(r"JGET\(\s*(\w+)\s*,\s*(\w+)\s*\)",
                     r"json_extract(\1,'$.\2')", sql)
    return sql


def _execute(sql: str, params: tuple = ()) -> None:
    """Run one write statement. Fire-and-forget — never raises."""
    with suppress(Exception):
        _init()
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(_adapt(sql), params)
            conn.commit()
        finally:
            conn.close()


def _query(sql: str, params: tuple = ()) -> list[dict]:
    """Run one read query → list[dict]. Returns [] on error."""
    try:
        _init()
        conn = _connect()
        try:
            if _USE_PG:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)  # type: ignore
            else:
                cur = conn.cursor()
            cur.execute(_adapt(sql), params)
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


# ── Schema ────────────────────────────────────────────────────────────────────
_INIT_DONE = False


def _init() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    with suppress(Exception):
        conn = _connect()
        try:
            cur = conn.cursor()
            if _USE_PG:
                cur.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id        BIGSERIAL PRIMARY KEY,
                    ts        TEXT,
                    session   TEXT DEFAULT '',
                    event     TEXT NOT NULL,
                    bank      TEXT DEFAULT '',
                    filename  TEXT DEFAULT '',
                    data      TEXT DEFAULT '{}'
                );""")
                cur.execute("CREATE INDEX IF NOT EXISTS ix_ev  ON events(event);")
                cur.execute("CREATE INDEX IF NOT EXISTS ix_ts  ON events(ts);")
                cur.execute("CREATE INDEX IF NOT EXISTS ix_ses ON events(session);")
                cur.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id           BIGSERIAL PRIMARY KEY,
                    ts           TEXT,
                    session      TEXT DEFAULT '',
                    account_name TEXT DEFAULT '',
                    bank         TEXT DEFAULT '',
                    avg_income   DOUBLE PRECISION DEFAULT 0,
                    max_loan     DOUBLE PRECISION DEFAULT 0,
                    tenor        INTEGER DEFAULT 0,
                    location     TEXT DEFAULT '',
                    product      TEXT DEFAULT '',
                    approved     INTEGER DEFAULT 0
                );""")
                cur.execute("CREATE INDEX IF NOT EXISTS ix_hist_name ON history(account_name);")
                cur.execute("CREATE INDEX IF NOT EXISTS ix_hist_ts   ON history(ts);")
                cur.execute("""
                CREATE TABLE IF NOT EXISTS blacklist (
                    id           BIGSERIAL PRIMARY KEY,
                    ts           TEXT,
                    entry_type   TEXT DEFAULT 'name',
                    value        TEXT NOT NULL,
                    reason       TEXT DEFAULT '',
                    added_by     TEXT DEFAULT ''
                );""")
                cur.execute("CREATE INDEX IF NOT EXISTS ix_bl_val ON blacklist(value);")
            else:
                cur.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
                    session   TEXT  DEFAULT '',
                    event     TEXT  NOT NULL,
                    bank      TEXT  DEFAULT '',
                    filename  TEXT  DEFAULT '',
                    data      TEXT  DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS ix_ev  ON events(event);
                CREATE INDEX IF NOT EXISTS ix_ts  ON events(ts);
                CREATE INDEX IF NOT EXISTS ix_ses ON events(session);

                CREATE TABLE IF NOT EXISTS history (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts           TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
                    session      TEXT DEFAULT '',
                    account_name TEXT DEFAULT '',
                    bank         TEXT DEFAULT '',
                    avg_income   REAL DEFAULT 0,
                    max_loan     REAL DEFAULT 0,
                    tenor        INTEGER DEFAULT 0,
                    location     TEXT DEFAULT '',
                    product      TEXT DEFAULT '',
                    approved     INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS ix_hist_name ON history(account_name);
                CREATE INDEX IF NOT EXISTS ix_hist_ts   ON history(ts);

                CREATE TABLE IF NOT EXISTS blacklist (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts         TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
                    entry_type TEXT DEFAULT 'name',
                    value      TEXT NOT NULL,
                    reason     TEXT DEFAULT '',
                    added_by   TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS ix_bl_val ON blacklist(value);
                """)
            conn.commit()
            _INIT_DONE = True
        finally:
            conn.close()


# ── Public write API ──────────────────────────────────────────────────────────
def track(
    event: str,
    *,
    session: str = "",
    bank: str = "",
    filename: str = "",
    **kwargs,
) -> None:
    """Fire-and-forget event log. Never raises."""
    _execute(
        "INSERT INTO events (ts, session, event, bank, filename, data) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (_now_iso(), session, event, bank, filename,
         json.dumps(kwargs, default=str)),
    )


# ── Applicant history API ─────────────────────────────────────────────────────
def save_history(
    account_name: str,
    bank: str,
    avg_income: float,
    max_loan: float,
    tenor: int,
    location: str,
    product: str,
    approved: bool,
    session: str = "",
) -> None:
    """Save one assessment to the history table. Never raises."""
    _execute(
        "INSERT INTO history "
        "(ts, session, account_name, bank, avg_income, max_loan, tenor, location, product, approved) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (_now_iso(), session, account_name, bank, float(avg_income or 0),
         float(max_loan or 0), int(tenor or 0), location, product, int(approved)),
    )


def get_history(account_name: str) -> list[dict]:
    """Return past assessments for the same account name, newest first."""
    return _query(
        "SELECT ts, bank, avg_income, max_loan, tenor, location, product, approved "
        "FROM history WHERE lower(trim(account_name)) = lower(trim(?)) "
        "ORDER BY ts DESC LIMIT 10",
        (account_name,),
    )


# ── Cross-statement applicant memory ─────────────────────────────────────────
def get_applicant_assessments(account_no: str = "", applicant: str = "") -> list[dict]:
    """Full past assessments for one applicant from the eligibility audit log.

    Matches by account number first (strongest identity), falling back to the
    applicant name. Returns the rich JSON fields tracked with every
    eligibility_result — income, DTI, obligations — so a new assessment can be
    diffed against the borrower's history. Newest first.
    """
    rows: list[dict] = []
    if account_no:
        rows = _query(
            "SELECT ts, bank, "
            "  JGET(data,officer)         AS officer, "
            "  JGET(data,applicant)       AS applicant, "
            "  JTRUE(data,approved)       AS approved, "
            "  JGET(data,max_loan)        AS max_loan, "
            "  JGET(data,tenor)           AS tenor, "
            "  JGET(data,dti)             AS dti, "
            "  JGET(data,total_net)       AS total_net, "
            "  JGET(data,monthly_repay_obligation) AS monthly_repay_obligation, "
            "  JGET(data,debit_total)     AS debit_total "
            "FROM events WHERE event='eligibility_result' "
            "AND JGET(data,account_no) = ? "
            "ORDER BY ts DESC LIMIT 10",
            (account_no,),
        )
    if not rows and applicant:
        rows = _query(
            "SELECT ts, bank, "
            "  JGET(data,officer)         AS officer, "
            "  JGET(data,applicant)       AS applicant, "
            "  JTRUE(data,approved)       AS approved, "
            "  JGET(data,max_loan)        AS max_loan, "
            "  JGET(data,tenor)           AS tenor, "
            "  JGET(data,dti)             AS dti, "
            "  JGET(data,total_net)       AS total_net, "
            "  JGET(data,monthly_repay_obligation) AS monthly_repay_obligation, "
            "  JGET(data,debit_total)     AS debit_total "
            "FROM events WHERE event='eligibility_result' "
            "AND lower(JGET(data,applicant)) = lower(?) "
            "ORDER BY ts DESC LIMIT 10",
            (applicant.strip(),),
        )
    return rows


# ── Officer notes ─────────────────────────────────────────────────────────────
def save_officer_note(applicant: str, account_no: str, note: str,
                      officer: str = "", session: str = "") -> None:
    """Persist a free-text officer note against an applicant. Never raises."""
    if not (note or "").strip():
        return
    _execute(
        "INSERT INTO events (ts, session, event, bank, filename, data) "
        "VALUES (?, ?, 'officer_note', '', '', ?)",
        (_now_iso(), session,
         json.dumps({"applicant": applicant, "account_no": account_no,
                     "note": note.strip()[:2000], "officer": officer},
                    default=str)),
    )


def get_officer_notes(account_no: str = "", applicant: str = "") -> list[dict]:
    """Past officer notes for an applicant (account no first, name fallback)."""
    rows: list[dict] = []
    if account_no:
        rows = _query(
            "SELECT ts, JGET(data,officer) AS officer, JGET(data,note) AS note "
            "FROM events WHERE event='officer_note' "
            "AND JGET(data,account_no) = ? ORDER BY ts DESC LIMIT 10",
            (account_no,),
        )
    if not rows and applicant:
        rows = _query(
            "SELECT ts, JGET(data,officer) AS officer, JGET(data,note) AS note "
            "FROM events WHERE event='officer_note' "
            "AND lower(JGET(data,applicant)) = lower(?) ORDER BY ts DESC LIMIT 10",
            (applicant.strip(),),
        )
    return rows


# ── Full audit-log export (admin only) ────────────────────────────────────────
def export_audit_csv() -> str:
    """Return the complete eligibility audit trail as CSV text.

    One row per eligibility_result event with the fields most useful for
    compliance / fraud review: timestamp, officer, applicant, account no,
    bank, decision, max loan, tenor, DTI, product, location.
    """
    import csv
    import io as _io

    rows = _query(
        "SELECT ts, bank, "
        "  JGET(data,officer)     AS officer, "
        "  JGET(data,applicant)   AS applicant, "
        "  JGET(data,account_no)  AS account_no, "
        "  JTRUE(data,approved)   AS approved, "
        "  JGET(data,max_loan)    AS max_loan, "
        "  JGET(data,tenor)       AS tenor, "
        "  JGET(data,dti)         AS dti, "
        "  JGET(data,product)     AS product, "
        "  JGET(data,location)    AS location, "
        "  JGET(data,total_net)   AS total_net, "
        "  session "
        "FROM events WHERE event='eligibility_result' "
        "ORDER BY ts DESC"
    )

    buf = _io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Timestamp (UTC)", "Officer", "Applicant", "Account No",
                "Bank", "Decision", "Max Loan", "Tenor", "DTI %",
                "Product", "Location", "Total Net Income", "Session"])
    for r in rows:
        _appr = r.get("approved")
        # approved arrives as bool/1/0/'true' depending on backend
        _is_appr = _appr in (True, 1, "1", "true", "t")
        w.writerow([
            r.get("ts", ""),
            r.get("officer") or "",
            r.get("applicant") or "",
            r.get("account_no") or "",
            r.get("bank", "") or "",
            "Approved" if _is_appr else "Below Min",
            r.get("max_loan") or "",
            r.get("tenor") or "",
            r.get("dti") or "",
            r.get("product") or "",
            r.get("location") or "",
            r.get("total_net") or "",
            r.get("session", "") or "",
        ])
    return buf.getvalue()


# ── Public read API (admin only) ──────────────────────────────────────────────
def admin_stats() -> dict:
    """Return aggregated stats for the admin dashboard."""
    try:
        _init()
        conn = _connect()
        try:
            if _USE_PG:
                def q(sql: str, *args):
                    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)  # type: ignore
                    cur.execute(_adapt(sql), args)
                    return [dict(r) for r in cur.fetchall()]
            else:
                def q(sql: str, *args):
                    cur = conn.cursor()
                    cur.execute(_adapt(sql), args)
                    return [dict(r) for r in cur.fetchall()]

            summary = q(
                "SELECT event, COUNT(*) AS total "
                "FROM events GROUP BY event ORDER BY total DESC"
            )

            daily = q(
                "SELECT substr(ts,1,10) AS day, COUNT(*) AS uploads "
                "FROM events WHERE event IN ('parse_success','parse_error') "
                "GROUP BY day ORDER BY day DESC LIMIT 30"
            )

            banks = q(
                "SELECT bank, COUNT(*) AS cnt "
                "FROM events WHERE event='parse_success' AND bank != '' "
                "GROUP BY bank ORDER BY cnt DESC"
            )

            errors = q(
                "SELECT ts, session, bank, filename, data "
                "FROM events WHERE event='parse_error' "
                "ORDER BY ts DESC LIMIT 50"
            )

            # Statements that strained the worker (slow parse or huge txn
            # count) — the kind that can OOM the shared Streamlit Cloud host.
            heavy = q(
                "SELECT ts, session, bank, filename, data "
                "FROM events WHERE event='parsed' "
                "ORDER BY ts DESC LIMIT 200"
            )

            loans = q(
                "SELECT ts, session, bank, data "
                "FROM events WHERE event='eligibility_result' "
                "ORDER BY ts DESC LIMIT 100"
            )

            sessions_row = q(
                "SELECT "
                "  COUNT(DISTINCT session) AS total_sessions, "
                "  COUNT(DISTINCT CASE WHEN event='parse_success'      THEN session END) AS parsed, "
                "  COUNT(DISTINCT CASE WHEN event='eligibility_result' THEN session END) AS completed "
                "FROM events"
            )

            rate_row = q(
                "SELECT "
                "  SUM(CASE WHEN event='parse_success' THEN 1 ELSE 0 END) AS ok, "
                "  SUM(CASE WHEN event='parse_error'   THEN 1 ELSE 0 END) AS err "
                "FROM events WHERE event IN ('parse_success','parse_error')"
            )

            # ── Portfolio analytics (Feature 11) ─────────────────────────
            approval_by_bank = q(
                "SELECT bank, "
                "  SUM(CASE WHEN JTRUE(data,approved) THEN 1 ELSE 0 END) AS approved, "
                "  COUNT(*) AS total "
                "FROM events WHERE event='eligibility_result' AND bank != '' "
                "GROUP BY bank ORDER BY total DESC"
            )

            loans_by_month = q(
                "SELECT substr(ts,1,7) AS month, "
                "  ROUND(AVG(CAST(JGET(data,max_loan) AS REAL))) AS avg_loan, "
                "  COUNT(*) AS count, "
                "  SUM(CASE WHEN JTRUE(data,approved) THEN 1 ELSE 0 END) AS approved "
                "FROM events WHERE event='eligibility_result' "
                "GROUP BY month ORDER BY month"
            )

            # Daily breakdown — last 62 days (covers current + previous full month)
            # Use Python-computed cutoff so the same SQL works on both SQLite and PostgreSQL
            _cutoff_day = (datetime.datetime.utcnow() - datetime.timedelta(days=62)).strftime("%Y-%m-%d")
            loans_by_day = q(
                "SELECT substr(ts,1,10) AS day, "
                "  ROUND(AVG(CAST(JGET(data,max_loan) AS REAL))) AS avg_loan, "
                "  COUNT(*) AS count, "
                "  SUM(CASE WHEN JTRUE(data,approved) THEN 1 ELSE 0 END) AS approved "
                "FROM events WHERE event='eligibility_result' "
                "  AND substr(ts,1,10) >= ? "
                "GROUP BY day ORDER BY day",
                (_cutoff_day,),
            )

            rejection_reasons = q(
                "SELECT "
                "  COALESCE(JGET(data,product),'—')  AS product, "
                "  COALESCE(JGET(data,location),'—') AS location, "
                "  COUNT(*) AS count "
                "FROM events "
                "WHERE event='eligibility_result' "
                "  AND NOT JTRUE(data,approved) "
                "GROUP BY product, location ORDER BY count DESC LIMIT 15"
            )

            download_formats = q(
                "SELECT COALESCE(JGET(data,fmt),'unknown') AS fmt, "
                "  COUNT(*) AS count "
                "FROM events WHERE event='download' "
                "GROUP BY fmt ORDER BY count DESC"
            )

            officer_activity = q(
                "SELECT "
                "  COALESCE(JGET(data,officer),'Unknown') AS officer, "
                "  COUNT(*) AS assessments, "
                "  SUM(CASE WHEN JTRUE(data,approved) THEN 1 ELSE 0 END) AS approved, "
                "  MAX(substr(ts,1,10)) AS last_active, "
                "  ROUND(CAST(AVG(CASE WHEN JTRUE(data,approved) "
                "    THEN CAST(JGET(data,max_loan) AS REAL) END) AS NUMERIC)) AS avg_loan_approved, "
                "  ROUND(CAST(AVG(CAST(JGET(data,dti) AS REAL)) AS NUMERIC), 1) AS avg_dti "
                "FROM events WHERE event='eligibility_result' "
                "  AND COALESCE(JGET(data,officer),'') != '' "
                "GROUP BY officer ORDER BY assessments DESC"
            )

            # ── Sign-in log: every officer who opened the app, whether or
            #    not they ran a calculation.  Newest first, last 500 rows.
            signin_log = q(
                "SELECT ts, "
                "  COALESCE(JGET(data,officer),'—') AS officer, "
                "  session "
                "FROM events WHERE event='signin' "
                "ORDER BY ts DESC LIMIT 500"
            )

            # ── Today's sign-in count (UTC date) ─────────────────────────
            signin_today = q(
                "SELECT COUNT(*) AS cnt FROM events "
                "WHERE event='signin' AND substr(ts,1,10) = substr(?,1,10)",
                (_now_iso(),),
            )

            # ── Per-officer sign-in summary ───────────────────────────────
            signin_summary = q(
                "SELECT "
                "  COALESCE(JGET(data,officer),'Unknown') AS officer, "
                "  COUNT(*)             AS total_signins, "
                "  MAX(substr(ts,1,10)) AS last_seen "
                "FROM events WHERE event='signin' "
                "  AND COALESCE(JGET(data,officer),'') != '' "
                "GROUP BY officer ORDER BY last_seen DESC"
            )

            return {
                "backend":           STORAGE_BACKEND,
                "summary":           summary,
                "daily":             daily,
                "banks":             banks,
                "errors":            errors,
                "heavy":             heavy,
                "loans":             loans,
                "sessions":          sessions_row[0]    if sessions_row    else {},
                "rate":              rate_row[0]        if rate_row        else {},
                "approval_by_bank":  approval_by_bank,
                "loans_by_month":    loans_by_month,
                "loans_by_day":      loans_by_day,
                "rejection_reasons": rejection_reasons,
                "download_formats":  download_formats,
                "officer_activity":  officer_activity,
                "signin_log":        signin_log,
                "signin_today":      int((signin_today[0].get("cnt") or 0) if signin_today else 0),
                "signin_summary":    signin_summary,
            }
        finally:
            conn.close()
    except Exception as exc:
        return {"_error": str(exc)}


# ── Blacklist / Watchlist API ─────────────────────────────────────────────────

def save_blacklist_entries(entries: list[dict], added_by: str = "") -> int:
    """Upsert blacklist entries. Each dict must have 'entry_type', 'value', optionally 'reason'.
    Returns the number of new entries inserted. Never raises."""
    inserted = 0
    for e in entries:
        val = (e.get("value") or "").strip()
        if not val:
            continue
        _execute(
            "INSERT INTO blacklist (ts, entry_type, value, reason, added_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (_now_iso(), e.get("entry_type", "name"), val,
             e.get("reason", ""), added_by),
        )
        inserted += 1
    return inserted


def get_blacklist() -> list[dict]:
    """Return all blacklist entries, newest first."""
    return _query(
        "SELECT id, ts, entry_type, value, reason, added_by "
        "FROM blacklist ORDER BY ts DESC LIMIT 2000"
    )


def delete_blacklist_entry(entry_id: int) -> None:
    """Remove one blacklist row by id. Never raises."""
    _execute("DELETE FROM blacklist WHERE id = ?", (entry_id,))


def clear_blacklist() -> None:
    """Remove all blacklist entries. Never raises."""
    _execute("DELETE FROM blacklist", ())


def check_blacklist(name: str, account_no: str) -> list[dict]:
    """Return any blacklist entries that match the applicant name or account number.
    Matching is case-insensitive substring for names, exact for account numbers."""
    if not name and not account_no:
        return []
    results = []
    all_entries = _query("SELECT id, entry_type, value, reason FROM blacklist")
    name_lc = name.strip().lower() if name else ""
    acct_clean = re.sub(r"\s+", "", account_no or "")
    for e in all_entries:
        val = (e.get("value") or "").strip()
        etype = e.get("entry_type", "name")
        if etype == "account_no":
            if acct_clean and re.sub(r"\s+", "", val) == acct_clean:
                results.append(e)
        else:  # name — substring match
            if name_lc and val.lower() in name_lc or (val.lower() and name_lc in val.lower()):
                results.append(e)
    return results


# ── Duplicate Application Detection ──────────────────────────────────────────

def check_duplicate_application(account_no: str, current_officer: str,
                                  days: int = 30) -> list[dict]:
    """Return eligibility_result events for the same account_no filed by a
    DIFFERENT officer within the last `days` days. Never raises."""
    if not account_no or not account_no.strip():
        return []
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days)
              ).strftime("%Y-%m-%dT%H:%M:%S")
    rows = _query(
        "SELECT ts, JGET(data,officer) AS officer, JGET(data,applicant) AS applicant, "
        "       JGET(data,bank) AS bank, JGET(data,max_loan) AS max_loan "
        "FROM events "
        "WHERE event = 'eligibility_result' "
        "  AND JGET(data,account_no) = ? "
        "  AND ts >= ? "
        "ORDER BY ts DESC LIMIT 20",
        (account_no.strip(), cutoff),
    )
    officer_lc = (current_officer or "").strip().lower()
    return [r for r in rows if (r.get("officer") or "").strip().lower() != officer_lc]
