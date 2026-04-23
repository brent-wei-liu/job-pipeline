---
name: job-backfill
description: >
  Backfill match scores for historical unscored jobs in batches. Use this skill when the user
  asks to backfill scores, score old jobs, catch up on scoring, 补分, 批量评分, or when there
  is a large backlog of unscored jobs to process.
---

# Job Backfill

Score historical unscored jobs in batches of 20. Unlike `job-score` (which only handles the last 1 day), this skill processes the entire backlog of unscored accepted jobs.

## Workflow

### Step 1: Check the backlog

```bash
cd <project-dir> && python3 backfill_scores.py --stats
```

If all jobs are scored, stop here.

### Step 2: Load the resume

Read `data/resume.md` from the project directory.

### Step 3: Get a batch of unscored jobs

```bash
python3 backfill_scores.py --json --limit 20
```

This outputs a JSON array of up to 20 unscored jobs with their JD text (truncated to 4000 chars).

### Step 4: Score each job

For each job in the batch, compare the JD against the resume. Use the same scoring rubric as `job-score`:

- **9-10**: Near-perfect fit. Core skills, seniority, and domain all align tightly.
- **7-8**: Strong fit. Most key requirements match. Gaps are minor.
- **5-6**: Moderate fit. Some important skills match but meaningful gaps exist.
- **3-4**: Weak fit. Related field but significantly different skills needed.
- **1-2**: Poor fit. Little overlap.

Dimensions to evaluate: technical skill overlap, seniority alignment, domain relevance, agentic AI signal.

Produce a JSON array:

```json
[
  {
    "job_id": 123,
    "score": 7,
    "summary": "中文一句话总结",
    "match_reasons": ["reason 1", "reason 2"],
    "gaps": ["gap 1"],
    "agentic_ai_relevant": false
  }
]
```

### Step 5: Write scores back

```bash
echo '<json_array>' | python3 job_digest.py save-score
```

### Step 6: Report and continue

Print how many were scored this batch and how many remain. Ask the user if they want to continue with the next batch.

If running as a scheduled task (unattended), automatically continue to the next batch until all jobs are scored or a reasonable limit is reached (e.g. 5 batches = 100 jobs per run).
