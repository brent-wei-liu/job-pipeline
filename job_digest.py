#!/usr/bin/env python3
"""Job Pipeline — Digest: query SQLite, score resume-JD match via LLM, output structured data."""
import argparse, json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn, query_jobs, query_search_runs, get_stats, get_match_score, save_match_score

# ── Config ──────────────────────────────────────────────────────────────
RESUME_TXT_PATH = Path(__file__).resolve().parent / "data" / "resume_text.txt"


# ── Resume Loading ─────────────────────────────────────────────────────

def load_resume_text():
    """Load cached resume text."""
    if RESUME_TXT_PATH.exists():
        return RESUME_TXT_PATH.read_text().strip()
    return ""


# ── Note: Gateway LLM scoring removed ─────────────────────────────────
# Match scoring is now done by Hermes agent via delegate_task.
# job_digest.py only outputs cached scores from DB.


# ── Digest Command ────────────────────────────────────────────────────

def cmd_query(args):
    """Output today's jobs + stats + match scores for LLM to format into digest."""
    conn = get_conn()
    days = args.days

    accepted = query_jobs(conn, days=days, status="accepted")
    rejected = query_jobs(conn, days=days, status="rejected")
    runs = query_search_runs(conn, days=days)
    stats = get_stats(conn, days=days)

    # Load resume for matching
    resume_text = load_resume_text()

    accepted_jobs = []
    for j in accepted:
        jd = dict(j)
        job_entry = {
            "company": jd["company"],
            "position": jd["position"],
            "url": jd["url"],
            "fetched_at": jd["fetched_at"],
        }

        # Use cached match score if available
        if jd.get("jd_text"):
            cached = get_match_score(conn, jd["id"])
            if cached:
                job_entry["match_score"] = cached["score"]
                job_entry["agentic_ai_relevant"] = cached["agentic_ai_relevant"]
                job_entry["match_reasons"] = cached["match_reasons"]
                job_entry["gaps"] = cached["gaps"]
                job_entry["match_summary"] = cached["summary"]
            else:
                # Unscored: include JD text (truncated) + job ID for agent to score
                job_entry["needs_scoring"] = True
                job_entry["job_id"] = jd["id"]
                job_entry["jd_text"] = jd["jd_text"][:3000]

        accepted_jobs.append(job_entry)

    # Sort by match score (highest first), unscored at end
    accepted_jobs.sort(key=lambda x: x.get("match_score", 0), reverse=True)

    output = {
        "period_days": days,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "resume_text": resume_text[:3000] if resume_text else "",
        "accepted_jobs": accepted_jobs,
        "rejected_jobs": [
            {
                "company": dict(j)["company"],
                "position": dict(j)["position"],
                "reject_reason": dict(j)["reject_reason"],
            }
            for j in rejected
        ],
        "search_runs": [
            {
                "query": dict(r)["query"],
                "results_count": dict(r)["results_count"],
                "accepted": dict(r)["accepted"],
                "rejected": dict(r)["rejected"],
                "run_at": dict(r)["run_at"],
            }
            for r in runs
        ],
        "stats": {
            "total_accepted": stats["total_accepted"],
            "total_rejected": stats["total_rejected"],
            "top_companies": [
                {"company": dict(c)["company"], "count": dict(c)["cnt"]}
                for c in stats["top_companies"]
            ],
            "reject_reasons": [
                {"reason": dict(r)["reject_reason"], "count": dict(r)["cnt"]}
                for r in stats["reject_reasons"]
            ],
        },
    }

    # Also get all-time stats
    all_time = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='accepted'").fetchone()[0]
    all_rejected = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='rejected'").fetchone()[0]
    output["all_time"] = {"accepted": all_time, "rejected": all_rejected}

    conn.close()
    print(json.dumps(output, indent=2, ensure_ascii=False))


def cmd_stats(args):
    """Quick stats view."""
    conn = get_conn()
    stats = get_stats(conn, days=args.days)
    print(f"=== Last {args.days} days ===")
    print(f"Accepted: {stats['total_accepted']}")
    print(f"Rejected: {stats['total_rejected']}")
    print(f"\nTop companies:")
    for c in stats["top_companies"]:
        print(f"  {dict(c)['company']}: {dict(c)['cnt']}")
    print(f"\nReject reasons:")
    for r in stats["reject_reasons"]:
        print(f"  {dict(r)['reject_reason']}: {dict(r)['cnt']}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")

    q = sub.add_parser("query", help="Query jobs for digest")
    q.add_argument("days", type=int, default=1, nargs="?")

    s = sub.add_parser("stats", help="Quick stats")
    s.add_argument("days", type=int, default=7, nargs="?")

    sv = sub.add_parser("save-summary", help="Save digest summary to DB")
    sv.add_argument("date", nargs="?", default=None, help="Date (YYYY-MM-DD), defaults to today")

    sc = sub.add_parser("save-score", help="Save match score for a job (JSON from stdin)")

    se = sub.add_parser("send-email", help="Send digest body (stdin, markdown) via Gmail SMTP")
    se.add_argument("--subject", required=True)
    se.add_argument("--to", default="brent.wei.liu@gmail.com")

    args = ap.parse_args()
    if args.cmd == "query":
        cmd_query(args)
    elif args.cmd == "stats":
        cmd_stats(args)
    elif args.cmd == "save-score":
        from db import get_conn, save_match_score
        import sys
        data = json.loads(sys.stdin.read())
        conn = get_conn()
        for item in (data if isinstance(data, list) else [data]):
            save_match_score(
                conn, item["job_id"],
                score=item["score"],
                summary=item.get("summary", ""),
                match_reasons=item.get("match_reasons"),
                gaps=item.get("gaps"),
                agentic_ai_relevant=item.get("agentic_ai_relevant", False),
            )
            print(f"Saved score {item['score']} for job {item['job_id']}")
        conn.close()
    elif args.cmd == "save-summary":
        from db import get_conn, save_summary
        import sys
        content = sys.stdin.read()
        conn = get_conn()
        save_summary(conn, content, args.date)
        conn.close()
        print(f"Saved digest summary ({len(content)} chars)")
    elif args.cmd == "send-email":
        import sys
        from email_sender import send_email
        body = sys.stdin.read()
        if not body.strip():
            print(json.dumps({"error": "stdin empty"}))
            sys.exit(1)
        try:
            result = send_email(args.to, args.subject, body)
            print(json.dumps(result, ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
            sys.exit(1)
    else:
        ap.print_help()
