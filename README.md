# Job Pipeline

自动化求职管线：LinkedIn 职位搜索 → 智能筛选 → SQLite 存储 → LLM 简历匹配评分 → Retro-Futuristic Web UI。

## 架构

```
job-pipeline/
├── db.py                # SQLite 数据层（jobs, search_runs, summaries）
├── job_fetch.py         # LinkedIn 搜索 + 筛选 + 入库（静默，零 token）
├── job_digest.py        # 从 SQLite 生成摘要报告（供 LLM 格式化）
├── backfill_scores.py   # LLM 简历↔JD 匹配打分（1-10 分，可中断续跑）
├── save_report.py       # 一次性手工回填工具（macOS 路径硬编码）
├── api.py               # FastAPI 后端（CRUD + Stats）
├── static/
│   └── index.html       # Retro-Futuristic 单文件前端
└── data/
    ├── jobs.db          # SQLite 数据库（WAL 模式）
    ├── resume.md        # 简历（markdown，评分用，首选）
    └── resume_text.txt  # 简历（纯文本，legacy fallback）
```

### Fetch / Digest / Score 解耦

与 AI Leaders Digest、GitHub Trending Digest、HN Digest、arXiv Digest 保持一致的三文件设计，再加上独立的评分模块：

- **Fetch** — 抓取数据，零 token 消耗，静默存库
- **Digest** — 从 SQLite 读数据，输出 JSON 供 LLM 生成报告
- **Score** — 独立的 LLM 评分（resume↔JD 匹配），幂等可中断
- **DB** — 公共数据层，所有脚本共享

## 搜索配置

**关键词：**
- Agentic AI Engineer
- Machine Learning Engineer
- ML Engineer
- AI Engineer Mid
- Machine Learning Engineer Mid Level

**地点：** San Francisco Bay Area

**时间窗口：** 最近 30 天

**每次最多接受：** 12 条

## 筛选规则

| 规则 | 排除条件 |
|------|----------|
| `seniority_too_low` | 标题含 intern / new grad / entry level / junior |
| `citizenship_greencard` | JD 要求绿卡 / US citizen / security clearance |
| `phd_only` | JD 要求 PhD（且有 "required" / "must have"） |
| `exp_over_13` | 要求 >13 年经验 |

被拒绝的职位也会存入 SQLite（含拒绝原因），方便后续调参。

## SQLite Schema

### jobs 表（19 字段）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 自增主键 |
| linkedin_id | TEXT | LinkedIn 职位 ID（去重用） |
| company | TEXT | 公司名 |
| position | TEXT | 职位名 |
| url | TEXT | LinkedIn 链接 |
| jd_text | TEXT | 职位描述全文 |
| status | TEXT | accepted / rejected |
| reject_reason | TEXT | 拒绝原因 |
| fetched_at | TEXT | 抓取时间 |
| notion_page_id | TEXT | 历史遗留字段（Notion 集成已移除） |
| starred | INTEGER | 标星 0/1 |
| notes | TEXT | 用户备注（已投、面试中等） |
| applied_at | TEXT | 投递时间 |
| match_score | INTEGER | 1-10 分匹配度（NULL = 未评分，由 `backfill_scores.py` 写入） |
| match_summary | TEXT | 中文一句话总结 |
| match_reasons | TEXT | 匹配原因（JSON 数组） |
| match_gaps | TEXT | 差距/缺失（JSON 数组） |
| agentic_ai_relevant | INTEGER | 是否涉及 agentic AI（0/1） |
| scored_at | TEXT | 评分时间 |

**索引：** `idx_jobs_fetched(fetched_at)`、`idx_jobs_status(status)`、`idx_jobs_linkedin(linkedin_id)`

### search_runs 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 自增主键 |
| query | TEXT | 搜索关键词 |
| results_count | INTEGER | 搜索结果数 |
| accepted | INTEGER | 接受数 |
| rejected | INTEGER | 拒绝数 |
| run_at | TEXT | 运行时间 |

### summaries 表（历史 digest 报告）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 自增主键 |
| date | TEXT | 报告日期（YYYY-MM-DD，可重复） |
| content | TEXT | 完整 markdown 内容 |
| created_at | TEXT | 入库时间 |

> Schema 演进采用累加式 `ALTER TABLE ADD COLUMN` 迁移（`db.py:_init_tables()` 启动时检查），无独立迁移框架。早期版本只有「核心/内容」+「过滤/状态」共 10 列；「用户操作」3 列和「LLM 评分」6 列都是后来加的，所以 `.schema jobs` 输出里这 9 列会挤在原始 CREATE TABLE 末尾的一行里。

## Web UI

Retro-Futuristic 风格（CRT 扫描线 + 霓虹色 + Orbitron 字体）。

**三个页面：**
- **JOBS** — 职位列表，支持搜索、筛选（status/starred/天数）、排序、JD 展开、标星、备注编辑、删除；卡片上展示 `match_score` / `match_summary` / `match_reasons` / `match_gaps` / `agentic_ai_relevant` 等评分字段
- **STATS** — 统计仪表板：接受/拒绝/标星数、热门公司、拒绝原因分布
- **SEARCH RUNS** — 搜索历史记录

**启动：**
```bash
cd job-pipeline
python3 api.py
# → http://127.0.0.1:8080
# → Swagger docs: http://127.0.0.1:8080/docs
```

## API Endpoints

```
# Jobs
GET    /api/jobs?days=30&status=accepted&starred=1&search=google&sort=fetched_at&order=desc
       # 返回 jobs 表所有字段，包含 match_score / match_summary / match_reasons / match_gaps / agentic_ai_relevant / scored_at
       # sort 支持: fetched_at | company | position | status | starred | match_score
GET    /api/jobs/{id}
PUT    /api/jobs/{id}          # 仅可更新 status / notes / starred / applied_at（match_* 字段只由 backfill_scores.py 写入）
DELETE /api/jobs/{id}
POST   /api/jobs/{id}/star     # 切换标星

# Search Runs
GET    /api/search-runs?days=30
DELETE /api/search-runs/{id}

# Stats
GET    /api/stats?days=30      # 接受/拒绝/标星数、热门公司、拒绝原因、按天分布（暂未含 match_score 分布）

# Digest Summaries
GET    /api/summaries?limit=30 # 历史 digest 报告（summaries 表）
```

## 定时任务

三个任务按顺序执行，共享同一个 SQLite 数据库（`data/jobs.db`）：

| 任务 | 时间 (PT) | 运行环境 | 源码 / Skill | 说明 |
|------|-----------|----------|-------------|------|
| Fetch | 10:00 AM | 本地 Mac (crontab) | `job_fetch.py` | 抓取 LinkedIn 职位，规则过滤，存入 SQLite |
| Score | 10:30 AM | Cowork (scheduled task) | `.claude/skills/job-score/SKILL.md` | LLM 对最近 1 天未评分的 accepted job 打分 |
| Digest | 11:00 AM | Cowork (scheduled task) | `.claude/skills/job-digest/SKILL.md` | 生成中文日报 → 存 summaries 表 → Gmail 发送 |

### Fetch — 本地 crontab

Fetch 需要访问 LinkedIn，只能在本地跑。设置方法：

```bash
crontab -e
# 加入：
0 10 * * * cd /Users/little_claw/job-pipeline && /usr/bin/python3 job_fetch.py --max-accept 12 >> /Users/little_claw/job-pipeline/data/fetch.log 2>&1
```

手动运行：

```bash
cd /Users/little_claw/job-pipeline && python3 job_fetch.py --max-accept 12
```

### Score — Cowork scheduled task

Cowork 定时任务 `job-pipeline-score`，每天 10:30 自动触发。流程：读 `data/resume.md` → 查最近 1 天未评分的 accepted jobs → LLM 逐个打分（1-10）→ 通过 `job_digest.py save-score` 写回数据库。

Skill 定义：`.claude/skills/job-score/SKILL.md`
Scheduled task 文件：`~/Documents/Claude/Scheduled/job-pipeline-score/SKILL.md`

手动运行（在 Cowork sidebar "Scheduled" 点 "Run now"）。也可以用旧的 `backfill_scores.py`（通过 OpenClaw gateway）：

```bash
python3 backfill_scores.py                       # 评所有未打分的 accepted 职位
python3 backfill_scores.py --limit 10             # 限制 10 条
python3 backfill_scores.py --dry-run              # 只列出，不调 LLM
```

### Digest — Cowork scheduled task

Cowork 定时任务 `job-pipeline-digest`，每天 11:00 自动触发。流程：`job_digest.py query 1` 获取 JSON → LLM 格式化为中文日报（含洞察分析）→ `job_digest.py save-summary` 存数据库 → Gmail 发送至 brent.wei.liu@gmail.com。

Skill 定义：`.claude/skills/job-digest/SKILL.md`
Scheduled task 文件：`~/Documents/Claude/Scheduled/job-pipeline-digest/SKILL.md`

手动查询：

```bash
python3 job_digest.py query 1      # 最近 1 天的 jobs（JSON）
python3 job_digest.py query 7      # 最近 7 天
python3 job_digest.py stats 30     # 快速统计

# 手动存报告
echo '<report>' | python3 job_digest.py save-summary
# 手动存评分
echo '<json>' | python3 job_digest.py save-score
```

### LLM 评分格式

Score 和 Digest 都使用相同的评分结构：

```json
{
  "job_id": 123,
  "score": 8,
  "agentic_ai_relevant": true,
  "match_reasons": ["LLM ranking experience", "Search/retrieval background"],
  "gaps": ["No explicit Kubernetes mention"],
  "summary": "强匹配，候选人在 LLM search ranking 方向有直接经验"
}
```

**幂等设计**：只处理 `match_score IS NULL` 的行，可以随时中断、重跑。

## 依赖

- Python 3.10+
- `requests`（fetch 用）
- `fastapi` + `uvicorn`（Web UI 用）
- SQLite（内置）

```bash
pip3 install fastapi uvicorn requests
```

## 历史

- 2026-03-24: 初版（单体脚本 `run_pipeline_brent.py`，直接写 Notion）
- 2026-03-25: 重构为三文件架构，加 SQLite、去重、Retro UI，停止 Notion 写入
- 2026-04: 加 LLM 简历匹配评分（`backfill_scores.py` + `data/resume.md`），新增 `summaries` 表存历史 digest，jobs 表新增 `starred` / `notes` / `applied_at` / 6 个 match_* 字段
- 2026-04-22: 迁移 Score 和 Digest 到 Cowork scheduled tasks（原 Hermes agent 已下线）。新增 `job-score` / `job-digest` 两个 skill。Web UI 加 SCORE 列 + match detail 展开面板 + agentic 标记。API 支持按 match_score 排序。`job_fetch.py` 加 logging。
