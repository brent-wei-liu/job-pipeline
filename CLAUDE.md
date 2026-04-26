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

# Fetch jobs from watchlist career pages (44 companies)
python3 watchlist_fetch.py --limit 50
python3 watchlist_fetch.py --dry-run --company Anthropic --limit 5

# Generate digest JSON (for LLM formatting)
python3 job_digest.py query 1         # last 1 day
python3 job_digest.py query 7         # last 7 days
python3 job_digest.py stats 30        # quick stats

# List unscored jobs for LLM scoring (output JSON for skill)
python3 backfill_scores.py --stats
python3 backfill_scores.py --json --limit 20
echo '<scores_json>' | python3 job_digest.py save-score

# Company analysis I/O
python3 company_analyze.py --stats
python3 company_analyze.py --list-pending
echo '<analysis_json>' | python3 company_analyze.py --save

# Install dependencies
pip3 install fastapi uvicorn requests
```

## Architecture

- **db.py** — Shared SQLite data layer. All scripts call `get_conn()` which auto-creates tables and runs lightweight `ALTER TABLE` migrations on startup. Tables: `jobs`, `search_runs`, `summaries`, `watchlist`, `company_analysis`. DB file lives at `data/jobs.db` in WAL mode.
- **job_fetch.py** — Scrapes LinkedIn's guest job search API (no auth needed), applies rejection rules (`reject_reason()` function), stores both accepted and rejected jobs. Zero LLM token usage.
- **watchlist_fetch.py** — Fetches jobs from 44 company career pages. Adapter-based: Greenhouse / Ashby / Lever / Workday / SmartRecruiters. Each HTTP request uses `subprocess + curl --max-time 10`. Applies `reject_by_title()` before insert.
- **backfill_scores.py** — I/O-only: lists unscored `accepted` jobs as JSON (`--json`), or accepts a JSON array on stdin and writes scores back. No LLM calls — scoring is done by the `job-score` / `job-backfill` skills. Loads resume from `data/resume.md`.
- **company_analyze.py** — I/O-only: lists companies pending analysis (`--list-pending`), or accepts a JSON array on stdin and writes to `company_analysis` table. No LLM calls — analysis done by `company-analyze` skill.
- **job_digest.py** — Read-only queries against SQLite, outputs structured JSON for an LLM to format. Subcommands: `query`, `stats`, `save-score`, `save-report`.
- **api.py** — FastAPI backend. Key endpoints: `/api/jobs`, `/api/companies`, `/api/stats`, `/api/summaries`, `POST /api/jobs/bulk-delete`.
- **static/index.html** — Single-file frontend. Retro-CRT aesthetic. Five tabs: Jobs, Stats, Search Runs, Digests, Companies.
- **data/** — `jobs.db` (WAL), `resume.md` (scoring), `resume_text.txt` (legacy fallback).

## Key Design Decisions

- Rejected jobs are stored in SQLite (not discarded) with `reject_reason` so filtering rules can be tuned later.
- Deduplication happens at two levels: `linkedin_id` uniqueness constraint, and a soft duplicate check comparing company + position + first 200 chars of JD text.
- Match scoring is decoupled from fetching — `job_fetch.py` stays LLM-free and fast; `backfill_scores.py` handles all LLM calls and is idempotent (skips jobs that already have a `match_score`), so it can be re-run, rate-limited, or interrupted at will.
- The frontend uses double-click-to-confirm for destructive actions (DEL buttons change to "SURE?" with a 3s timeout).
- All datetime filtering uses SQLite's `datetime('now', '-N days')` pattern.
- Schema evolves via additive `ALTER TABLE ADD COLUMN` migrations inside `_init_tables()` — no separate migration framework.
- `notion_page_id` column exists but is a historical artifact — Notion integration has been removed.

## Skills

Four Claude Code skills live in `.claude/skills/`. They do the LLM work; Python scripts handle only I/O.

| Skill | Trigger | What it does | Writes to |
|---|---|---|---|
| `job-score` | Daily 10:05 AM PT | Score recent unscored accepted jobs (resume↔JD, 1-10) | `jobs.match_score` etc. |
| `job-backfill` | On demand | Same as job-score but processes full historical backlog | `jobs.match_score` etc. |
| `job-digest` | Daily after score | Generate Chinese digest, email to user | `summaries` table + Gmail |
| `company-analyze` | Sunday 8:00 AM PT | WebSearch + 5-dim score for watchlist companies | `company_analysis` table |

To run manually: invoke the skill by name in a Claude Code session, or use the Python I/O scripts directly (see Commands section above).

## Cron Schedule

- `0 10 * * *` — `job_fetch.py` (LinkedIn fetch, local crontab)
- Daily 10:05 AM — `job-score` + `job-digest` skills (Claude scheduled tasks)
- Sunday 8:00 AM — `company-analyze` skill (Claude scheduled task)
