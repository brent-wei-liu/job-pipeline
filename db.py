#!/usr/bin/env python3
"""Job Pipeline — shared DB layer (SQLite)."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "jobs.db"


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_tables(conn)
    return conn


def _init_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS jobs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        linkedin_id     TEXT UNIQUE NOT NULL,
        company         TEXT NOT NULL,
        position        TEXT NOT NULL,
        url             TEXT NOT NULL,
        jd_text         TEXT DEFAULT '',
        status          TEXT NOT NULL DEFAULT 'accepted',   -- accepted / rejected
        reject_reason   TEXT DEFAULT NULL,
        notion_page_id  TEXT DEFAULT NULL,
        starred         INTEGER NOT NULL DEFAULT 0,         -- 0 or 1
        notes           TEXT DEFAULT '',                    -- user notes (已投、面试中 etc.)
        applied_at      TEXT DEFAULT NULL,                  -- datetime when applied
        fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
        source          TEXT NOT NULL DEFAULT 'linkedin'
    );

    CREATE TABLE IF NOT EXISTS search_runs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        query           TEXT NOT NULL,
        results_count   INTEGER DEFAULT 0,
        accepted        INTEGER DEFAULT 0,
        rejected        INTEGER DEFAULT 0,
        run_at          TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_jobs_fetched ON jobs(fetched_at);
    CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
    CREATE INDEX IF NOT EXISTS idx_jobs_linkedin ON jobs(linkedin_id);

    CREATE TABLE IF NOT EXISTS summaries (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        date        TEXT NOT NULL,
        content     TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS watchlist (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        company     TEXT NOT NULL UNIQUE,
        ats_type    TEXT NOT NULL,
        ats_slug    TEXT,
        enabled     INTEGER NOT NULL DEFAULT 1,
        keywords    TEXT DEFAULT NULL,
        last_run_at TEXT DEFAULT NULL
    );

    CREATE TABLE IF NOT EXISTS company_analysis (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        company         TEXT NOT NULL UNIQUE,
        summary         TEXT,
        funding_score   INTEGER,
        revenue_score   INTEGER,
        moat_score      INTEGER,
        stage_score     INTEGER,
        risk_score      INTEGER,
        overall_score   INTEGER,
        reasoning       TEXT,
        analyzed_at     TEXT NOT NULL DEFAULT (datetime('now')),
        analyst_model   TEXT
    );
    """)
    # Migration: add match score columns if missing
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    for col, typedef in [
        ("match_score", "INTEGER DEFAULT NULL"),
        ("match_summary", "TEXT DEFAULT NULL"),
        ("match_reasons", "TEXT DEFAULT NULL"),       # JSON array
        ("match_gaps", "TEXT DEFAULT NULL"),           # JSON array
        ("agentic_ai_relevant", "INTEGER DEFAULT NULL"),  # 0/1
        ("scored_at", "TEXT DEFAULT NULL"),
        ("source", "TEXT NOT NULL DEFAULT 'linkedin'"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typedef}")
    conn.commit()
    # Migration: add financial profile columns to company_analysis
    ca_cols = {r[1] for r in conn.execute("PRAGMA table_info(company_analysis)").fetchall()}
    for col, typedef in [
        ("is_public",         "INTEGER DEFAULT NULL"),
        ("market_cap_usd",    "REAL DEFAULT NULL"),
        ("funding_stage",     "TEXT DEFAULT NULL"),
        ("total_raised_usd",  "REAL DEFAULT NULL"),
        ("notable_investors", "TEXT DEFAULT NULL"),
    ]:
        if col not in ca_cols:
            conn.execute(f"ALTER TABLE company_analysis ADD COLUMN {col} {typedef}")
    conn.commit()
    # Migration: add LinkedIn company ID column to watchlist (for f_C= filter fetching)
    wl_cols = {r[1] for r in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
    if "linkedin_company_id" not in wl_cols:
        conn.execute("ALTER TABLE watchlist ADD COLUMN linkedin_company_id INTEGER DEFAULT NULL")
        conn.commit()
    _seed_watchlist(conn)


def job_exists(conn, linkedin_id):
    row = conn.execute("SELECT 1 FROM jobs WHERE linkedin_id = ?", (linkedin_id,)).fetchone()
    return row is not None


def job_duplicate(conn, company, position, jd_text=""):
    """Check if same company+position+similar JD already exists (catches LinkedIn re-posts).
    Compares first 200 chars of JD to avoid false positives for large companies
    posting same title across different teams."""
    rows = conn.execute(
        "SELECT jd_text FROM jobs WHERE company = ? AND position = ?",
        (company, position)
    ).fetchall()
    if not rows:
        return False
    jd_prefix = (jd_text or "")[:200].strip()
    # If current JD is empty, can't reliably compare — don't treat as duplicate
    if not jd_prefix:
        return False
    for r in rows:
        existing_prefix = (dict(r)["jd_text"] or "")[:200].strip()
        if existing_prefix and jd_prefix == existing_prefix:
            return True
    return False


def insert_job(conn, linkedin_id, company, position, url, jd_text="",
               status="accepted", reject_reason=None, notion_page_id=None, source="linkedin"):
    conn.execute("""
        INSERT OR IGNORE INTO jobs
            (linkedin_id, company, position, url, jd_text, status, reject_reason, notion_page_id, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (linkedin_id, company, position, url, jd_text, status, reject_reason, notion_page_id, source))
    conn.commit()


def insert_search_run(conn, query, results_count, accepted, rejected):
    conn.execute("""
        INSERT INTO search_runs (query, results_count, accepted, rejected)
        VALUES (?, ?, ?, ?)
    """, (query, results_count, accepted, rejected))
    conn.commit()


def query_jobs(conn, days=1, status=None):
    """Query jobs from the last N days, optionally filtered by status."""
    sql = "SELECT * FROM jobs WHERE fetched_at > datetime('now', ?)"
    params = [f"-{days} days"]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY fetched_at DESC"
    return conn.execute(sql, params).fetchall()


def query_search_runs(conn, days=1):
    return conn.execute(
        "SELECT * FROM search_runs WHERE run_at > datetime('now', ?) ORDER BY run_at DESC",
        (f"-{days} days",)
    ).fetchall()


def get_match_score(conn, job_id):
    """Get cached match score for a job. Returns dict or None."""
    row = conn.execute(
        "SELECT match_score, match_summary, match_reasons, match_gaps, agentic_ai_relevant "
        "FROM jobs WHERE id = ? AND match_score IS NOT NULL", (job_id,)
    ).fetchone()
    if not row:
        return None
    import json
    return {
        "score": row[0],
        "summary": row[1] or "",
        "match_reasons": json.loads(row[2]) if row[2] else [],
        "gaps": json.loads(row[3]) if row[3] else [],
        "agentic_ai_relevant": bool(row[4]),
    }


def save_match_score(conn, job_id, score, summary="", match_reasons=None, gaps=None, agentic_ai_relevant=False):
    """Cache match score for a job."""
    import json
    conn.execute("""
        UPDATE jobs SET
            match_score = ?,
            match_summary = ?,
            match_reasons = ?,
            match_gaps = ?,
            agentic_ai_relevant = ?,
            scored_at = datetime('now')
        WHERE id = ?
    """, (
        score,
        summary,
        json.dumps(match_reasons or [], ensure_ascii=False),
        json.dumps(gaps or [], ensure_ascii=False),
        1 if agentic_ai_relevant else 0,
        job_id,
    ))
    conn.commit()


def get_stats(conn, days=7):
    """Get summary stats for the digest."""
    stats = {}
    stats["total_accepted"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='accepted' AND fetched_at > datetime('now', ?)",
        (f"-{days} days",)
    ).fetchone()[0]
    stats["total_rejected"] = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='rejected' AND fetched_at > datetime('now', ?)",
        (f"-{days} days",)
    ).fetchone()[0]
    stats["top_companies"] = conn.execute(
        """SELECT company, COUNT(*) as cnt FROM jobs
           WHERE status='accepted' AND fetched_at > datetime('now', ?)
           GROUP BY company ORDER BY cnt DESC LIMIT 10""",
        (f"-{days} days",)
    ).fetchall()
    stats["reject_reasons"] = conn.execute(
        """SELECT reject_reason, COUNT(*) as cnt FROM jobs
           WHERE status='rejected' AND fetched_at > datetime('now', ?)
           GROUP BY reject_reason ORDER BY cnt DESC""",
        (f"-{days} days",)
    ).fetchall()
    return stats


def save_summary(conn, content, date=None):
    """Save a digest summary to the summaries table."""
    from datetime import datetime, timezone
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO summaries (date, content) VALUES (?, ?)",
        (date, content)
    )
    conn.commit()


def get_recent_summaries(conn, limit=10):
    """Get the most recent digest summaries."""
    return conn.execute(
        "SELECT * FROM summaries ORDER BY date DESC LIMIT ?",
        (limit,)
    ).fetchall()


def _load_watchlist_seed():
    """Load seed entries from data/watchlist.local.json, falling back to
    data/watchlist.example.json. Returns a list of dicts; empty list if neither exists.
    """
    import json
    data_dir = Path(__file__).resolve().parent / "data"
    for fname in ("watchlist.local.json", "watchlist.example.json"):
        p = data_dir / fname
        if not p.exists():
            continue
        raw = json.loads(p.read_text(encoding="utf-8"))
        return [r for r in raw if isinstance(r, dict) and "company" in r]
    return []


def _seed_watchlist(conn):
    if conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] > 0:
        return  # already seeded; INSERT OR IGNORE makes this safe but cheap to skip
    for r in _load_watchlist_seed():
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (company, ats_type, ats_slug, enabled, linkedin_company_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (r["company"], r.get("ats_type", "custom"), r.get("ats_slug"),
             int(r.get("enabled", 0)), r.get("linkedin_company_id")),
        )
    conn.commit()
