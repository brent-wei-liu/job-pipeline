#!/usr/bin/env python3
"""Job Pipeline — Fetch LinkedIn jobs by company ID (f_C filter).

Targets the LinkedIn guest job-search API with `f_C=<id>` filter to sweep all
recent postings for specific companies. Used for big employers whose own career
pages are locked behind anti-bot or custom auth (Google, Apple, Meta, Amazon,
Microsoft, Tesla, Intuit, etc.).

Reuses parsing / rejection logic from job_fetch.py — pure addition, no edits
to existing scripts.
"""
import sys, json, time, random, argparse, logging, requests
from pathlib import Path
from urllib.parse import quote

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("linkedin_company_fetch")

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn, insert_job, insert_search_run
from job_fetch import (
    parse_job_cards,
    extract_jd_text,
    reject_by_title,
    reject_reason,
    UA,
)

PAGES_PER_COMPANY = 4
PAGE_SIZE = 25
TPR_DAYS = "r2592000"
SEARCH_TIMEOUT = 25
JD_TIMEOUT = 25
JD_DELAY_RANGE = (2, 5)


def search_url(company_id, start):
    return (
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        f"?keywords=&location=&f_TPR={TPR_DAYS}"
        f"&sortBy=DD&start={start}&f_C={company_id}"
    )


def jd_url(linkedin_id):
    return f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{linkedin_id}"


def watchlist_targets(conn, company_filter=None):
    """Return list of (company, linkedin_company_id) where ID is set."""
    sql = "SELECT company, linkedin_company_id FROM watchlist WHERE linkedin_company_id IS NOT NULL"
    params = []
    if company_filter:
        sql += " AND company = ?"
        params.append(company_filter)
    sql += " ORDER BY company"
    return [(r[0], r[1]) for r in conn.execute(sql, params).fetchall()]


def fetch_company(conn, company, company_id, args, summary):
    log.info("── %s (LinkedIn id=%d) ──", company, company_id)
    cards = []
    for page in range(PAGES_PER_COMPANY):
        start = page * PAGE_SIZE
        try:
            html = requests.get(search_url(company_id, start),
                                headers={"User-Agent": UA},
                                timeout=SEARCH_TIMEOUT).text
        except Exception as e:
            log.error("  search failed page=%d: %s", page, e)
            summary["errors"] += 1
            break
        page_cards = parse_job_cards(html)
        if not page_cards:
            break
        cards.extend(page_cards)
        time.sleep(0.5)

    log.info("  Found %d cards", len(cards))
    summary["searched"] += len(cards)

    seen_local = set()
    accepted_count = 0
    rejected_count = 0

    for j in cards:
        if j["id"] in seen_local:
            continue
        seen_local.add(j["id"])

        existing = conn.execute(
            "SELECT id, jd_text FROM jobs WHERE linkedin_id = ?", (j["id"],)
        ).fetchone()
        if existing and existing[1]:
            summary["skipped_dup"] += 1
            continue

        # Phase 1: title reject (no JD fetch needed)
        title_reason = reject_by_title(j["position"])
        if title_reason and not existing:
            log.info("  ✗ %s — %s (%s)", j["position"][:60], "title rejected", title_reason)
            insert_job(conn, j["id"], company, j["position"], j["url"],
                       jd_text="", status="rejected",
                       reject_reason=title_reason, source="linkedin")
            rejected_count += 1
            summary["rejected_by_reason"][title_reason] = (
                summary["rejected_by_reason"].get(title_reason, 0) + 1
            )
            continue

        # Phase 2: JD fetch
        time.sleep(random.uniform(*JD_DELAY_RANGE))
        try:
            resp = requests.get(jd_url(j["id"]),
                                headers={"User-Agent": UA},
                                timeout=JD_TIMEOUT)
            if resp.status_code == 403:
                log.warning("  ⚠ JD 403 for %s — saving without JD", j["position"][:60])
                if not existing:
                    insert_job(conn, j["id"], company, j["position"], j["url"],
                               jd_text="", status="accepted", source="linkedin")
                    accepted_count += 1
                continue
            jd_html = resp.text
        except Exception as e:
            log.warning("  JD fetch failed for %s: %s", j["id"], e)
            summary["errors"] += 1
            continue

        jd_text = extract_jd_text(jd_html)
        reason = reject_reason(jd_html, j["position"])

        if existing:
            # Backfill existing row missing JD
            if reason:
                conn.execute(
                    "UPDATE jobs SET jd_text=?, status='rejected', reject_reason=? WHERE id=?",
                    (jd_text, reason, existing[0]),
                )
            else:
                conn.execute("UPDATE jobs SET jd_text=? WHERE id=?", (jd_text, existing[0]))
            conn.commit()
            summary["backfilled"] += 1
            continue

        if reason:
            log.info("  ✗ %s — rejected (%s)", j["position"][:60], reason)
            insert_job(conn, j["id"], company, j["position"], j["url"],
                       jd_text=jd_text, status="rejected",
                       reject_reason=reason, source="linkedin")
            rejected_count += 1
            summary["rejected_by_reason"][reason] = (
                summary["rejected_by_reason"].get(reason, 0) + 1
            )
            continue

        log.info("  ✓ %s", j["position"][:70])
        insert_job(conn, j["id"], company, j["position"], j["url"],
                   jd_text=jd_text, status="accepted", source="linkedin")
        accepted_count += 1

        if args.limit > 0 and accepted_count >= args.limit:
            break

    log.info("  → %s: accepted=%d rejected=%d", company, accepted_count, rejected_count)
    insert_search_run(
        conn,
        query=f"linkedin_company:{company}:f_C={company_id}",
        results_count=len(cards),
        accepted=accepted_count,
        rejected=rejected_count,
    )
    summary["accepted"] += accepted_count
    summary["rejected"] += rejected_count


def run(args):
    conn = get_conn()
    targets = watchlist_targets(conn, args.company)
    if not targets:
        log.error("No watchlist targets with linkedin_company_id set%s",
                  f" matching company={args.company!r}" if args.company else "")
        sys.exit(1)

    log.info("Targets: %d company/companies", len(targets))

    summary = {
        "searched": 0,
        "accepted": 0,
        "rejected": 0,
        "rejected_by_reason": {},
        "skipped_dup": 0,
        "backfilled": 0,
        "errors": 0,
    }

    if args.dry_run:
        log.info("DRY RUN — would fetch:")
        for c, cid in targets:
            log.info("  %s (id=%d)", c, cid)
        return

    for company, cid in targets:
        try:
            fetch_company(conn, company, cid, args, summary)
        except Exception as e:
            log.error("  Fatal error for %s: %s", company, e)
            summary["errors"] += 1
        time.sleep(1)

    conn.close()
    log.info("Done — searched=%d accepted=%d rejected=%d skipped_dup=%d backfilled=%d errors=%d",
             summary["searched"], summary["accepted"], summary["rejected"],
             summary["skipped_dup"], summary["backfilled"], summary["errors"])
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", default=None,
                    help="Restrict to one company name (must exist in watchlist)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Cap accepted count per company (0 = no limit)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print targets without fetching")
    run(ap.parse_args())
