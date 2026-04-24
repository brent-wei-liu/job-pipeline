#!/usr/bin/env python3
"""Job Pipeline — Fetch: Hacker News 'Who is Hiring?' → SQLite."""
import re, json, sys, time, argparse, logging, requests
from html import unescape
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("hn_fetch")

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn, job_exists, job_duplicate, insert_job, insert_search_run

HN_API = "https://hacker-news.firebaseio.com/v0"
TIMEOUT = 10


# ── HN API helpers ────────────────────────────────────────────────────────

def hn_get(path):
    """Fetch one HN item/user JSON. Returns dict or None on failure."""
    try:
        r = requests.get(f"{HN_API}/{path}", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("HN request failed (%s): %s", path, e)
        return None


def get_latest_hiring_thread(manual_id=None):
    """Return the story id of the most recent 'Ask HN: Who is hiring?' post."""
    if manual_id:
        return manual_id

    user = hn_get("user/whoishiring.json")
    if not user or not user.get("submitted"):
        log.error("Could not fetch whoishiring user data")
        return None

    for story_id in user["submitted"][:20]:  # Check the 20 most recent submissions
        item = hn_get(f"item/{story_id}.json")
        if not item:
            continue
        title = item.get("title", "")
        if "who is hiring" in title.lower() and item.get("type") == "story":
            log.info("Found hiring thread: %s (id=%s)", title, story_id)
            return story_id

    log.error("No 'Who is hiring?' thread found in recent submissions")
    return None


# ── Comment parsing ───────────────────────────────────────────────────────

def strip_html(text):
    """Strip HTML tags and decode entities."""
    text = re.sub(r"<p>", "\n\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return text.strip()


def extract_company_role(text):
    """
    Parse 'Company | Role | ...' convention common in HN hiring posts.
    Returns (company, position). Falls back gracefully.
    """
    # Normalise separators — some use | some use · or —
    first_line = text.split("\n")[0].strip()
    # Replace common non-pipe separators with |
    first_line = re.sub(r"\s*[·–—]\s*", " | ", first_line)

    parts = [p.strip() for p in first_line.split("|")]
    parts = [p for p in parts if p]  # drop empties

    company = parts[0] if len(parts) >= 1 else "Unknown"
    position = parts[1] if len(parts) >= 2 else "(see description)"

    # Trim noise: strip trailing parentheticals like "(Series B)" from company
    company = re.sub(r"\s*\(.*?\)\s*$", "", company).strip() or "Unknown"

    # Sanity check: if company looks like a URL or is very long, fall back
    if len(company) > 80 or company.startswith("http"):
        company = "Unknown"
    if len(position) > 120:
        position = "(see description)"

    return company, position


def parse_comment(item):
    """
    Map a raw HN comment item to a job dict.
    Returns None if the comment should be skipped (deleted, dead, no text, child reply).
    """
    if not item:
        return None
    if item.get("deleted") or item.get("dead"):
        return None
    text_raw = item.get("text", "").strip()
    if not text_raw:
        return None
    # Only top-level replies (direct children of the story) — skip nested replies.
    # Caller is responsible for only passing story.kids, but double-check via parent
    # being the story itself (handled at call site, no field to check here reliably).

    jd_text = strip_html(text_raw)
    if len(jd_text) < 50:  # Too short to be a real job post
        return None

    comment_id = item["id"]
    company, position = extract_company_role(jd_text)

    return {
        "id": f"hn_{comment_id}",
        "company": company,
        "position": position,
        "url": f"https://news.ycombinator.com/item?id={comment_id}",
        "jd_text": jd_text[:18000],  # Match LinkedIn cap
    }


# ── Main ──────────────────────────────────────────────────────────────────

def run(args):
    conn = None if args.dry_run else get_conn()

    story_id = get_latest_hiring_thread(args.thread_id)
    if not story_id:
        sys.exit(1)

    story = hn_get(f"item/{story_id}.json")
    if not story or not story.get("kids"):
        log.error("Story has no comments")
        sys.exit(1)

    kids = story["kids"]
    log.info("Thread has %d top-level comments", len(kids))

    summary = {
        "thread_id": story_id,
        "thread_title": story.get("title", ""),
        "total_comments": len(kids),
        "processed": 0,
        "accepted": 0,
        "skipped_deleted": 0,
        "skipped_dup": 0,
        "failures": 0,
    }

    processed = 0
    accepted = 0

    for comment_id in kids:
        if args.limit and processed >= args.limit:
            break

        item = hn_get(f"item/{comment_id}.json")
        if item is None:
            summary["failures"] += 1
            continue

        job = parse_comment(item)
        if job is None:
            summary["skipped_deleted"] += 1
            continue

        processed += 1

        if args.dry_run:
            log.info("  [dry-run] %s / %s — %s", job["company"], job["position"], job["url"])
            print(json.dumps({
                "company": job["company"],
                "position": job["position"],
                "url": job["url"],
                "jd_text_preview": job["jd_text"][:150],
            }, ensure_ascii=False, indent=2))
            accepted += 1
            continue

        # Dedup check
        if job_exists(conn, job["id"]):
            log.info("  ↷ %s / %s — already in DB", job["company"], job["position"])
            summary["skipped_dup"] += 1
            continue

        if job_duplicate(conn, job["company"], job["position"], job["jd_text"]):
            log.info("  ↷ %s / %s — soft duplicate", job["company"], job["position"])
            summary["skipped_dup"] += 1
            continue

        insert_job(
            conn,
            linkedin_id=job["id"],
            company=job["company"],
            position=job["position"],
            url=job["url"],
            jd_text=job["jd_text"],
            status="accepted",
            source="hackernews",
        )
        log.info("  ✓ %s / %s", job["company"], job["position"])
        accepted += 1

        time.sleep(0.1)  # gentle pacing

    if conn:
        insert_search_run(conn, f"hn_hiring_{story_id}", processed, accepted, 0)
        conn.close()

    summary["processed"] = processed
    summary["accepted"] = accepted
    log.info("Done: %d processed, %d accepted, %d skipped-dup, %d failures",
             processed, accepted, summary["skipped_dup"], summary["failures"])
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fetch HN 'Who is Hiring?' jobs")
    ap.add_argument("--thread-id", type=int, default=None,
                    help="Override HN story ID (skip auto-discovery)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Max comments to process (0 = no limit)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print jobs without writing to DB")
    run(ap.parse_args())
