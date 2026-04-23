# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Automated job search pipeline: LinkedIn job search → rule-based filtering → SQLite storage → LLM-based resume matching → retro-futuristic web UI. Follows a three-file pattern (Fetch/Digest/DB) consistent with sibling digest projects (ai-leaders-digest, hackernews-digest, etc.), with a separate backfill script for resume scoring.

## Commands

```bash
# Start the web UI (FastAPI + single-file frontend)
python3 api.py                        # → http://127.0.0.1:8080

# Fetch jobs from LinkedIn → filter → store in SQLite
python3 job_fetch.py --max-accept 12

# Generate digest JSON (for LLM formatting)
python3 job_digest.py query 1         # last 1 day
python3 job_digest.py query 7         # last 7 days
python3 job_digest.py stats 30        # quick stats

# Score accepted jobs against the resume (resumable, batched)
python3 backfill_scores.py                        # score everything unscored
python3 backfill_scores.py --limit 10             # cap to 10 jobs
python3 backfill_scores.py --dry-run              # list jobs without calling LLM
python3 backfill_scores.py --batch-size 5 --batch-delay 3

# Install dependencies
pip3 install fastapi uvicorn requests
```

## Architecture

- **db.py** — Shared SQLite data layer. All scripts call `get_conn()` which auto-creates tables and runs lightweight `ALTER TABLE` migrations on startup. Tables: `jobs` (dedup by `linkedin_id` + company/position/JD-prefix; user fields `starred` / `notes` / `applied_at`; match-score fields `match_score` / `match_summary` / `match_reasons` / `match_gaps` / `agentic_ai_relevant` / `scored_at`), `search_runs`, `summaries` (date-keyed digest history). DB file lives at `data/jobs.db` in WAL mode.
- **job_fetch.py** — Scrapes LinkedIn's guest job search API (no auth needed), applies rejection rules (`reject_reason()` function), stores both accepted and rejected jobs. Zero LLM token usage. Configurable via `ROLE_QUERIES` and `MAX_ACCEPT` constants at top of file.
- **job_digest.py** — Read-only queries against SQLite, outputs structured JSON for an LLM to format into a WhatsApp digest. Two subcommands: `query` and `stats`.
- **backfill_scores.py** — Reads accepted jobs with no `match_score` and asks an LLM (via the local OpenClaw gateway at `OPENCLAW_GATEWAY_URL`, default `http://127.0.0.1:18789`) to score resume↔JD fit on a 1–10 scale, returning a structured JSON payload (score, match reasons, gaps, agentic-AI flag, Chinese summary). Safe to interrupt — resumes from where it left off. Loads resume from `data/resume.md` (preferred) or the legacy `data/resume_text.txt`, falling back to `pdftotext` on `Brent_Wei_Liu_CV.pdf` in the parent workspace. Auth token comes from `OPENCLAW_GATEWAY_TOKEN` or `~/.openclaw/openclaw.json`.
- **save_report.py** — One-off helper to insert a hand-written `digest_report.md` into the `summaries` table for a specific date. Hardcoded macOS paths (`/Users/little_claw/...`); not portable, kept around as a manual backfill tool.
- **api.py** — FastAPI backend serving REST endpoints (`/api/jobs`, `/api/stats`, `/api/search-runs`) and the static frontend.
- **static/index.html** — Single-file frontend (HTML+CSS+JS, no build step). Retro-CRT aesthetic with Orbitron/Share Tech Mono fonts. Three tabs: Jobs, Stats, Search Runs.
- **data/** — Persistent state. Holds `jobs.db` (+ WAL sidecars), `resume.md` (curated markdown resume used for matching), and a legacy `resume_text.txt` fallback.

## Key Design Decisions

- Rejected jobs are stored in SQLite (not discarded) with `reject_reason` so filtering rules can be tuned later.
- Deduplication happens at two levels: `linkedin_id` uniqueness constraint, and a soft duplicate check comparing company + position + first 200 chars of JD text.
- Match scoring is decoupled from fetching — `job_fetch.py` stays LLM-free and fast; `backfill_scores.py` handles all LLM calls and is idempotent (skips jobs that already have a `match_score`), so it can be re-run, rate-limited, or interrupted at will.
- The frontend uses double-click-to-confirm for destructive actions (DEL buttons change to "SURE?" with a 3s timeout).
- All datetime filtering uses SQLite's `datetime('now', '-N days')` pattern.
- Schema evolves via additive `ALTER TABLE ADD COLUMN` migrations inside `_init_tables()` — no separate migration framework.
- `notion_page_id` column exists but is a historical artifact — Notion integration has been removed.

## Cron Schedule

Fetch runs daily at 10:00 AM PT (silent), Digest runs at 10:05 AM PT (sends to WhatsApp). Score backfill is run on demand (not yet scheduled).
