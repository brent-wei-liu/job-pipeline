---
name: job-digest
description: >
  Generate a daily job pipeline digest report in Chinese, save to database, and email to user.
  Use this skill whenever the user asks for a job digest, job report, 职位摘要, 求职日报,
  or when this task runs on a schedule.
---

# Job Digest

Generate a concise, insightful daily digest from the job pipeline data, save it to the database, and email it to the user.

## Workflow

### Step 1: Get the raw data

Run this in the job-pipeline project directory:

```bash
python3 job_digest.py query 1
```

This outputs a JSON with: accepted jobs (with match scores), rejected jobs, search runs, and stats.

### Step 2: Generate the digest report

Write the report in Chinese using the **strict template below**. Section headings, sub-row labels, and the final `行动建议` line are required — do not rephrase them. Inside each row you have full freedom on prose quality.

#### Report structure (mandatory, fill in but do not rename)

````
# 求职日报 YYYY-MM-DD

## 今日概览

[ONE narrative paragraph (~3-5 sentences). Cover: total accepted vs rejected counts, how many entered ≥7 high-match band, top 1-2 themes/directions, whether filter rules behaved well today. Don't bullet-list the numbers — make it read like an analyst's lede.]

## 值得重点关注（≥7分）

[For EACH job with match_score ≥ 7, exactly four lines:]

**<Company> / <Title>** — N分
为什么匹配：<2-3 sentences linking the JD to specific resume items (project names, prior employers, technologies). Reference Pinterest LLM search ranking, Meta ads, Yahoo recsys etc by name where relevant. Do NOT just say "技术栈匹配".>
需要注意：<1-2 sentences on the most concrete gap or risk — domain mismatch, level concern, early-stage uncertainty, missing skill. Be specific.>
链接：<url>

[Blank line between jobs.]

## 可以考虑（4-6分）

[For EACH job with 4 ≤ match_score ≤ 6, ONE bullet:]

- **<Company> / <Title>** — N分：<one-sentence verdict explaining what's off — usually wrong domain, wrong level, or thin AI content.>

## 不太合适（≤3分）

[ONE summary line, NO list of every job:]

共 N 条，集中在 <2-4 broad role categories with company examples in parentheses, e.g. "Apple Genius/Specialist、Tesla 制造/项目经理、Microsoft 行政、Amazon 运营">，<one-clause why they leaked through, e.g. "全部为公司关键词搜索的副产物，与 ML 工程无关">。不展开。

## 被过滤掉的（N条）

[Group rejected jobs by reject_reason. ONE bullet per reason. Show count and brief description. If a reason rejected something potentially worth reviewing manually, flag it explicitly:]

- `irrelevant_role`（X条）：<which kinds of roles, which companies>，过滤合理。
- `seniority_too_low`（X条）：<examples>，过滤正确。
- `citizenship_greencard`（X条）：<examples>。<Comment if any high-match got killed — flag for manual review>.
- [other reject_reasons as applicable]

## 洞察

[3-5 bullets. Each focuses on ONE pattern or strategic implication, not a job summary. Lead with a bold short label, then the analytic point.]

- **<Pattern label>**：<the observation, what it means, recommended action>.
- **<Pattern label>**：<...>
- **<Pattern label>**：<...>

行动建议：<one final sentence prioritizing the user's actions for today/this week — which 1-2 jobs to apply to first, what resume tweak is needed, what to defer.>
````

#### Writing principles

- **有洞见，不是流水账**。"今天新增 9 条"没有价值，"今天 Meta FAIR 和一个 GEO 方向的 founding role 都是 8 分，LLM/搜索方向的高质量机会在增加"才有价值。
- **结合简历说匹配**。不要说"技术栈匹配"，要说"你在 Pinterest 做的 LLM search ranking 直接对口这个岗位的核心需求"。简历项目名、雇主名、具体技术（CLIP / BLIP-2 / qLoRA / 多智能体编排）要精确引用。
- **指出风险**。薪资偏低、级别不匹配、公司太早期、领域不熟悉，这些要明确说出来。
- **洞察要具体**。基于今天的数据说话，不要泛泛而谈。给出可执行建议而不是观察。
- **不要省略章节**。即使某档没有 jobs（例如 ≥7 分一条没有），也要保留章节标题并写一行"今天无 7+ 高分匹配。"等说明。
- **`行动建议:` 是终行**，独立成段，不要嵌在洞察 bullets 里。

### Step 3: Save to database (must succeed before Step 4)

Write the report to a temp file (heredoc-safe), pipe it into save-summary, then verify the row landed. `save-summary` dates rows in UTC, so use the UTC date throughout:

```bash
DATE=$(date -u +%Y-%m-%d)
TMP=$(mktemp -t job_digest.XXXXXX.md)
cat > "$TMP" <<'DIGEST_EOF'
<report_content>
DIGEST_EOF
python3 job_digest.py save-summary "$DATE" < "$TMP"
NEW_ID=$(sqlite3 data/jobs.db "SELECT id FROM summaries WHERE date='$DATE' ORDER BY id DESC LIMIT 1")
test -n "$NEW_ID" && echo "saved as summary id=$NEW_ID"
```

**Fallback on failure** (DB locked / corrupted / disk full / verify came back empty): keep the digest on disk and continue to Step 4 in orphan mode, so the user notices the archival gap.

```bash
# only if save-summary or the verify above failed
cp "$TMP" "data/orphan_digest_${DATE}.md"
ORPHAN=1
```

If the orphan file can't be written either, stop and surface the error — do not send a digest that exists nowhere on disk.

### Step 4: Send email via SMTP

The sender sends immediately, with no review step. Each digest date goes out once: a repeat run prints `{"sent": false, "skipped": true, ...}` and exits 0 — treat that as delivered, create no draft, and don't pass `--force` unless the user asked for a resend.

```bash
python3 email_sender.py --date "$DATE"
# only if Step 3 set ORPHAN=1 (subject gets the [ORPHAN: not in DB] prefix):
python3 email_sender.py --date "$DATE" --file "data/orphan_digest_${DATE}.md" --orphan
```

Success prints `{"sent": true, ...}` and exits 0. The sender reads `DIGEST_RECIPIENT`, `GMAIL_SMTP_USER` and `GMAIL_APP_PASSWORD` from `<project>/.env`, uses the subject `求职日报 YYYY-MM-DD`, and renders the markdown to HTML with a plain-text alternative. Use `--dry-run` to render and print headers without connecting.

**Fallback on a non-zero exit** (missing credentials, blocked network, auth failure): keep the error text for Step 5, then create a Gmail draft if the Gmail MCP `create_draft` tool is available. Do not retry the send.

```bash
python3 email_sender.py --date "$DATE" --dry-run --html-out "$(mktemp -t job_digest.XXXXXX.html)"
# add --file/--orphan as above; the same renderer output becomes htmlBody
```

Pass `to` = `DIGEST_RECIPIENT`, `subject` = the Subject line from the dry-run output, `body` = the digest markdown, `htmlBody` = the rendered HTML file's contents. If the Gmail MCP isn't available, report the send failure — do not fail silently.

### Step 5: Confirm

Report that the digest was saved to the DB (or the orphan file path) and the delivery status: sent via SMTP to `<recipient>` / already sent, skipped / SMTP failed with `<error>`, draft created with id `<id>` / SMTP failed with `<error>`, Gmail MCP unavailable.
