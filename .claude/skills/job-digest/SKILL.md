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

Write the report in Chinese. The goal is not just listing jobs — it's providing **actionable insight** so the user can quickly decide what to focus on.

#### Report structure

```
# 求职日报 YYYY-MM-DD

## 今日概览
[一两句话总结今天的情况：新增多少条，多少条高匹配，有没有值得关注的趋势]

## 值得重点关注（≥7分）
[对每个高分 job，用 2-3 句话说明为什么值得关注，不只是罗列字段]

格式：
**公司 / 职位** — N分
为什么匹配：[结合简历解释，不是照搬 match_reasons]
需要注意：[gaps 里最关键的一两点]
链接：[url]

## 可以考虑（4-6分）
[简短列出，每个一句话说明]

格式：
- **公司 / 职位** — N分：[一句话点评]

## 不太合适（≤3分）
[只列公司/职位/分数，不展开]

## 被过滤掉的
[列出被 reject 的，附原因，帮助判断过滤规则是否需要调整]

## 洞察
[这是最重要的部分。分析今天数据中的模式和趋势，比如：]
- 某类岗位最近出现频率变高/变低了吗？
- 高分岗位集中在哪些公司/方向？
- 过滤规则有没有误杀好机会的迹象？
- 和之前几天对比，市场有什么变化？
- 对求职策略有什么建议？
```

#### Writing principles

- **有洞见，不是流水账**。"今天新增 9 条"没有价值，"今天 Meta FAIR 和一个 GEO 方向的 founding role 都是 8 分，LLM/搜索方向的高质量机会在增加"才有价值。
- **结合简历说匹配**。不要说"技术栈匹配"，要说"你在 Pinterest 做的 LLM search ranking 直接对口这个岗位的核心需求"。
- **指出风险**。薪资偏低、级别不匹配、公司太早期，这些要明确说出来。
- **洞察要具体**。基于数据说话，不要泛泛而谈。

### Step 3: Save to database

Pipe the report into save-summary:

```bash
echo '<report_content>' | python3 job_digest.py save-summary
```

### Step 4: Create Gmail draft

Use the Gmail MCP `create_draft` tool to create (not send) a draft email containing the digest. The user reviews in Gmail and sends manually.

Parameters:
- `to`: read `DIGEST_RECIPIENT` from `<project>/.env` (parse `KEY=VALUE` lines, value of `DIGEST_RECIPIENT`). If missing, abort with a clear error.
- `subject`: `求职日报 YYYY-MM-DD`
- `body`: the full digest text (plain markdown is fine — Gmail renders linebreaks)

If the Gmail MCP isn't available, surface that to the user — do not silently fall back to anything else.

### Step 5: Confirm

Report that the digest has been saved to the DB and a Gmail draft has been created (with the draft id if available).
