#!/usr/bin/env python3
"""
Job Pipeline — Backfill: list unscored jobs for external LLM scoring.

Usage:
  # List unscored jobs (human-readable)
  python3 backfill_scores.py --dry-run

  # Output JSON for Cowork/LLM to score (default batch of 20)
  python3 backfill_scores.py --json --limit 20

  # Score is written back via:
  echo '<json_array>' | python3 job_digest.py save-score

Safe to interrupt: resumes from where it left off (only queries match_score IS NULL).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn, get_match_score

RESUME_MD_PATH = Path(__file__).resolve().parent / "data" / "resume.md"
RESUME_TXT_PATH = Path(__file__).resolve().parent / "data" / "resume_text.txt"


def load_resume_text():
    if RESUME_MD_PATH.exists():
        return RESUME_MD_PATH.read_text().strip()
    if RESUME_TXT_PATH.exists():
        return RESUME_TXT_PATH.read_text().strip()
    return ""


def main():
    ap = argparse.ArgumentParser(description="List unscored jobs for backfill scoring")
    ap.add_argument("--limit", type=int, default=20, help="Max jobs to output (default 20)")
    ap.add_argument("--dry-run", action="store_true", help="Show unscored jobs summary")
    ap.add_argument("--json", action="store_true", help="Output JSON with JD text for LLM scoring")
    ap.add_argument("--stats", action="store_true", help="Show scoring stats only")
    args = ap.parse_args()

    conn = get_conn()

    # Stats
    total_accepted = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='accepted'").fetchone()[0]
    scored = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='accepted' AND match_score IS NOT NULL").fetchone()[0]
    unscored_with_jd = conn.execute("""
        SELECT COUNT(*) FROM jobs
        WHERE status='accepted' AND match_score IS NULL
        AND (jd_text IS NOT NULL AND jd_text != '')
    """).fetchone()[0]
    unscored_no_jd = conn.execute("""
        SELECT COUNT(*) FROM jobs
        WHERE status='accepted' AND match_score IS NULL
        AND (jd_text IS NULL OR jd_text = '')
    """).fetchone()[0]

    if args.stats:
        print(f"Accepted jobs: {total_accepted}")
        print(f"Scored: {scored}")
        print(f"Unscored (with JD): {unscored_with_jd}")
        print(f"Unscored (no JD): {unscored_no_jd}")
        conn.close()
        return

    # Get unscored jobs with JD
    rows = conn.execute("""
        SELECT id, company, position, jd_text
        FROM jobs
        WHERE status = 'accepted'
          AND match_score IS NULL
          AND (jd_text IS NOT NULL AND jd_text != '')
        ORDER BY fetched_at DESC
    """).fetchall()

    if not rows:
        print("All accepted jobs with JD text are already scored.")
        conn.close()
        return

    if args.dry_run:
        print(f"Unscored jobs: {len(rows)} (showing first {min(args.limit, len(rows))})\n")
        for r in rows[:args.limit]:
            print(f"  [{r['id']}] {r['company']} — {r['position']} ({len(r['jd_text'])} chars JD)")
        if len(rows) > args.limit:
            print(f"\n  ... and {len(rows) - args.limit} more")
        conn.close()
        return

    if args.json:
        batch = rows[:args.limit]
        output = []
        for r in batch:
            output.append({
                "id": r["id"],
                "company": r["company"],
                "position": r["position"],
                "jd_text": r["jd_text"][:4000],
            })
        print(json.dumps(output, indent=2, ensure_ascii=False))
        conn.close()
        return

    # Default: show stats + dry-run
    print(f"Scoring progress: {scored}/{total_accepted} ({unscored_with_jd} remaining with JD, {unscored_no_jd} without JD)")
    print(f"\nUse --json --limit N to output a batch for Cowork scoring")
    print(f"Use --dry-run to list unscored jobs")
    print(f"Use --stats for numbers only")
    conn.close()


if __name__ == "__main__":
    main()
