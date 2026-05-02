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

### Step 3: Save to database

Pipe the report into save-summary:

```bash
echo '<report_content>' | python3 job_digest.py save-summary
```

### Step 4: Create Gmail draft

Use the Gmail MCP `create_draft` tool to create (not send) a draft email containing the digest. The user reviews in Gmail and sends manually.

**Convert the digest markdown to HTML before calling `create_draft`** — Gmail's UI does not render markdown, so passing raw markdown shows it as source. Pass BOTH parameters:

- `to`: read `DIGEST_RECIPIENT` from `<project>/.env` (parse `KEY=VALUE` lines, value of `DIGEST_RECIPIENT`). If missing, abort with a clear error.
- `subject`: `求职日报 YYYY-MM-DD`
- `body`: the plain markdown text (fallback for non-HTML clients)
- `htmlBody`: the rendered HTML (used by Gmail web/mobile)

Renderer requirements:
- Headings (`#` / `##` / `###`) → `<h1>` / `<h2>` / `<h3>`
- Bullet lists (`- ` or `* `) → `<ul><li>`
- Bold (`**text**`) → `<strong>`
- Inline code (`` `x` ``) → `<code>`
- Links `[text](url)` → `<a href="url">text</a>`
- Blank lines separate paragraphs (`<p>`)
- HTML-escape the source first to prevent injection from any tweet/JD content

Wrap the HTML in a minimal shell with inline styles for readability:
```html
<!DOCTYPE html><html><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:680px;margin:auto;padding:20px;line-height:1.5;color:#222;">
  ...rendered content...
</body></html>
```

Prefer Python's `markdown` library if available (`python3 -c "import markdown; print(markdown.markdown(open('<file>').read()))"`). Otherwise inline a minimal converter:

```python
import html, re
def md_to_html(md):
    safe = html.escape(md)
    out, in_list, para = [], False, []
    bold = re.compile(r'\*\*(.+?)\*\*')
    code = re.compile(r'`([^`]+?)`')
    link = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
    def inline(s):
        s = bold.sub(r'<strong>\1</strong>', s)
        s = code.sub(r'<code>\1</code>', s)
        s = link.sub(r'<a href="\2">\1</a>', s)
        return s
    def flush_para():
        nonlocal para
        if para:
            out.append('<p>' + inline(' '.join(para).strip()) + '</p>')
            para = []
    def close_list():
        nonlocal in_list
        if in_list: out.append('</ul>'); in_list = False
    for line in safe.split('\n'):
        s = line.strip()
        m = re.match(r'^(#{1,3})\s+(.+)$', s)
        if m:
            flush_para(); close_list()
            lvl = len(m.group(1))
            out.append(f'<h{lvl}>{inline(m.group(2))}</h{lvl}>')
        elif s.startswith('- ') or s.startswith('* '):
            flush_para()
            if not in_list: out.append('<ul>'); in_list = True
            out.append(f'<li>{inline(s[2:])}</li>')
        elif not s:
            flush_para(); close_list()
        else:
            close_list(); para.append(s)
    flush_para(); close_list()
    return ('<!DOCTYPE html><html><body style="font-family:-apple-system,'
            'BlinkMacSystemFont,Segoe UI,sans-serif;max-width:680px;'
            'margin:auto;padding:20px;line-height:1.5;color:#222;">'
            + '\n'.join(out) + '</body></html>')
```

If the Gmail MCP isn't available, surface that to the user — do not silently fall back to anything else.

### Step 5: Confirm

Report that the digest has been saved to the DB and a Gmail draft has been created (with the draft id if available).
