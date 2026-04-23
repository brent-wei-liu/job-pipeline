---
name: job-score
description: >
  Score unscored accepted jobs from the job pipeline against the user's resume.
  Use this skill whenever the user asks to score jobs, rate job matches, evaluate
  JD fit, run match scoring, or when this task runs on a schedule. Also trigger
  when the user mentions "backfill scores", "打分", "评分", or "匹配".
---

# Job Score

Score recently fetched jobs against the user's resume by comparing each JD to the resume and producing a structured match assessment.

## When This Runs

Scheduled daily at 10:30 AM PT, after Fetch (10:00) and before Digest (11:00). Can also be triggered manually.

## Workflow

### Step 1: Load the resume

Read the resume from `data/resume.md` in the job-pipeline project directory. This is the primary resume file. If it doesn't exist, fall back to `data/resume_text.txt`.

### Step 2: Query unscored jobs

Run this command to get the list of unscored accepted jobs from the last 1 day:

```bash
cd <project-dir> && python3 -c "
from db import get_conn
import json
conn = get_conn()
rows = conn.execute('''
    SELECT id, company, position, jd_text FROM jobs
    WHERE status=\"accepted\" AND match_score IS NULL
    AND fetched_at >= datetime(\"now\", \"-1 days\")
    ORDER BY fetched_at DESC
''').fetchall()
print(json.dumps([{'id':r[0],'company':r[1],'position':r[2],'jd_text':r[3]} for r in rows], ensure_ascii=False))
conn.close()
"
```

If there are zero results, stop here — nothing to score.

### Step 3: Score each job

For each job, compare the JD against the resume and assess fit. Consider these dimensions:

- **Technical skill overlap**: Does the candidate's stack (PyTorch, LLMs, recommendation systems, search ranking, distributed systems) match what the JD asks for?
- **Seniority alignment**: The candidate is senior/staff level with 10+ years. Does the role's level match?
- **Domain relevance**: Is the role in a domain the candidate has worked in (ads, search, recommendations, ML infra, agentic AI)?
- **Agentic AI signal**: Does the JD mention agents, autonomous systems, tool-calling, multi-agent, or LLM orchestration?

Produce a JSON object for each job:

```json
{
  "job_id": 123,
  "score": 7,
  "summary": "中文一句话总结，说明为什么匹配或不匹配",
  "match_reasons": ["reason 1", "reason 2"],
  "gaps": ["gap 1", "gap 2"],
  "agentic_ai_relevant": false
}
```

### Scoring rubric

- **9-10**: Near-perfect fit. The JD reads like it was written for this candidate. Core skills, seniority, and domain all align tightly.
- **7-8**: Strong fit. Most key requirements match. Gaps are minor or in nice-to-have areas.
- **5-6**: Moderate fit. Some important skills match but there are meaningful gaps, or the role is adjacent but not core to the candidate's experience.
- **3-4**: Weak fit. The role is in a related field but requires significantly different skills or experience level.
- **1-2**: Poor fit. Little overlap between the candidate's background and the role's requirements.

### Step 4: Write scores back

Pipe the JSON array into `save-score`:

```bash
echo '<json_array>' | python3 job_digest.py save-score
```

This writes each score into the `jobs` table. The operation is idempotent — jobs that already have a `match_score` won't be overwritten by the database layer.

### Step 5: Report

Print a brief summary of what was scored:

```
Scored N jobs:
- Company A / Position X → 8/10
- Company B / Position Y → 5/10
...
```
