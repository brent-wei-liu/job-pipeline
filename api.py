#!/usr/bin/env python3
"""Job Pipeline — FastAPI backend."""
import os, sys, json, re, shutil, subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
from db import get_conn

app = FastAPI(title="Job Pipeline", version="1.0.0")

# ── Static files ────────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ── Models ──────────────────────────────────────────────────────────────
class JobUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    starred: Optional[int] = None
    applied_at: Optional[str] = None


# ── Jobs API ────────────────────────────────────────────────────────────
@app.get("/api/jobs")
def list_jobs(
    days: int = Query(30, ge=1, le=365),
    status: Optional[str] = Query(None),
    starred: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None),
    sort: str = Query("fetched_at"),
    order: str = Query("desc"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    conn = get_conn()
    # Exclude large fields (jd_text, match_reasons, match_gaps) — fetch lazily via GET /api/jobs/{id}
    sql = """SELECT id, linkedin_id, company, position, url, status, reject_reason,
                    notion_page_id, starred, notes, applied_at, fetched_at, source,
                    match_score, match_summary, agentic_ai_relevant, scored_at
             FROM jobs WHERE fetched_at > datetime('now', ?)"""
    params = [f"-{days} days"]

    if status:
        sql += " AND status = ?"
        params.append(status)
    if starred is not None:
        sql += " AND starred = ?"
        params.append(starred)
    if search:
        sql += " AND (company LIKE ? OR position LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    if source:
        sql += " AND source = ?"
        params.append(source)
    if status != "rejected":
        sql += " AND (reject_reason IS NULL OR reject_reason != 'user_deleted')"
    if min_score is not None:
        if min_score == -1:
            sql += " AND match_score IS NULL"
        else:
            sql += " AND match_score >= ?"
            params.append(min_score)

    allowed_sorts = {"fetched_at", "company", "position", "status", "starred", "match_score"}
    if sort not in allowed_sorts:
        sort = "fetched_at"
    order_dir = "DESC" if order.lower() == "desc" else "ASC"
    sql += f" ORDER BY {sort} {order_dir} LIMIT ? OFFSET ?"

    rows = conn.execute(sql, params + [limit + 1, offset]).fetchall()
    has_more = len(rows) > limit
    jobs = [dict(r) for r in rows[:limit]]
    conn.close()
    return {"jobs": jobs, "total": len(jobs), "has_more": has_more, "offset": offset}


class BulkDeleteBody(BaseModel):
    ids: List[int]

@app.post("/api/jobs/bulk-delete")
def bulk_delete_jobs(body: BulkDeleteBody):
    if not body.ids:
        raise HTTPException(400, "ids list is required and must be non-empty")
    conn = get_conn()
    placeholders = ",".join("?" * len(body.ids))
    result = conn.execute(
        f"UPDATE jobs SET status='rejected', reject_reason='user_deleted' WHERE id IN ({placeholders})",
        body.ids,
    )
    conn.commit()
    updated = result.rowcount
    conn.close()
    return {"ok": True, "updated": updated}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Job not found")
    return dict(row)


@app.put("/api/jobs/{job_id}")
def update_job(job_id: int, data: JobUpdate):
    conn = get_conn()
    row = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Job not found")

    updates = []
    params = []
    if data.status is not None:
        updates.append("status = ?")
        params.append(data.status)
    if data.notes is not None:
        updates.append("notes = ?")
        params.append(data.notes)
    if data.starred is not None:
        updates.append("starred = ?")
        params.append(data.starred)
    if data.applied_at is not None:
        updates.append("applied_at = ?")
        params.append(data.applied_at if data.applied_at else None)

    if updates:
        params.append(job_id)
        conn.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

    updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return dict(updated)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int):
    conn = get_conn()
    row = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Job not found")
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/jobs/{job_id}/star")
def toggle_star(job_id: int):
    conn = get_conn()
    row = conn.execute("SELECT starred FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Job not found")
    new_val = 0 if row["starred"] else 1
    conn.execute("UPDATE jobs SET starred = ? WHERE id = ?", (new_val, job_id))
    conn.commit()
    conn.close()
    return {"starred": new_val}


# ── Search Runs API ─────────────────────────────────────────────────────
@app.get("/api/sources")
def list_sources():
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT source FROM jobs ORDER BY source").fetchall()
    conn.close()
    return {"sources": [r["source"] for r in rows]}


@app.get("/api/search-runs")
def list_search_runs(days: int = Query(30, ge=1, le=365)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM search_runs WHERE run_at > datetime('now', ?) ORDER BY run_at DESC",
        (f"-{days} days",)
    ).fetchall()
    conn.close()
    return {"runs": [dict(r) for r in rows], "total": len(rows)}


@app.delete("/api/search-runs/{run_id}")
def delete_search_run(run_id: int):
    conn = get_conn()
    row = conn.execute("SELECT id FROM search_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Search run not found")
    conn.execute("DELETE FROM search_runs WHERE id = ?", (run_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── Stats API ───────────────────────────────────────────────────────────
@app.get("/api/stats")
def get_stats(days: int = Query(30, ge=1, le=365)):
    conn = get_conn()
    d = f"-{days} days"

    accepted = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='accepted' AND fetched_at > datetime('now', ?)", (d,)
    ).fetchone()[0]
    rejected = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status='rejected' AND fetched_at > datetime('now', ?)", (d,)
    ).fetchone()[0]
    starred = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE starred=1 AND fetched_at > datetime('now', ?)", (d,)
    ).fetchone()[0]

    top_companies = conn.execute(
        """SELECT company, COUNT(*) as cnt FROM jobs
           WHERE status='accepted' AND fetched_at > datetime('now', ?)
           GROUP BY company ORDER BY cnt DESC LIMIT 10""", (d,)
    ).fetchall()

    reject_reasons = conn.execute(
        """SELECT reject_reason, COUNT(*) as cnt FROM jobs
           WHERE status='rejected' AND fetched_at > datetime('now', ?)
           GROUP BY reject_reason ORDER BY cnt DESC""", (d,)
    ).fetchall()

    daily_counts = conn.execute(
        """SELECT DATE(fetched_at) as day, status, COUNT(*) as cnt FROM jobs
           WHERE fetched_at > datetime('now', ?)
           GROUP BY day, status ORDER BY day""", (d,)
    ).fetchall()

    all_accepted = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='accepted'").fetchone()[0]
    all_rejected = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='rejected'").fetchone()[0]

    conn.close()
    return {
        "period_days": days,
        "accepted": accepted,
        "rejected": rejected,
        "starred": starred,
        "top_companies": [{"company": dict(r)["company"], "count": dict(r)["cnt"]} for r in top_companies],
        "reject_reasons": [{"reason": dict(r)["reject_reason"], "count": dict(r)["cnt"]} for r in reject_reasons],
        "daily_counts": [dict(r) for r in daily_counts],
        "all_time": {"accepted": all_accepted, "rejected": all_rejected},
    }


# ── Digest Summaries API ─────────────────────────────────────────────────────
@app.get("/api/summaries")
def list_summaries(limit: int = Query(30, ge=1, le=100)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM summaries ORDER BY date DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return {"summaries": [dict(r) for r in rows], "total": len(rows)}


# ── Companies API ────────────────────────────────────────────────────────────
@app.get("/api/companies")
def list_companies():
    conn = get_conn()
    rows = conn.execute("""
        SELECT w.company, w.ats_type, w.ats_slug, w.enabled,
               ca.summary, ca.funding_score, ca.revenue_score, ca.moat_score,
               ca.stage_score, ca.risk_score, ca.overall_score,
               ca.reasoning, ca.analyzed_at, ca.analyst_model,
               ca.is_public, ca.market_cap_usd, ca.funding_stage,
               ca.total_raised_usd, ca.notable_investors
        FROM watchlist w LEFT JOIN company_analysis ca ON ca.company = w.company
        UNION
        SELECT ca.company, 'manual' AS ats_type, NULL AS ats_slug, 1 AS enabled,
               ca.summary, ca.funding_score, ca.revenue_score, ca.moat_score,
               ca.stage_score, ca.risk_score, ca.overall_score,
               ca.reasoning, ca.analyzed_at, ca.analyst_model,
               ca.is_public, ca.market_cap_usd, ca.funding_stage,
               ca.total_raised_usd, ca.notable_investors
        FROM company_analysis ca
        WHERE ca.company NOT IN (SELECT company FROM watchlist)
        ORDER BY overall_score DESC NULLS LAST, company ASC
    """).fetchall()
    conn.close()
    return {"companies": [dict(r) for r in rows], "total": len(rows)}


_ANALYZE_PROMPT = """You are a company analyst. Research the company "{company}" and score it across five dimensions for a senior ML engineer evaluating job opportunities.

Use WebSearch to find current information on:
- Whether the company is publicly traded (search for its stock ticker)
- If public: current market cap in USD
- If private: latest funding round stage, total raised, top investors
- Revenue / ARR, key customers
- Competitive position and main competitors
- Employee count and growth stage
- Any recent risks (layoffs, pivots, regulatory issues)

Score each dimension 1-10:
1. funding_score: runway/financial cushion (10=profitable/well-funded, 1=distressed)
2. revenue_score: revenue predictability (10=clear ARR+enterprise contracts, 1=no revenue)
3. moat_score: competitive defensibility (10=strong moat, 1=commodity)
4. stage_score: stage fit for senior ML IC (10=50-500 people scaling ML, 1=too early/too large/wrong domain)
5. risk_score: structural risk — HIGHER = WORSE (10=existential risk, 1=very stable)
overall_score: weighted avg: funding×0.25 + revenue×0.25 + moat×0.20 + stage×0.20 + (10-risk)×0.10, round to integer.

Financial profile fields:
- is_public: 1 if publicly traded, 0 if private, null if unknown
- market_cap_usd: public companies only — market cap in Billion USD (e.g. 2600.0 for $2.6T), null otherwise
- funding_stage: private companies only — latest round (Seed/A/B/C/D/E/Growth/Pre-IPO), null otherwise
- total_raised_usd: private companies only — total raised in Million USD (e.g. 1100.0 for $1.1B), null otherwise
- notable_investors: private companies only — JSON array of top 3 investor names, null otherwise

Output ONLY a single JSON object (no markdown, no explanation):
{{"company":"{company}","summary":"一句话中文简介","funding_score":N,"revenue_score":N,"moat_score":N,"stage_score":N,"risk_score":N,"overall_score":N,"reasoning":{{"funding":"...","revenue":"...","moat":"...","stage":"...","risk":"..."}},"is_public":1,"market_cap_usd":2600.0,"funding_stage":null,"total_raised_usd":null,"notable_investors":null,"analyst_model":"claude-sonnet-4-6"}}"""

CLAUDE_CLI = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
# Subprocess running from the project dir takes ~2x the bare CLI latency
# because Claude Code auto-discovers project skills / CLAUDE.md as context.
# 180s leaves a comfortable margin for the WebSearch-instructed prompt.
ANALYZE_TIMEOUT = 180  # seconds


@app.post("/api/companies/{company}/analyze")
def analyze_company(company: str):
    conn = get_conn()

    # 1-hour cache
    cached = conn.execute(
        "SELECT * FROM company_analysis WHERE company = ? AND analyzed_at > datetime('now', '-1 hour')",
        (company,)
    ).fetchone()
    if cached:
        conn.close()
        return {"cached": True, **dict(cached)}

    # Spawn claude CLI
    if not CLAUDE_CLI:
        conn.close()
        raise HTTPException(
            500,
            "claude CLI not found. Install Claude Code, or set CLAUDE_BIN to its path."
        )
    prompt = _ANALYZE_PROMPT.format(company=company)
    try:
        result = subprocess.run(
            [CLAUDE_CLI, "-p", "--model", "claude-sonnet-4-6", prompt],
            capture_output=True, text=True, timeout=ANALYZE_TIMEOUT,
        )
        raw = result.stdout.strip()
    except subprocess.TimeoutExpired:
        conn.close()
        raise HTTPException(504, "Analysis timed out after 90s")
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"CLI error: {e}")

    # Extract JSON — strip any surrounding prose
    json_match = re.search(r'\{[\s\S]*\}', raw)
    if not json_match:
        conn.close()
        raise HTTPException(502, f"No JSON in response: {raw[:200]}")
    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        conn.close()
        raise HTTPException(502, f"JSON parse error: {e} — raw: {raw[:200]}")

    # Soft-validate required score fields: missing → 0 with a warning.
    # Hard-failing here meant the user got a useless 502 when the LLM
    # truncated or omitted a single field; better to record what we have.
    required = {"funding_score", "revenue_score", "moat_score",
                "stage_score", "risk_score", "overall_score"}
    missing = required - data.keys()
    warnings = []
    if missing:
        warnings.append(f"missing score fields defaulted to 0: {sorted(missing)}")
        for k in missing:
            data[k] = 0

    reasoning = data.get("reasoning", {})
    if isinstance(reasoning, dict):
        reasoning = json.dumps(reasoning, ensure_ascii=False)

    notable_investors = data.get("notable_investors")
    if isinstance(notable_investors, list):
        notable_investors = json.dumps(notable_investors, ensure_ascii=False)

    # Upsert into company_analysis
    conn.execute("""
        INSERT INTO company_analysis
            (company, summary, funding_score, revenue_score, moat_score,
             stage_score, risk_score, overall_score, reasoning, analyzed_at, analyst_model,
             is_public, market_cap_usd, funding_stage, total_raised_usd, notable_investors)
        VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),?,?,?,?,?,?)
        ON CONFLICT(company) DO UPDATE SET
            summary=excluded.summary, funding_score=excluded.funding_score,
            revenue_score=excluded.revenue_score, moat_score=excluded.moat_score,
            stage_score=excluded.stage_score, risk_score=excluded.risk_score,
            overall_score=excluded.overall_score, reasoning=excluded.reasoning,
            analyzed_at=excluded.analyzed_at, analyst_model=excluded.analyst_model,
            is_public=excluded.is_public, market_cap_usd=excluded.market_cap_usd,
            funding_stage=excluded.funding_stage, total_raised_usd=excluded.total_raised_usd,
            notable_investors=excluded.notable_investors
    """, (
        company, data.get("summary"), data.get("funding_score"), data.get("revenue_score"),
        data.get("moat_score"), data.get("stage_score"), data.get("risk_score"),
        data.get("overall_score"), reasoning, data.get("analyst_model", "claude-sonnet-4-6"),
        data.get("is_public"), data.get("market_cap_usd"), data.get("funding_stage"),
        data.get("total_raised_usd"), notable_investors,
    ))
    conn.commit()
    row = conn.execute("SELECT * FROM company_analysis WHERE company = ?", (company,)).fetchone()
    conn.close()
    resp = {"cached": False, **dict(row)}
    if warnings:
        resp["warnings"] = warnings
    return resp


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
