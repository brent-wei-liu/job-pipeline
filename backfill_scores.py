#!/usr/bin/env python3
"""
Job Pipeline — Backfill unscored jobs in small batches.
Safe to interrupt: resumes from where it left off.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn, save_match_score, get_match_score

WORKSPACE = Path(__file__).resolve().parent.parent
RESUME_PATH = WORKSPACE / "Brent_Wei_Liu_CV.pdf"
RESUME_TXT_PATH = WORKSPACE / "job-pipeline" / "data" / "resume_text.txt"
GATEWAY_URL = os.environ.get("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")

BATCH_SIZE = 5
BATCH_DELAY = 3  # seconds between batches
CALL_DELAY = 1   # seconds between LLM calls

# ── Resume ─────────────────────────────────────────────────────────────

def load_resume_text():
    if RESUME_TXT_PATH.exists():
        return RESUME_TXT_PATH.read_text().strip()
    import subprocess
    for alt in [RESUME_PATH, Path.home() / "Downloads" / "1_0_Brent_Wei_Liu_CV.pdf"]:
        if alt.exists():
            try:
                result = subprocess.run(["pdftotext", "-layout", str(alt), "-"],
                    capture_output=True, text=True, timeout=15)
                if result.stdout.strip():
                    RESUME_TXT_PATH.parent.mkdir(parents=True, exist_ok=True)
                    RESUME_TXT_PATH.write_text(result.stdout.strip())
                    return result.stdout.strip()
            except Exception:
                pass
    return ""

# ── Gateway ────────────────────────────────────────────────────────────

def get_gateway_token():
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN")
    if token:
        return token
    config_path = Path.home() / ".openclaw" / "openclaw.json"
    if config_path.exists():
        text = config_path.read_text()
        matches = re.findall(r'''["']?token["']?\s*[:=]\s*["']([^"']+)["']''', text)
        for m in matches:
            if re.match(r'^[0-9a-f]{20,}$', m):
                return m
    return None

def llm_score(resume_text, jd_text, position, company):
    token = get_gateway_token()
    if not token:
        print("   ❌ No gateway token", file=sys.stderr)
        return None

    prompt = f"""You are a job matching expert. Score how well this resume matches the job description.

RESUME:
{resume_text[:3000]}

JOB: {position} at {company}
JD:
{jd_text[:4000]}

Respond in this exact JSON format (no markdown, no extra text):
{{
  "score": <1-10 integer>,
  "agentic_ai_relevant": <true/false>,
  "match_reasons": ["reason1", "reason2"],
  "gaps": ["gap1", "gap2"],
  "summary": "<1 sentence Chinese summary of fit>"
}}

Scoring guide:
- 9-10: Almost perfect match, directly relevant experience
- 7-8: Strong match, most requirements met
- 5-6: Decent match, some relevant experience
- 3-4: Weak match, few overlaps
- 1-2: Poor match

Focus on: technical skills match, seniority level, domain relevance.
Bonus points if the role involves agentic AI, search/ranking, or LLM systems."""

    body = json.dumps({
        "model": "openclaw:main",
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        f"{GATEWAY_URL}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                result = json.loads(resp.read())
            text = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if text.startswith("```"):
                text = re.sub(r"^```\w*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            return json.loads(text)
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
            else:
                print(f"   ❌ {e}", file=sys.stderr)
                return None

# ── Main ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Backfill match scores for unscored jobs")
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE, help=f"Jobs per batch (default {BATCH_SIZE})")
    ap.add_argument("--batch-delay", type=int, default=BATCH_DELAY, help=f"Seconds between batches (default {BATCH_DELAY})")
    ap.add_argument("--dry-run", action="store_true", help="Show jobs without scoring")
    ap.add_argument("--resume", action="store_true", help="Skip already-scored jobs (default true, use --no-resume to override)")
    ap.add_argument("--limit", type=int, default=0, help="Max jobs to score (0 = all)")
    args = ap.parse_args()

    resume_text = load_resume_text()
    if not resume_text:
        print("ERROR: No resume text found. Cannot score.", file=sys.stderr)
        sys.exit(1)
    print(f"✅ Resume loaded: {len(resume_text)} chars")

    conn = get_conn()

    # Get all unscored accepted jobs with JD text
    rows = conn.execute("""
        SELECT id, company, position, jd_text
        FROM jobs
        WHERE status = 'accepted'
          AND match_score IS NULL
          AND (jd_text IS NOT NULL AND jd_text != '')
        ORDER BY fetched_at ASC
    """).fetchall()

    # Filter out already-scored (in case match_score was set after we queried)
    rows = [r for r in rows if get_match_score(conn, r["id"]) is None]

    total = len(rows)
    if total == 0:
        print("🎉 All accepted jobs already scored!")
        conn.close()
        return

    print(f"📋 {total} unscored jobs to backfill")
    if args.dry_run:
        print("DRY RUN — listing jobs only:")
        for r in rows[:20]:
            print(f"  [{r['id']}] {r['company']} — {r['position']}")
        if total > 20:
            print(f"  ... and {total - 20} more")
        conn.close()
        return

    if args.limit > 0:
        rows = rows[:args.limit]
        print(f"🔒 Limit set to {args.limit} jobs")

    scored = 0
    failed = 0
    batches = [rows[i:i+args.batch_size] for i in range(0, len(rows), args.batch_size)]

    for batch_idx, batch in enumerate(batches):
        print(f"\n📦 Batch {batch_idx+1}/{len(batches)} ({len(batch)} jobs)")
        for r in batch:
            cached = get_match_score(conn, r["id"])
            if cached:
                print(f"  ⏭️  Already scored: {r['company']} — {r['position']} (score={cached['score']})")
                continue

            print(f"  🔄 Scoring: {r['company']} — {r['position']}...", end=" ", flush=True)
            result = llm_score(resume_text, r["jd_text"], r["position"], r["company"])

            if result:
                save_match_score(
                    conn, r["id"],
                    score=result.get("score", 0),
                    summary=result.get("summary", ""),
                    match_reasons=result.get("match_reasons"),
                    gaps=result.get("gaps"),
                    agentic_ai_relevant=result.get("agentic_ai_relevant", False),
                )
                ai_tag = " 🤖" if result.get("agentic_ai_relevant") else ""
                print(f"✅ score={result.get('score')}{ai_tag}")
                scored += 1
            else:
                print(f"  ❌ FAILED")
                failed += 1

            time.sleep(CALL_DELAY)

        print(f"  ⏳ Waiting {args.batch_delay}s before next batch...")
        time.sleep(args.batch_delay)

    # Final summary
    remaining = conn.execute("""
        SELECT COUNT(*) FROM jobs
        WHERE status='accepted' AND match_score IS NULL AND (jd_text IS NOT NULL AND jd_text != '')
    """).fetchone()[0]

    print(f"\n{'='*50}")
    print(f"✅ Scored: {scored} | ❌ Failed: {failed} | 📋 Remaining unscored (with JD): {remaining}")
    conn.close()

if __name__ == "__main__":
    main()
