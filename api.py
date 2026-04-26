#!/usr/bin/env python3
"""Job Pipeline — FastAPI backend."""
import sys
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
    sort: str = Query("fetched_at"),
    order: str = Query("desc"),
):
    conn = get_conn()
    sql = "SELECT * FROM jobs WHERE fetched_at > datetime('now', ?)"
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

    allowed_sorts = {"fetched_at", "company", "position", "status", "starred", "match_score"}
    if sort not in allowed_sorts:
        sort = "fetched_at"
    order_dir = "DESC" if order.lower() == "desc" else "ASC"
    sql += f" ORDER BY {sort} {order_dir}"

    rows = conn.execute(sql, params).fetchall()
    jobs = [dict(r) for r in rows]
    conn.close()
    return {"jobs": jobs, "total": len(jobs)}


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
