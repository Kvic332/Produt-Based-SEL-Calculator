"""
SEL Analytics Tracker — event + applicant-history logger.

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
    with suppress(Exception):
        import streamlit as st
        v = st.secrets.get("DATABASE_URL", "")
        if v:
            return str(v)
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
                "  MAX(substr(ts,1,10)) AS last_active "
                "FROM events WHERE event='eligibility_result' "
                "  AND COALESCE(JGET(data,officer),'') != '' "
                "GROUP BY officer ORDER BY assessments DESC"
            )

            return {
                "backend":           STORAGE_BACKEND,
                "summary":           summary,
                "daily":             daily,
                "banks":             banks,
                "errors":            errors,
                "loans":             loans,
                "sessions":          sessions_row[0]    if sessions_row    else {},
                "rate":              rate_row[0]        if rate_row        else {},
                "approval_by_bank":  approval_by_bank,
                "loans_by_month":    loans_by_month,
                "rejection_reasons": rejection_reasons,
                "download_formats":  download_formats,
                "officer_activity":  officer_activity,
            }
        finally:
            conn.close()
    except Exception as exc:
        return {"_error": str(exc)}
