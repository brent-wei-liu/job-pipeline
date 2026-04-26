#!/usr/bin/env python3
"""
Job Pipeline — Company Analysis: data I/O layer for LLM-driven company scoring.

Usage:
  # List companies pending analysis (JSON output for LLM)
  python3 company_analyze.py --list-pending

  # Show stats: how many analyzed vs pending
  python3 company_analyze.py --stats

  # Write LLM results back to DB (reads JSON array from stdin)
  echo '<json_array>' | python3 company_analyze.py --save

  # Force re-analysis even if recently analyzed (with --list-pending)
  python3 company_analyze.py --list-pending --force

JSON format for --save:
[
  {
    "company": "Harvey",
    "summary": "一句话公司简介",
    "funding_score": 8,
    "revenue_score": 7,
    "moat_score": 8,
    "stage_score": 7,
    "risk_score": 3,
    "overall_score": 8,
    "reasoning": {
      "funding": "...",
      "revenue": "...",
      "moat": "...",
      "stage": "...",
      "risk": "..."
    },
    "is_public": 0,
    "market_cap_usd": null,
    "funding_stage": "Series B",
    "total_raised_usd": 206.0,
    "notable_investors": ["a16z", "Sequoia", "OpenAI Fund"],
    "analyst_model": "claude-sonnet-4-6"
  }
]
"""
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn

STALE_DAYS = 7  # Re-analyze if older than this


def cmd_stats(conn):
    total = conn.execute("SELECT COUNT(*) FROM watchlist WHERE enabled=1").fetchone()[0]
    analyzed = conn.execute("""
        SELECT COUNT(*) FROM watchlist w
        JOIN company_analysis ca ON ca.company = w.company
        WHERE w.enabled=1 AND ca.analyzed_at > datetime('now', ?)
    """, (f"-{STALE_DAYS} days",)).fetchone()[0]
    pending = total - analyzed
    print(f"Enabled companies: {total}")
    print(f"Analyzed (fresh):  {analyzed}")
    print(f"Pending analysis:  {pending}")


def cmd_list_pending(conn, force=False):
    if force:
        rows = conn.execute(
            "SELECT company, ats_type FROM watchlist WHERE enabled=1 ORDER BY company"
        ).fetchall()
    else:
        rows = conn.execute("""
            SELECT w.company, w.ats_type FROM watchlist w
            LEFT JOIN company_analysis ca ON ca.company = w.company
            WHERE w.enabled=1
              AND (ca.analyzed_at IS NULL OR ca.analyzed_at < datetime('now', ?))
            ORDER BY w.company
        """, (f"-{STALE_DAYS} days",)).fetchall()

    result = [{"company": r["company"], "ats_type": r["ats_type"]} for r in rows]
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_save(conn):
    raw = sys.stdin.read().strip()
    if not raw:
        print("ERROR: no input on stdin", file=sys.stderr)
        sys.exit(1)
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(entries, list):
        print("ERROR: expected JSON array", file=sys.stderr)
        sys.exit(1)

    saved = 0
    for e in entries:
        company = e.get("company", "").strip()
        if not company:
            print(f"SKIP: missing company field in entry", file=sys.stderr)
            continue
        reasoning = e.get("reasoning", {})
        if isinstance(reasoning, dict):
            reasoning = json.dumps(reasoning, ensure_ascii=False)
        notable_investors = e.get("notable_investors")
        if isinstance(notable_investors, list):
            notable_investors = json.dumps(notable_investors, ensure_ascii=False)
        conn.execute("""
            INSERT INTO company_analysis
                (company, summary, funding_score, revenue_score, moat_score,
                 stage_score, risk_score, overall_score, reasoning, analyzed_at, analyst_model,
                 is_public, market_cap_usd, funding_stage, total_raised_usd, notable_investors)
            VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),?,?,?,?,?,?)
            ON CONFLICT(company) DO UPDATE SET
                summary=excluded.summary,
                funding_score=excluded.funding_score,
                revenue_score=excluded.revenue_score,
                moat_score=excluded.moat_score,
                stage_score=excluded.stage_score,
                risk_score=excluded.risk_score,
                overall_score=excluded.overall_score,
                reasoning=excluded.reasoning,
                analyzed_at=excluded.analyzed_at,
                analyst_model=excluded.analyst_model,
                is_public=excluded.is_public,
                market_cap_usd=excluded.market_cap_usd,
                funding_stage=excluded.funding_stage,
                total_raised_usd=excluded.total_raised_usd,
                notable_investors=excluded.notable_investors
        """, (
            company,
            e.get("summary"),
            e.get("funding_score"),
            e.get("revenue_score"),
            e.get("moat_score"),
            e.get("stage_score"),
            e.get("risk_score"),
            e.get("overall_score"),
            reasoning,
            e.get("analyst_model"),
            e.get("is_public"),
            e.get("market_cap_usd"),
            e.get("funding_stage"),
            e.get("total_raised_usd"),
            notable_investors,
        ))
        saved += 1
    conn.commit()
    print(f"Saved {saved} company analyses")


def main():
    ap = argparse.ArgumentParser(description="Company analysis data I/O")
    ap.add_argument("--stats",        action="store_true", help="Show analysis stats")
    ap.add_argument("--list-pending", action="store_true", help="Output pending companies as JSON")
    ap.add_argument("--save",         action="store_true", help="Read JSON from stdin and write to DB")
    ap.add_argument("--force",        action="store_true", help="With --list-pending: include all, even recent")
    args = ap.parse_args()

    if not any([args.stats, args.list_pending, args.save]):
        ap.print_help()
        sys.exit(0)

    conn = get_conn()
    if args.stats:
        cmd_stats(conn)
    if args.list_pending:
        cmd_list_pending(conn, force=args.force)
    if args.save:
        cmd_save(conn)
    conn.close()


if __name__ == "__main__":
    main()
