# Job Pipeline — Skills & Automation

## Architecture

```
Cron / Scheduled Task
        │
        ▼
  Claude Code skill prompt   ← .claude/skills/{skill}/SKILL.md
        │  (Cowork session)
        ▼
  LLM reads context
  LLM calls Python scripts     ← job_fetch.py / backfill_scores.py /
  to list pending work              company_analyze.py / job_digest.py
        │
        ▼
  LLM produces structured JSON
        │
        ▼
  Python script --save         ← reads JSON from stdin, writes to SQLite
        │
        ▼
      jobs.db
```

**Key principle:** Python scripts are pure I/O — they query what needs work and write results back. The LLM (via a skill session) does all computation: scoring, analysis, summarisation. This keeps scripts simple, LLM-free, and safe to interrupt.

---

## Python Scripts

| Script | Role | LLM? |
|---|---|---|
| `job_fetch.py` | Scrape LinkedIn → filter → insert `jobs` | No |
| `watchlist_fetch.py` | Scrape 44 company career pages → insert `jobs` | No |
| `backfill_scores.py` | List unscored jobs; save scores from stdin | No |
| `company_analyze.py` | List unanalyzed companies; save analysis from stdin | No |
| `job_digest.py` | Query DB for digest data; save digest; send email | No |

---

## Skills

### `job-score`

**Purpose:** Score newly fetched accepted jobs against the user's resume (1–10 match score).

**Cron:** Daily at 10:05 AM PT (runs after `job_fetch.py` at 10:00 AM).

**Workflow:**
1. `python3 backfill_scores.py --stats` — check how many unscored jobs exist
2. Read `data/resume.md`
3. `python3 backfill_scores.py --json --limit 20` — get a batch of unscored jobs as JSON
4. LLM scores each job across: technical skill overlap, seniority, domain relevance, agentic AI signal
5. `echo '<json_array>' | python3 job_digest.py save-score` — write scores back

**Input:** Unscored rows in `jobs` where `status='accepted' AND match_score IS NULL`

**Output:** Updates `jobs.match_score`, `match_summary`, `match_reasons`, `match_gaps`, `agentic_ai_relevant`, `scored_at`

**Score rubric:** 9–10 near-perfect · 7–8 strong · 5–6 moderate · 3–4 weak · 1–2 poor

**Depends on:** `backfill_scores.py`, `job_digest.py save-score`, `data/resume.md`

---

### `job-backfill`

**Purpose:** Score the full historical backlog of unscored accepted jobs (not just today's).

**Cron:** On-demand only (no scheduled run). Run when a large batch of new sources is added.

**Workflow:** Same as `job-score` but processes all unscored jobs, not just recent ones. Continues in batches of 20 until backlog is empty or user stops it.

**Input/Output:** Same as `job-score`.

**Depends on:** `backfill_scores.py`, `job_digest.py save-score`, `data/resume.md`

---

### `job-digest`

**Purpose:** Generate a Chinese-language daily digest of accepted jobs, save to DB, email to user.

**Cron:** Daily at 10:05 AM PT.

**Workflow:**
1. `python3 job_digest.py query 1` — get raw data for the last 1 day
2. LLM formats data into a structured Chinese digest (company highlights, score distribution, notable roles)
3. `echo '<report>' | python3 job_digest.py save-report` — save to `summaries` table
4. Send email to `brent.wei.liu@gmail.com` via Gmail API

**Input:** `jobs` rows from the last 24 hours

**Output:** Inserts into `summaries` (date-keyed), sends email

**Depends on:** `job_digest.py`, Gmail API

---

### `company-analyze`

**Purpose:** Research and score watchlist companies across five investment/career dimensions.

**Cron:** Weekly, Sunday at 8:00 AM PT.

**Workflow:**
1. `python3 company_analyze.py --stats` — check pending count
2. `python3 company_analyze.py --list-pending` — get companies needing analysis as JSON
3. LLM does WebSearch research for each (funding, revenue, competitors, stage, risks)
4. LLM scores 5 dimensions per company (see rubric below)
5. `echo '<json_array>' | python3 company_analyze.py --save` — write to DB

**Input:** `watchlist` rows where `enabled=1` and `analyzed_at IS NULL` or older than 7 days

**Output:** Inserts/updates `company_analysis` table

**Five dimensions (1–10):**
- `funding_score` — runway / financial cushion (higher = safer)
- `revenue_score` — revenue predictability (higher = more certain)
- `moat_score` — competitive defensibility (higher = stronger moat)
- `stage_score` — company size fit for a senior ML IC (50–500 people = ideal)
- `risk_score` — structural risk (higher = more risk — displayed inverted in UI)

**Depends on:** `company_analyze.py`, WebSearch access

---

## Scheduled Tasks

| Task ID | Cron | Description |
|---|---|---|
| `job-fetch` (system cron) | `0 10 * * *` — 10:00 AM PT daily | LinkedIn scrape → filter → DB |
| `job-digest` (Claude scheduled) | `5 10 * * *` — 10:05 AM PT daily | Score new jobs + generate digest + email |
| `company-analyze` (Claude scheduled) | `0 8 * * 0` — 8:00 AM PT Sunday | Research + score all enabled watchlist companies |

> **Note:** `job-score` and `job-backfill` are run on-demand or embedded in `job-digest`. Check `.claude/scheduled-tasks/` for current Claude-managed tasks.

---

## Data Flow

```
LinkedIn API          Company Career Pages (44 companies)
     │                         │
     ▼                         ▼
job_fetch.py          watchlist_fetch.py
  - reject_by_title()   - Greenhouse/Ashby/Lever/Workday/SmartRecruiters adapters
  - reject_reason()     - reject_by_title() filter
     │                         │
     └──────────┬──────────────┘
                ▼
          jobs table (SQLite)
          status = 'accepted' | 'rejected'
          source = 'linkedin' | 'watchlist'
                │
                ▼
        backfill_scores.py  ←─── job-score / job-backfill skill
        (LLM scores resume fit)
                │
                ▼
          jobs.match_score (1–10)
          jobs.match_summary
                │
                ▼
         job_digest.py  ←──────── job-digest skill
         (LLM formats Chinese digest)
                │
                ├──→  summaries table
                └──→  Email (Gmail API)

company_analyze.py  ←────────── company-analyze skill
(LLM researches + scores companies)
        │
        ▼
  company_analysis table
  (5-dim scores + reasoning, shown in COMPANIES tab)
```

---

## Manual Triggers

### job-score / job-backfill

```bash
cd /Users/little_claw/job-pipeline

# Check backlog
python3 backfill_scores.py --stats

# Get a batch of 20 unscored jobs as JSON (feed to LLM)
python3 backfill_scores.py --json --limit 20

# Write scores back (LLM outputs JSON array to stdin)
echo '[{"job_id":123,"score":7,...}]' | python3 job_digest.py save-score
```

### job-digest

```bash
# Get raw data for last N days
python3 job_digest.py query 1      # last 1 day
python3 job_digest.py query 7      # last 7 days
python3 job_digest.py stats 30     # stats only

# Save a completed digest
echo '<markdown_report>' | python3 job_digest.py save-report
```

### company-analyze

```bash
# Check pending
python3 company_analyze.py --stats

# List companies needing analysis (JSON output)
python3 company_analyze.py --list-pending

# Force re-analysis of all enabled companies
python3 company_analyze.py --list-pending --force

# Save LLM analysis results
echo '[{"company":"Harvey","overall_score":8,...}]' | python3 company_analyze.py --save
```

### watchlist-fetch

```bash
# All enabled companies, limit 50 per company
python3 watchlist_fetch.py --limit 50

# Single company dry-run
python3 watchlist_fetch.py --dry-run --company Anthropic --limit 5

# Single company real fetch
python3 watchlist_fetch.py --company Anthropic --limit 50
```
