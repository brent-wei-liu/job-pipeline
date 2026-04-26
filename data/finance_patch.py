#!/usr/bin/env python3
"""Partial-update script: patch only the 5 financial profile fields in company_analysis.

Usage:
  echo '<json_array>' | python3 data/finance_patch.py
  cat finance_data.json | python3 data/finance_patch.py

JSON format (only company is required; any missing finance field is skipped):
[
  {
    "company": "Anthropic",
    "is_public": 0,
    "market_cap_usd": null,
    "funding_stage": "Series E",
    "total_raised_usd": 15500.0,
    "notable_investors": ["Google", "Amazon", "Lightspeed"]
  }
]
"""
import json, sys, sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "jobs.db"

raw = sys.stdin.read().strip()
if not raw:
    print("ERROR: no input on stdin", file=sys.stderr)
    sys.exit(1)

entries = json.loads(raw)
if not isinstance(entries, list):
    print("ERROR: expected JSON array", file=sys.stderr)
    sys.exit(1)

con = sqlite3.connect(str(DB_PATH))
updated = skipped = 0

for e in entries:
    company = e.get("company", "").strip()
    if not company:
        print(f"SKIP: missing company", file=sys.stderr)
        skipped += 1
        continue

    # Only patch the 5 finance fields; never touch scores/reasoning/analyzed_at
    fields = {}
    for col in ("is_public", "market_cap_usd", "funding_stage", "total_raised_usd", "notable_investors"):
        if col in e:
            val = e[col]
            if isinstance(val, list):
                val = json.dumps(val, ensure_ascii=False)
            fields[col] = val

    if not fields:
        print(f"SKIP {company}: no finance fields in entry", file=sys.stderr)
        skipped += 1
        continue

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [company]
    result = con.execute(
        f"UPDATE company_analysis SET {set_clause} WHERE company = ?", params
    )
    if result.rowcount == 0:
        print(f"WARN {company}: no matching row in DB", file=sys.stderr)
        skipped += 1
    else:
        print(f"OK   {company}: patched {list(fields.keys())}")
        updated += 1

con.commit()
con.close()
print(f"\nDone — updated={updated} skipped={skipped}")
