# Copyright (c) 2026 Kenechukwu (Kvic7). All rights reserved.
# Proprietary and confidential — see LICENSE. No license granted.
"""
Parser regression suite.

Every fixture in tests/fixtures/ is an anonymized bank statement (extracted
text, never the PDF) plus the verified-correct expected outputs. The suite
re-parses each fixture through the REAL production path
(bank_parser.parse_transactions) with the PDF-text extractors stubbed, and
asserts every monthly figure still matches to the kobo.

Run:
    py -m pytest tests/ -v          (with pytest)
    py tests/test_parser_regression.py    (standalone, no pytest needed)

Add a fixture for every statement that ever misparsed:
    py tests/make_fixture.py "<pdf path>" <fixture_name>
"""
from __future__ import annotations

import gzip
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(_HERE.parent))   # import bank_parser from repo root

FIXTURES = sorted((_HERE / "fixtures").glob("*.json.gz"))

# Money figures must match to the kobo (2dp tolerance for float noise)
TOL = 0.01

_BUCKET_KEYS = ["gross", "self_transfer", "reversal", "non_business",
                "loan_disbursal", "real_credit", "count"]


def _load(path: pathlib.Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _run_fixture(fx: dict) -> list[str]:
    """Parse one fixture through the production dispatcher; return failures."""
    import bank_parser

    # Stub the PDF-text extractors with the fixture's stored text so the
    # real detection + parsing + classification path runs unchanged.
    _orig = (bank_parser.extract_pdf_text,
             bank_parser.extract_pdf_text_pdfplumber,
             bank_parser.extract_pdf_text_layout)
    bank_parser.extract_pdf_text           = lambda pdf_bytes, password="": fx["pypdf2_text"]
    bank_parser.extract_pdf_text_pdfplumber = lambda pdf_bytes, password="": fx["plumber_text"]
    bank_parser.extract_pdf_text_layout    = lambda pdf_bytes: ""
    try:
        buckets, summary, bank, name, txns, debit_txns = bank_parser.parse_transactions(
            b"%PDF-1.4 fixture", "", filename="fixture.pdf",
        )
    finally:
        (bank_parser.extract_pdf_text,
         bank_parser.extract_pdf_text_pdfplumber,
         bank_parser.extract_pdf_text_layout) = _orig

    exp = fx["expected"]
    fails: list[str] = []

    if bank != exp["bank"]:
        fails.append(f"bank detection: expected {exp['bank']!r}, got {bank!r}")

    for ym, exp_b in exp["monthly"].items():
        got_b = buckets.get(ym)
        if got_b is None:
            fails.append(f"{ym}: month missing from parse output")
            continue
        for k in _BUCKET_KEYS:
            e, g = float(exp_b.get(k, 0) or 0), float(got_b.get(k, 0) or 0)
            if abs(e - g) > TOL:
                fails.append(f"{ym}.{k}: expected {e:,.2f}, got {g:,.2f}")

    extra_months = set(buckets) - set(exp["monthly"])
    if extra_months:
        fails.append(f"unexpected extra months parsed: {sorted(extra_months)}")

    got_debit_total = sum(t["amount"] for t in debit_txns)
    if abs(got_debit_total - float(exp["debit_total"])) > TOL:
        fails.append(f"debit_total: expected {exp['debit_total']:,.2f}, "
                     f"got {got_debit_total:,.2f}")
    if len(debit_txns) != exp["debit_count"]:
        fails.append(f"debit_count: expected {exp['debit_count']}, got {len(debit_txns)}")

    return fails


# ── pytest entry points (one test per fixture) ────────────────────────────────
def _make_test(path: pathlib.Path):
    def _test():
        fails = _run_fixture(_load(path))
        assert not fails, f"{path.stem}:\n  " + "\n  ".join(fails)
    return _test


for _p in FIXTURES:
    globals()[f"test_{_p.name.split('.')[0]}"] = _make_test(_p)


# ── standalone runner ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not FIXTURES:
        print("No fixtures found in tests/fixtures/ — create one with make_fixture.py")
        sys.exit(1)
    failed = 0
    for p in FIXTURES:
        fails = _run_fixture(_load(p))
        status = "PASS" if not fails else "FAIL"
        print(f"[{status}] {p.name.split('.')[0]}")
        for f in fails:
            print(f"        {f}")
        failed += bool(fails)
    print(f"\n{len(FIXTURES) - failed}/{len(FIXTURES)} fixtures passed")
    sys.exit(1 if failed else 0)
