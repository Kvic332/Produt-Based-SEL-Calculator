"""
SEL Analytics Tracker — lightweight SQLite event logger.
All writes are fire-and-forget: exceptions never bubble up to the app.

Events logged
─────────────
  upload           File submitted for parsing (before parse attempt)
  parse_success    Statement parsed OK
  parse_error      Statement parsing threw an exception
  eligibility_result  Eligibility calculation completed
  download         PDF / Excel / CSV downloaded

Usage
─────
  from tracker import track, admin_stats

  track("parse_success", session=sid, bank="GTBank", filename="stmt.pdf",
        txn_count=48, total=1_200_000.0)
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
from contextlib import suppress

# ── DB location — sits next to this file ─────────────────────────────────────
_DB = pathlib.Path(__file__).parent / "sel_analytics.db"


def _cx() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _init() -> None:
    with suppress(Exception):
        with _cx() as c:
            c.executescript("""
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
            """)


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
    with suppress(Exception):
        _init()
        with _cx() as c:
            c.execute(
                "INSERT INTO events (session, event, bank, filename, data) "
                "VALUES (?, ?, ?, ?, ?)",
                (session, event, bank, filename, json.dumps(kwargs, default=str)),
            )


# ── Public read API (admin only) ──────────────────────────────────────────────
def admin_stats() -> dict:
    """Return aggregated stats for the admin dashboard."""
    try:
        _init()
        with _cx() as c:

            def q(sql: str, *args):
                return [dict(r) for r in c.execute(sql, args).fetchall()]

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

            return {
                "summary":  summary,
                "daily":    daily,
                "banks":    banks,
                "errors":   errors,
                "loans":    loans,
                "sessions": sessions_row[0] if sessions_row else {},
                "rate":     rate_row[0]     if rate_row     else {},
            }
    except Exception as exc:
        return {"_error": str(exc)}
