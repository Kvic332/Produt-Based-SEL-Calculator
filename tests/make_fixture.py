# Copyright (c) 2026 Kenechukwu (Kvic7). All rights reserved.
# Proprietary and confidential — see LICENSE. No license granted.
"""
Fixture builder for the parser regression suite.

Usage:
    py tests/make_fixture.py "<path to statement.pdf>" <fixture_name> [pdf_password]

What it does:
 1. Parses the original PDF through the production path → golden outputs.
 2. Anonymizes the ACCOUNT HOLDER: every word of the detected account name is
    replaced with a fake name (case-preserved), and the detected account
    number is replaced digit-for-digit. Counterparty names in narrations are
    left as-is — the statement owner's identity is what's sensitive.
 3. Re-parses the anonymized text and verifies every figure still matches the
    golden outputs (guarantees anonymization didn't change parser behavior).
 4. Saves tests/fixtures/<fixture_name>.json.gz (anonymized text + expected).

The original PDF is never stored.
"""
from __future__ import annotations

import gzip
import json
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(_HERE.parent))

import bank_parser  # noqa: E402

_FAKE_WORDS = ["Tunde", "Bayo", "Adewale", "Ngozi", "Chidi", "Amaka",
               "Femi", "Yemi", "Sade", "Emeka", "Bisi", "Kunle"]


def _word_map(name: str) -> dict[str, str]:
    """Map each word of the real account name to a fake word, all cases."""
    mapping: dict[str, str] = {}
    words = [w for w in re.split(r"[\s,]+", name or "") if len(w) >= 3]
    for i, w in enumerate(dict.fromkeys(words)):       # unique, order-kept
        fake = _FAKE_WORDS[i % len(_FAKE_WORDS)]
        # Match the source word's casing exactly — some parsers anchor on
        # ALL-CAPS name lines, so changing case changes behavior.
        if w.isupper():
            fake = fake.upper()
        elif w.islower():
            fake = fake.lower()
        mapping[w] = fake
    return mapping


def _apply(text: str, mapping: dict[str, str], acct: str, fake_acct: str) -> str:
    for real, fake in mapping.items():
        text = re.sub(re.escape(real),         fake,         text)
        text = re.sub(re.escape(real.upper()), fake.upper(), text)
        text = re.sub(re.escape(real.lower()), fake.lower(), text)
        text = re.sub(re.escape(real.title()), fake.title(), text)
    if acct:
        text = text.replace(acct, fake_acct)
    return text


def _parse_with_texts(pypdf2_text: str, plumber_text: str):
    _orig = (bank_parser.extract_pdf_text,
             bank_parser.extract_pdf_text_pdfplumber,
             bank_parser.extract_pdf_text_layout)
    bank_parser.extract_pdf_text            = lambda pdf_bytes, password="": pypdf2_text
    bank_parser.extract_pdf_text_pdfplumber = lambda pdf_bytes, password="": plumber_text
    bank_parser.extract_pdf_text_layout     = lambda pdf_bytes: ""
    try:
        return bank_parser.parse_transactions(b"%PDF-1.4 fixture", "", filename="fixture.pdf")
    finally:
        (bank_parser.extract_pdf_text,
         bank_parser.extract_pdf_text_pdfplumber,
         bank_parser.extract_pdf_text_layout) = _orig


def _snapshot(buckets, bank, txns, debit_txns) -> dict:
    return {
        "bank": bank,
        "monthly": {
            ym: {k: round(float(b.get(k, 0) or 0), 2)
                 for k in ["gross", "self_transfer", "reversal", "non_business",
                           "loan_disbursal", "real_credit", "count"]}
            for ym, b in buckets.items()
        },
        "debit_total": round(sum(t["amount"] for t in debit_txns), 2),
        "debit_count": len(debit_txns),
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    pdf_path = pathlib.Path(sys.argv[1])
    name     = sys.argv[2]
    password = sys.argv[3] if len(sys.argv) > 3 else ""

    pdf_bytes = pdf_path.read_bytes()
    print(f"Reading {pdf_path.name} ({len(pdf_bytes)/1_048_576:.1f} MB)")

    # 1. Golden parse from the real PDF (real extraction)
    buckets, summary, bank, holder, txns, debit_txns = bank_parser.parse_transactions(
        pdf_bytes, password, filename=pdf_path.name)
    golden = _snapshot(buckets, bank, txns, debit_txns)
    print(f"Golden: bank={bank}, holder={holder!r}, months={len(golden['monthly'])}, "
          f"debits={golden['debit_count']}")

    # 2. Extract both text variants, then anonymize the account holder
    pypdf2_text  = bank_parser.extract_pdf_text(pdf_bytes, password)
    plumber_text = bank_parser.extract_pdf_text_pdfplumber(pdf_bytes, password)
    acct = bank_parser.extract_account_no(pypdf2_text) if hasattr(bank_parser, "extract_account_no") else ""
    if not acct:
        m = re.search(r"\b(\d{10})\b", pypdf2_text[:2000])
        acct = m.group(1) if m else ""
    fake_acct = "5" + "0123456789"[: max(len(acct) - 1, 0)] if acct else ""
    mapping   = _word_map(holder)
    print(f"Anonymizing: {mapping}  acct {acct!r} -> {fake_acct!r}")

    anon_pypdf2  = _apply(pypdf2_text,  mapping, acct, fake_acct)
    anon_plumber = _apply(plumber_text, mapping, acct, fake_acct)

    # 3. Re-parse the anonymized text and verify nothing changed
    a_buckets, _, a_bank, a_holder, a_txns, a_debits = _parse_with_texts(anon_pypdf2, anon_plumber)
    anon_snap = _snapshot(a_buckets, a_bank, a_txns, a_debits)
    if anon_snap != golden:
        print("\nERROR: anonymized parse differs from the golden parse —")
        for ym in sorted(set(golden["monthly"]) | set(anon_snap["monthly"])):
            g, a = golden["monthly"].get(ym), anon_snap["monthly"].get(ym)
            if g != a:
                print(f"  {ym}: golden={g}  anon={a}")
        for k in ["bank", "debit_total", "debit_count"]:
            if golden[k] != anon_snap[k]:
                print(f"  {k}: golden={golden[k]}  anon={anon_snap[k]}")
        print("Adjust the anonymization mapping and retry. Fixture NOT saved.")
        return 1

    # 4. Save
    out = _HERE / "fixtures" / f"{name}.json.gz"
    out.parent.mkdir(exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump({"pypdf2_text": anon_pypdf2, "plumber_text": anon_plumber,
                   "expected": golden}, f)
    print(f"\nSaved {out} ({out.stat().st_size/1024:.0f} KB) — verified identical to golden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
