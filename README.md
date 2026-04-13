# 🦀 Job Pipeline

自动化求职管线：LinkedIn 职位搜索 → 智能筛选 → SQLite 存储 → Retro-Futuristic Web UI。

## 架构

```
job-pipeline/
├── db.py           # SQLite 数据层（jobs, search_runs）
├── job_fetch.py    # LinkedIn 搜索 + 筛选 + 入库（静默）
├── job_digest.py   # 从 SQLite 生成摘要报告（供 LLM 格式化）
├── api.py          # FastAPI 后端（CRUD + Stats）
├── static/
│   └── index.html  # Retro-Futuristic 单文件前端
└── data/
    └── jobs.db     # SQLite 数据库
```

### 三文件模式（Fetch / Digest 解耦）

与 AI Leaders Digest、GitHub Trending Digest、HN Digest、arXiv Digest 保持一致的设计：

- **Fetch** — 抓取数据，零 token 消耗，静默存库
- **Digest** — 从 SQLite 读数据，输出 JSON 供 LLM 生成报告
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

### jobs 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| linkedin_id | TEXT UNIQUE | LinkedIn 职位 ID（去重用） |
| company | TEXT | 公司名 |
| position | TEXT | 职位名 |
| url | TEXT | LinkedIn 链接 |
| jd_text | TEXT | 职位描述全文 |
| status | TEXT | accepted / rejected |
| reject_reason | TEXT | 拒绝原因（nullable） |
| notion_page_id | TEXT | 历史遗留字段（已停止写入 Notion） |
| starred | INTEGER | 标星 0/1 |
| notes | TEXT | 用户备注（已投、面试中等） |
| applied_at | TEXT | 投递时间 |
| fetched_at | TEXT | 抓取时间 |

### search_runs 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| query | TEXT | 搜索关键词 |
| results_count | INTEGER | 搜索结果数 |
| accepted | INTEGER | 接受数 |
| rejected | INTEGER | 拒绝数 |
| run_at | TEXT | 运行时间 |

## Web UI

Retro-Futuristic 风格（CRT 扫描线 + 霓虹色 + Orbitron 字体）。

**三个页面：**
- **JOBS** — 职位列表，支持搜索、筛选（status/starred/天数）、排序、JD 展开、标星、备注编辑、删除
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
GET    /api/jobs?days=30&status=accepted&starred=1&search=google
GET    /api/jobs/{id}
PUT    /api/jobs/{id}          # 更新 status/notes/starred/applied_at
DELETE /api/jobs/{id}
POST   /api/jobs/{id}/star     # 切换标星

GET    /api/search-runs?days=30
DELETE /api/search-runs/{id}

GET    /api/stats?days=30
```

## Cron Jobs

| 任务 | 时间 | 说明 |
|------|------|------|
| Job Pipeline - Fetch | 每天 10:00 AM PT | 静默抓取，不发消息 |
| Job Pipeline - Digest | 每天 10:05 AM PT | 生成报告 → WhatsApp |

## 手动运行

```bash
# Fetch（搜索 + 存库）
python3 job_fetch.py --max-accept 12

# Digest（查询 + 输出 JSON）
python3 job_digest.py query 1      # 最近 1 天
python3 job_digest.py query 7      # 最近 7 天
python3 job_digest.py stats 30     # 快速统计
```

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
