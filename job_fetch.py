#!/usr/bin/env python3
"""Job Pipeline — Fetch: LinkedIn search → SQLite + Notion."""
import re, json, sys, time, random, argparse, logging, requests
from pathlib import Path
from urllib.parse import quote
from html import unescape

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("job_fetch")

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn, job_exists, job_duplicate, insert_job, insert_search_run

# ── Config ──────────────────────────────────────────────────────────────
ROLE_QUERIES = [
    "agentic AI engineer",
    "machine learning engineer",
    "ML engineer",
    "AI engineer mid",
    "machine learning engineer mid level",
]

MAX_ACCEPT = 0  # 0 = no limit, accept all qualifying jobs
PAGES_PER_QUERY = 4  # Fetch up to 4 pages (0, 25, 50, 75) = ~100 results per query

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


# ── LinkedIn parsing ───────────────────────────────────────────────────
def parse_job_cards(html):
    jobs = []
    for m in re.finditer(r"<li>(.*?)</li>", html, re.S):
        s = m.group(1)
        idm = re.search(r"jobPosting:(\d+)", s)
        hrefm = re.search(r'href="([^"]*linkedin\.com/jobs/view/[^"]+)"', s)
        titlem = re.search(r"<h3[^>]*>\s*(.*?)\s*</h3>", s, re.S)
        compm = re.search(r'job-search-card-subtitle"[^>]*>\s*(.*?)\s*</a>', s, re.S)
        if not (idm and hrefm and titlem and compm):
            continue
        jobs.append({
            "id": idm.group(1),
            "url": unescape(hrefm.group(1)).replace("&amp;", "&"),
            "position": re.sub(r"<[^>]+>", "", unescape(titlem.group(1))).strip(),
            "company": re.sub(r"<[^>]+>", "", unescape(compm.group(1))).strip(),
        })
    return jobs


def extract_jd_text(job_html):
    m = re.search(r'<div class="show-more-less-html__markup[^>]*>([\s\S]*?)</div>', job_html)
    if not m:
        return ""
    txt = m.group(1)
    txt = re.sub(r"<script[\s\S]*?</script>", "", txt)
    txt = re.sub(r"<style[\s\S]*?</style>", "", txt)
    txt = txt.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    txt = re.sub(r"</p>", "\n\n", txt)
    txt = re.sub(r"</li>", "\n", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = unescape(txt)
    lines = [ln.strip() for ln in txt.splitlines()]
    noisy = []
    for ln in lines:
        l = ln.lower()
        if not ln:
            noisy.append("")
            continue
        if re.search(r"\b\d+[+,]?\s+applicants?\b", l):
            continue
        if re.search(r"\b(posted|reposted|\d+\s+(day|days|hour|hours|week|weeks)\s+ago)\b", l):
            continue
        if l in {"about the job", "job description"}:
            continue
        noisy.append(ln)
    txt = "\n".join(noisy)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    if len(txt) > 18000:
        txt = txt[:18000] + "\n\n[Truncated]"
    return txt


def jd_to_children(jd_text):
    if not jd_text.strip():
        return []
    chunks = []
    for para in [p.strip() for p in jd_text.split("\n\n") if p.strip()]:
        while len(para) > 1800:
            chunks.append(para[:1800])
            para = para[1800:]
        chunks.append(para)
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": c}}]},
        }
        for c in chunks[:80]
    ]


# ── Rejection rules ────────────────────────────────────────────────────
def reject_by_title(position):
    """Fast rejection based on job title only (no JD needed)."""
    pos_lower = position.lower()

    # Seniority too low
    for pat in ["intern", "new grad", "entry level", "entry-level", "junior"]:
        if pat in pos_lower:
            return "seniority_too_low"

    # Irrelevant role type
    irrelevant_roles = [
        "frontend", "front end", "front-end",
        "data scientist", "data analyst", "data analytics",
        "product manager", "product designer",
        "devops", "site reliability", "sre",
        "qa engineer", "test engineer",
    ]
    for pat in irrelevant_roles:
        if pat in pos_lower:
            return "irrelevant_role"

    return None


def reject_reason(jd, position):
    """Full rejection check including JD text."""
    # Title-based checks first
    title_reject = reject_by_title(position)
    if title_reject:
        return title_reject

    txt = (jd + "\n" + position).lower()
    pos_lower = position.lower()

    # Green card / US citizen required
    gc_patterns = [
        "green card", "us citizen", "u.s. citizen", "united states citizen",
        "permanent resident", "must be authorized to work in the u",
        "clearance required", "security clearance",
        "us persons only", "u.s. persons",
    ]
    for pat in gc_patterns:
        if pat in txt:
            return "citizenship_greencard"

    # PhD only — but not "master's or phd", "ms/phd", "bs/ms/phd" etc.
    if "phd" in txt:
        # Check if PhD is listed as an alternative (or/slash patterns)
        phd_alternative = re.search(
            r"(master|ms|m\.s|bachelor|bs|b\.s).{0,30}(or|\/|\|).{0,10}phd", txt
        )
        phd_required_alone = re.search(
            r"phd\s*(required|is required|only|must have|necessary|mandatory)", txt
        )
        if phd_required_alone and not phd_alternative:
            return "phd_only"

    # Experience > 13 years
    exp_match = re.findall(r"(\d+)\+?\s*(?:years?|yrs?)\s*(?:of)?\s*(?:experience|exp)", txt)
    for y in exp_match:
        if int(y) > 13:
            return "exp_over_13"

    return None


# ── Main ────────────────────────────────────────────────────────────────
def run(args):
    conn = get_conn()

    summary = {
        "searched": 0, "accepted": 0, "rejected": 0,
        "rejected_by_reason": {},
        "skipped_dup": 0,
        "failures": [],
        "queries": [],
    }

    seen = set()
    accepted_jobs = []

    log.info("Starting fetch run (max_accept=%s)", args.max_accept or "unlimited")

    for qi, q in enumerate(ROLE_QUERIES, 1):
        log.info("── Query %d/%d: \"%s\" ──", qi, len(ROLE_QUERIES), q)
        query_accepted = 0
        query_rejected = 0
        query_results = 0
        all_cards = []

        # Paginate through multiple pages
        for page in range(PAGES_PER_QUERY):
            start = (args.start_page + page) * 25
            url = (
                f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                f"?keywords={quote(q)}"
                f"&location={quote('San Francisco Bay Area')}"
                f"&f_TPR=r2592000"
                f"&sortBy=DD&start={start}"
            )

            try:
                html = requests.get(url, headers={"User-Agent": UA}, timeout=25).text
            except Exception as e:
                log.error("Search failed: %s (page %d)", e, page)
                summary["failures"].append({"stage": "search", "query": q, "page": page, "error": str(e)})
                break

            cards = parse_job_cards(html)
            if not cards:
                break  # No more results
            all_cards.extend(cards)
            time.sleep(0.5)  # Be polite between pages

        query_results = len(all_cards)
        summary["searched"] += len(all_cards)
        log.info("  Found %d cards", query_results)

        for j in all_cards:
            if j["id"] in seen:
                continue
            seen.add(j["id"])

            # Dedup: check if already in DB
            existing = conn.execute(
                "SELECT id, jd_text, status FROM jobs WHERE linkedin_id = ?", (j["id"],)
            ).fetchone()
            if existing:
                if existing[1]:  # Has JD already → fully processed, skip
                    summary["skipped_dup"] += 1
                    continue
                # No JD yet (was 403'd last time) → will retry JD fetch below
                log.info("  ↻ %s / %s — backfilling JD", j["company"], j["position"])
                needs_jd_backfill = True
                existing_db_id = existing[0]
            else:
                needs_jd_backfill = False
                existing_db_id = None

            # Phase 1: Title-based rejection (no JD fetch needed)
            if not needs_jd_backfill:
                title_reason = reject_by_title(j["position"])
                if title_reason:
                    log.info("  ✗ %s / %s — rejected (%s)", j["company"], j["position"], title_reason)
                    insert_job(conn, j["id"], j["company"], j["position"], j["url"],
                               jd_text="", status="rejected", reject_reason=title_reason, source="linkedin")
                    summary["rejected"] += 1
                    query_rejected += 1
                    summary["rejected_by_reason"][title_reason] = summary["rejected_by_reason"].get(title_reason, 0) + 1
                    continue

            # Phase 2: Fetch JD (only for title-passed or JD-backfill jobs)
            time.sleep(random.uniform(2, 5))  # Anti-detection delay
            try:
                resp = requests.get(
                    f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{j['id']}",
                    headers={"User-Agent": UA}, timeout=25
                )
                if resp.status_code == 403:
                    log.warning("  ⚠ %s / %s — JD fetch 403 blocked", j["company"], j["position"])
                    summary["failures"].append({"stage": "jd_fetch", "job_id": j["id"], "error": "403 blocked"})
                    if not needs_jd_backfill:
                        # New job, save without JD
                        insert_job(conn, j["id"], j["company"], j["position"], j["url"],
                                   jd_text="", status="accepted", source="linkedin")
                        accepted_jobs.append(j)
                        summary["accepted"] += 1
                        query_accepted += 1
                    continue
                jd_html = resp.text
            except Exception as e:
                summary["failures"].append({"stage": "jd_fetch", "job_id": j["id"], "error": str(e)})
                continue

            jd_text = extract_jd_text(jd_html)

            # Backfill: update existing job with JD
            if needs_jd_backfill:
                reason = reject_reason(jd_html, j["position"])
                if reason:
                    conn.execute("UPDATE jobs SET jd_text = ?, status = 'rejected', reject_reason = ? WHERE id = ?",
                                 (jd_text, reason, existing_db_id))
                else:
                    conn.execute("UPDATE jobs SET jd_text = ? WHERE id = ?", (jd_text, existing_db_id))
                conn.commit()
                summary["backfilled"] = summary.get("backfilled", 0) + 1
                continue

            # Skip if same company+position+JD already exists (re-posts)
            if job_duplicate(conn, j["company"], j["position"], jd_text):
                summary["skipped_dup"] += 1
                continue
            reason = reject_reason(jd_html, j["position"])

            if reason:
                # Store rejected job in SQLite too
                log.info("  ✗ %s / %s — rejected (%s)", j["company"], j["position"], reason)
                insert_job(conn, j["id"], j["company"], j["position"], j["url"],
                           jd_text=jd_text, status="rejected", reject_reason=reason, source="linkedin")
                summary["rejected"] += 1
                query_rejected += 1
                summary["rejected_by_reason"][reason] = summary["rejected_by_reason"].get(reason, 0) + 1
                continue

            # Store in SQLite
            log.info("  ✓ %s / %s — accepted", j["company"], j["position"])
            insert_job(conn, j["id"], j["company"], j["position"], j["url"],
                       jd_text=jd_text, status="accepted", source="linkedin")
            accepted_jobs.append(j)
            summary["accepted"] += 1
            query_accepted += 1

            if args.max_accept > 0 and len(accepted_jobs) >= args.max_accept:
                break

        log.info("  Result: +%d accepted, +%d rejected, %d skipped", query_accepted, query_rejected, query_results - query_accepted - query_rejected)
        insert_search_run(conn, q, query_results, query_accepted, query_rejected)
        summary["queries"].append(q)

        if args.max_accept > 0 and len(accepted_jobs) >= args.max_accept:
            break
        time.sleep(1)

    conn.close()
    log.info("Done: %d searched, %d accepted, %d rejected, %d skipped",
             summary["searched"], summary["accepted"], summary["rejected"], summary["skipped_dup"])
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-accept", type=int, default=MAX_ACCEPT)
    ap.add_argument("--start-page", type=int, default=0, help="Starting page offset (0-based)")
    run(ap.parse_args())
