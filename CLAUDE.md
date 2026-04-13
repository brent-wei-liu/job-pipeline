# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Automated job search pipeline: LinkedIn job search → rule-based filtering → SQLite storage → retro-futuristic web UI. Follows a three-file pattern (Fetch/Digest/DB) consistent with sibling digest projects (ai-leaders-digest, hackernews-digest, etc.).

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

# Install dependencies
pip3 install fastapi uvicorn requests
```

## Architecture

- **db.py** — Shared SQLite data layer. All scripts call `get_conn()` which auto-creates tables. Tables: `jobs` (with dedup by `linkedin_id` + company/position/JD-prefix), `search_runs`. DB file lives at `data/jobs.db`.
- **job_fetch.py** — Scrapes LinkedIn's guest job search API (no auth needed), applies rejection rules (`reject_reason()` function), stores both accepted and rejected jobs. Zero LLM token usage. Configurable via `ROLE_QUERIES` and `MAX_ACCEPT` constants at top of file.
- **job_digest.py** — Read-only queries against SQLite, outputs structured JSON for an LLM to format into a WhatsApp digest. Two subcommands: `query` and `stats`.
- **api.py** — FastAPI backend serving REST endpoints (`/api/jobs`, `/api/stats`, `/api/search-runs`) and the static frontend.
- **static/index.html** — Single-file frontend (HTML+CSS+JS, no build step). Retro-CRT aesthetic with Orbitron/Share Tech Mono fonts. Three tabs: Jobs, Stats, Search Runs.

## Key Design Decisions

- Rejected jobs are stored in SQLite (not discarded) with `reject_reason` so filtering rules can be tuned later.
- Deduplication happens at two levels: `linkedin_id` uniqueness constraint, and a soft duplicate check comparing company + position + first 200 chars of JD text.
- The frontend uses double-click-to-confirm for destructive actions (DEL buttons change to "SURE?" with a 3s timeout).
- All datetime filtering uses SQLite's `datetime('now', '-N days')` pattern.
- `notion_page_id` column exists but is a historical artifact — Notion integration has been removed.

## Cron Schedule

Fetch runs daily at 10:00 AM PT (silent), Digest runs at 10:05 AM PT (sends to WhatsApp).
