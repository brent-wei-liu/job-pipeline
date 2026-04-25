#!/usr/bin/env python3
"""Job Pipeline — Fetch: Watchlist career pages (Greenhouse/Ashby/Lever) → SQLite."""
import re, json, sys, time, subprocess, argparse, logging
from html import unescape
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("watchlist_fetch")

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn, job_exists, job_duplicate, insert_job
from job_fetch import reject_by_title

CURL_MAX_TIME = "10"   # curl --max-time seconds
SUBPROCESS_TIMEOUT = 15  # subprocess.run timeout (hard kill)


# ── HTTP via curl ─────────────────────────────────────────────────────────

def _curl(url):
    """Fetch URL via curl. Returns (body_str, error_str)."""
    try:
        r = subprocess.run(
            ["curl", "-sS", "--max-time", CURL_MAX_TIME, url],
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
        )
        if r.returncode != 0:
            return None, f"curl exit {r.returncode}: {r.stderr.strip()[:120]}"
        return r.stdout, None
    except subprocess.TimeoutExpired:
        return None, f"timeout (>{SUBPROCESS_TIMEOUT}s)"
    except Exception as e:
        return None, str(e)


def _json_get(url):
    """Fetch URL and parse JSON. Returns (data, error_str)."""
    body, err = _curl(url)
    if err:
        return None, err
    try:
        return json.loads(body), None
    except Exception:
        snippet = (body or "")[:120].replace("\n", " ")
        return None, f"JSON parse error — got: {snippet}"


# ── HTML stripping ────────────────────────────────────────────────────────

def strip_html(text):
    if not text:
        return ""
    text = unescape(text)  # decode &lt; → < before regexes run
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</?(p|div|h[1-6]|ul|ol|section|article)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "\n• ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Adapters ──────────────────────────────────────────────────────────────

def fetch_greenhouse(slug, limit=100):
    """Returns (jobs_list, error_str). Fetches JD via per-job second request."""
    data, err = _json_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if err:
        return None, err
    if "jobs" not in data:
        return None, f"unexpected response: {str(data)[:100]}"

    results = []
    for j in data["jobs"][:limit]:
        jid = j["id"]
        jd_text = ""
        jd_data, jd_err = _json_get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}"
        )
        if jd_err:
            log.warning("  JD fetch failed for %s/%s: %s", slug, jid, jd_err)
        elif jd_data:
            jd_text = strip_html(jd_data.get("content", ""))

        results.append({
            "external_id": str(jid),
            "position":    j.get("title", ""),
            "url":         j.get("absolute_url", ""),
            "jd_text":     jd_text[:18000],
        })
        time.sleep(0.15)

    return results, None


def fetch_ashby(slug, limit=100):
    """Returns (jobs_list, error_str). Description included in list response."""
    data, err = _json_get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if err:
        return None, err
    if not isinstance(data, dict):
        return None, f"unexpected response type: {type(data).__name__}"
    # Ashby API uses 'jobs' key; older docs showed 'jobPostings'
    postings = data.get("jobs") or data.get("jobPostings")
    if postings is None:
        return None, f"no jobs key in response: {str(data)[:100]}"

    results = []
    for j in postings[:limit]:
        desc = (j.get("descriptionHtml") or j.get("descriptionPlain")
                or j.get("description") or "")
        url = j.get("jobUrl") or j.get("applyUrl") or j.get("applicationUrl", "")
        results.append({
            "external_id": j.get("id", ""),
            "position":    j.get("title", ""),
            "url":         url,
            "jd_text":     strip_html(desc)[:18000],
        })
    return results, None


def fetch_lever(slug, limit=100):
    """Returns (jobs_list, error_str). Full description included in list response."""
    data, err = _json_get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if err:
        return None, err
    if not isinstance(data, list):
        return None, f"unexpected response type: {type(data).__name__}"

    results = []
    for j in data[:limit]:
        desc_parts = [j.get("description") or j.get("descriptionBody") or ""]
        for lst in j.get("lists", []):
            content = lst.get("content", "")
            if content:
                heading = lst.get("text", "")
                desc_parts.append(f"<h3>{heading}</h3>{content}")
        results.append({
            "external_id": j.get("id", ""),
            "position":    j.get("text", ""),
            "url":         j.get("hostedUrl", ""),
            "jd_text":     strip_html("\n".join(desc_parts))[:18000],
        })
    return results, None


WD_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def fetch_workday(slug, limit=100):
    """Workday adapter. slug format: '{tenant}/{site}' e.g. 'broadcom/External_Career'."""
    parts = slug.split("/", 1)
    if len(parts) != 2:
        return None, f"slug must be 'tenant/site', got: {slug}"
    tenant, site = parts
    base = f"https://{tenant}.wd1.myworkdayjobs.com/wday/cxs/{tenant}/{site}"

    results = []
    offset = 0
    batch = min(limit, 20)

    while len(results) < limit:
        body = json.dumps({"limit": batch, "offset": offset, "searchText": "", "appliedFacets": {}})
        try:
            r = subprocess.run(
                ["curl", "-sS", "--max-time", CURL_MAX_TIME,
                 "-X", "POST",
                 "-H", "Content-Type: application/json",
                 "-H", "Accept: application/json",
                 "-A", WD_UA,
                 "-d", body,
                 f"{base}/jobs"],
                capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
            )
            if r.returncode != 0:
                return None, f"curl exit {r.returncode}: {r.stderr.strip()[:120]}"
            data = json.loads(r.stdout)
        except subprocess.TimeoutExpired:
            return None, "timeout on jobs list"
        except Exception as e:
            return None, str(e)

        postings = data.get("jobPostings", [])
        if not postings:
            break

        for j in postings:
            if len(results) >= limit:
                break
            ext_path = j.get("externalPath", "")
            # ext_path like /job/Location/Title_REQID — strip leading /job/
            path_seg = ext_path.lstrip("/")
            jd_text = ""
            try:
                jr = subprocess.run(
                    ["curl", "-sS", "--max-time", CURL_MAX_TIME,
                     "-H", "Accept: application/json",
                     "-A", WD_UA,
                     f"{base}/{path_seg}"],
                    capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
                )
                if jr.returncode == 0:
                    jd_data = json.loads(jr.stdout)
                    raw = jd_data.get("jobPostingInfo", {}).get("jobDescription", "")
                    jd_text = strip_html(raw)
            except Exception:
                pass

            req_id = ext_path.split("_")[-1] if "_" in ext_path else ext_path
            results.append({
                "external_id": req_id,
                "position":    j.get("title", ""),
                "url":         f"https://{tenant}.wd1.myworkdayjobs.com/en-US/{site}{ext_path}",
                "jd_text":     jd_text[:18000],
            })
            time.sleep(0.15)

        offset += len(postings)
        if offset >= data.get("total", 0):
            break

    return results, None


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "ashby":      fetch_ashby,
    "lever":      fetch_lever,
    "workday":    fetch_workday,
}


# ── Main ──────────────────────────────────────────────────────────────────

def matches_keywords(job, keywords):
    if not keywords:
        return True
    text = (job["position"] + " " + job["jd_text"]).lower()
    return any(kw.lower() in text for kw in keywords)


def run(args):
    conn = get_conn()  # also seeds watchlist on first run

    q = "SELECT company, ats_type, ats_slug, keywords FROM watchlist WHERE enabled = 1"
    params = []
    if args.company:
        q += " AND LOWER(company) = LOWER(?)"
        params.append(args.company)

    rows = conn.execute(q, params).fetchall()
    if not rows:
        log.error("No enabled watchlist entries found (company filter: %s)", args.company or "none")
        sys.exit(1)

    totals = {"fetched": 0, "new": 0, "skipped": 0, "errors": 0}

    for row in rows:
        company  = row["company"]
        ats_type = row["ats_type"]
        slug     = row["ats_slug"]
        keywords = json.loads(row["keywords"]) if row["keywords"] else None

        fetcher = FETCHERS.get(ats_type)
        if not fetcher:
            log.warning("[%s] ats_type '%s' not implemented — skip", company, ats_type)
            continue

        log.info("── %s (%s / %s) ──", company, ats_type, slug)
        jobs, err = fetcher(slug, limit=args.limit)

        if err:
            log.error("  [%s] %s", company, err)
            totals["errors"] += 1
            print(f"  {company}: ERROR — {err}")
            continue

        new_count = skip_count = 0

        for job in jobs:
            if not matches_keywords(job, keywords):
                skip_count += 1
                continue

            linkedin_id = f"{ats_type}_{slug}_{job['external_id']}"

            if args.dry_run:
                log.info("  [dry-run] %s | %s", company, job["position"])
                print(json.dumps({
                    "company":    company,
                    "position":   job["position"],
                    "url":        job["url"],
                    "jd_preview": job["jd_text"][:150],
                }, ensure_ascii=False, indent=2))
                new_count += 1
                continue

            if job_exists(conn, linkedin_id):
                skip_count += 1
                continue
            if job_duplicate(conn, company, job["position"], job["jd_text"]):
                skip_count += 1
                continue

            reason = reject_by_title(job["position"])
            status = "rejected" if reason else "accepted"
            if reason:
                log.info("  ✗ %s / %s — %s", company, job["position"], reason)
            else:
                log.info("  ✓ %s / %s", company, job["position"])

            insert_job(
                conn,
                linkedin_id=linkedin_id,
                company=company,
                position=job["position"],
                url=job["url"],
                jd_text=job["jd_text"],
                status=status,
                reject_reason=reason,
                source="watchlist",
            )
            new_count += 1

        print(f"  {company}: fetched={len(jobs)}, new={new_count}, skip={skip_count}")
        totals["fetched"] += len(jobs)
        totals["new"]     += new_count
        totals["skipped"] += skip_count

        if not args.dry_run:
            conn.execute(
                "UPDATE watchlist SET last_run_at = datetime('now') WHERE company = ?",
                (company,),
            )
            conn.commit()

    conn.close()
    log.info("Done — fetched=%d new=%d skipped=%d errors=%d",
             totals["fetched"], totals["new"], totals["skipped"], totals["errors"])
    print(json.dumps(totals, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Fetch jobs from watchlist career pages")
    ap.add_argument("--dry-run",  action="store_true", help="Print without writing to DB")
    ap.add_argument("--company",  type=str, default=None, help="Only fetch this company")
    ap.add_argument("--limit",    type=int, default=100,  help="Max jobs per company (default 100)")
    run(ap.parse_args())
