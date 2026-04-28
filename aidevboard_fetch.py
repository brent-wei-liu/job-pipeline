#!/usr/bin/env python3
"""Job Pipeline — Fetch from aidevboard.com public API.

Aggregator site, returns jobs from many companies. Independent of watchlist
(no per-company config). Source-tagged 'aidevboard' in jobs table.
"""
import re, json, sys, subprocess, argparse, logging, urllib.parse
from html import unescape
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("aidevboard_fetch")

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn, insert_job, insert_search_run
from job_fetch import reject_by_title

API_BASE = "https://aidevboard.com/api/v1/jobs"
LEVELS = ["senior", "lead", "principal"]
PAGE_SIZE = 50
POSTED_WITHIN_DAYS = 30
CURL_TIMEOUT = 10
PAGE_CAP = 30

MGMT_RE = re.compile(
    r'\b(manager|director|vp|vice\s+president|head\s+of|chief)\b',
    re.IGNORECASE,
)

AI_KEYWORDS = [
    r'machine\s+learning', r'ML', r'AI', r'artificial\s+intelligence',
    r'deep\s+learning', r'neural',
    r'LLM', r'LLMs', r'large\s+language', r'agent', r'agentic', r'multi-?agent',
    r'RAG', r'retrieval-?augmented', r'embedding', r'vector', r'transformer',
    r'foundation\s+model', r'generative', r'multimodal', r'prompt',
    r'NLP', r'natural\s+language', r'computer\s+vision', r'speech',
    r'recommendation', r'ranking', r'personalization', r'search', r'retrieval',
    r'MLOps', r'training', r'inference', r'GPU', r'GPUs',
    r'model\s+serving', r'distributed',
]
AI_RE = re.compile(r'\b(' + '|'.join(AI_KEYWORDS) + r')\b', re.IGNORECASE)


def fetch_page(level, page):
    params = {
        "posted_within_days": POSTED_WITHIN_DAYS,
        "level": level,
        "page": page,
        "limit": PAGE_SIZE,
    }
    url = API_BASE + "?" + urllib.parse.urlencode(params)
    res = subprocess.run(
        ["curl", "-s", "--max-time", str(CURL_TIMEOUT),
         "-H", "Accept: application/json",
         "-A", "Mozilla/5.0",
         url],
        capture_output=True, text=True, timeout=CURL_TIMEOUT + 2,
    )
    if res.returncode != 0:
        raise RuntimeError(f"curl failed (level={level} page={page}): {res.stderr.strip()[:200]}")
    return json.loads(res.stdout)


def strip_html(text):
    if not text:
        return ""
    if "<" not in text:
        return unescape(text).strip()
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def classify(title, description):
    """Return (status, reject_reason) tuple for a job."""
    reason = reject_by_title(title)
    if reason:
        return "rejected", reason
    if MGMT_RE.search(title):
        return "rejected", "management_role"
    haystack = title + "\n" + (description or "")
    if not AI_RE.search(haystack):
        return "rejected", "not_ai_relevant"
    return "accepted", None


def run(args):
    summary = {
        "fetched_total": 0,
        "accepted": 0,
        "rejected": 0,
        "rejected_by_reason": {},
        "skipped_dup": 0,
        "errors": 0,
    }

    seen_ids = set()
    rows_to_insert = []

    for level in LEVELS:
        page = 1
        while True:
            try:
                resp = fetch_page(level, page)
            except Exception as e:
                log.error("fetch failed level=%s page=%d: %s", level, page, e)
                summary["errors"] += 1
                break
            jobs = resp.get("jobs", [])
            if not jobs:
                break
            for j in jobs:
                jid = j.get("id")
                if not jid or jid in seen_ids:
                    continue
                seen_ids.add(jid)
                summary["fetched_total"] += 1

                linkedin_id = f"aidevboard_{jid}"
                company = j.get("company_name") or ""
                title = j.get("title") or ""
                url = j.get("apply_url") or j.get("url") or ""
                description = strip_html(j.get("description", ""))
                if len(description) > 18000:
                    description = description[:18000] + "\n\n[Truncated]"

                status, reason = classify(title, description)
                if status == "accepted":
                    summary["accepted"] += 1
                else:
                    summary["rejected"] += 1
                    summary["rejected_by_reason"][reason] = (
                        summary["rejected_by_reason"].get(reason, 0) + 1
                    )

                rows_to_insert.append({
                    "linkedin_id": linkedin_id,
                    "company": company,
                    "position": title,
                    "url": url,
                    "jd_text": description,
                    "status": status,
                    "reject_reason": reason,
                })

                if args.limit > 0 and summary["accepted"] >= args.limit:
                    break

            if args.limit > 0 and summary["accepted"] >= args.limit:
                break
            if not resp.get("has_next"):
                break
            page += 1
            if page > PAGE_CAP:
                log.warning("page cap reached for level=%s", level)
                break

        if args.limit > 0 and summary["accepted"] >= args.limit:
            break
        log.info("Level %s done: total fetched so far=%d", level, summary["fetched_total"])

    if args.dry_run:
        log.info("DRY RUN — not writing to DB")
        log.info("Would insert %d rows (accepted=%d, rejected=%d)",
                 len(rows_to_insert), summary["accepted"], summary["rejected"])
        for r in rows_to_insert[:5]:
            log.info("  [%s] %s / %s — %s",
                     r["status"], r["company"], r["position"][:60],
                     r["reject_reason"] or "")
    else:
        conn = get_conn()
        for r in rows_to_insert:
            existing = conn.execute(
                "SELECT id FROM jobs WHERE linkedin_id = ?", (r["linkedin_id"],)
            ).fetchone()
            if existing:
                summary["skipped_dup"] += 1
                if r["status"] == "accepted":
                    summary["accepted"] -= 1
                else:
                    summary["rejected"] -= 1
                    summary["rejected_by_reason"][r["reject_reason"]] -= 1
                continue
            insert_job(
                conn,
                linkedin_id=r["linkedin_id"],
                company=r["company"],
                position=r["position"],
                url=r["url"],
                jd_text=r["jd_text"],
                status=r["status"],
                reject_reason=r["reject_reason"],
                source="aidevboard",
            )
        insert_search_run(
            conn,
            query=f"aidevboard:senior+lead+principal:{POSTED_WITHIN_DAYS}d",
            results_count=summary["fetched_total"],
            accepted=summary["accepted"],
            rejected=summary["rejected"],
        )
        conn.close()

    log.info("Done — fetched=%d accepted=%d rejected=%d skipped_dup=%d errors=%d",
             summary["fetched_total"], summary["accepted"], summary["rejected"],
             summary["skipped_dup"], summary["errors"])
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    ap.add_argument("--limit", type=int, default=0,
                    help="Cap accepted count (0 = no limit)")
    run(ap.parse_args())
