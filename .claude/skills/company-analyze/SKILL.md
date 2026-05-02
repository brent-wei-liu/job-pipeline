---
name: company-analyze
description: >
  Analyze companies in the job pipeline watchlist across five dimensions using web research.
  Use this skill when the user asks to analyze companies, score company quality, run company
  analysis, 分析公司, 评估公司, or when this task runs on a schedule.
---

# Company Analysis

Analyze pending companies from the job pipeline watchlist. For each company, research and score
five dimensions, then save results to the database.

## Workflow

### Step 1: Check pending companies

```bash
cd <project-root> && python3 company_analyze.py --stats
```

If no companies are pending, stop here.

### Step 2: Get the pending list

```bash
python3 company_analyze.py --list-pending
```

This outputs a JSON array of companies needing analysis (not yet analyzed or stale > 7 days).

### Step 3: Research and score each company

For each company in the list, use WebSearch to research current information, then score across
five dimensions. **You MUST use WebSearch to verify facts** — do not rely on training data alone
for funding, revenue, or competitive position.

Research queries to run per company:
- `"{company}" stock ticker NYSE NASDAQ` — determine if publicly traded
- If public: `"{company}" market cap 2025` — get current market cap in USD
- If private: `"{company}" funding round raised investors 2024 2025` — get stage, total raised, top VCs
- `"{company}" revenue ARR customers 2024`
- `"{company}" competitors market position`

#### Scoring rubric (1–10 per dimension):

**1. 资金安全垫 (funding_score)** — How much runway / financial cushion does the company have?
- 9-10: Well-funded (Series C+, >$200M raised, or profitable), 3+ years runway
- 7-8: Solid funding (Series B+, $50-200M raised), 2+ years runway
- 5-6: Adequately funded (Series A/B), 1-2 years runway
- 3-4: Early stage or funding uncertain, <1 year runway possible
- 1-2: Distressed, layoffs, or funding crisis signals

**2. 收入确定性 (revenue_score)** — How predictable / real is the revenue?
- 9-10: Clear revenue/ARR, enterprise contracts, or profitable
- 7-8: Growing revenue, B2B SaaS model with retention signals
- 5-6: Early monetization, some paying customers
- 3-4: Pre-revenue or consumer with unclear monetization
- 1-2: No revenue model or revenue at serious risk

**3. 竞争壁垄 (moat_score)** — How defensible is the competitive position?
- 9-10: Strong proprietary technology, network effects, or switching costs
- 7-8: Clear differentiation, some defensible assets
- 5-6: Competitive but crowded space, moderate differentiation
- 3-4: Commodity product or easily replicated
- 1-2: No clear moat, facing well-resourced incumbents directly

**4. 阶段规模 (stage_score)** — Is the company at a good stage for a senior ML engineer hire?
- 9-10: 50-500 people, product-market fit found, scaling ML
- 7-8: 20-200 people, early scaling phase, high-impact ML work
- 5-6: Very early (<20) or large (500+) — higher risk or bureaucracy
- 3-4: Pre-product or very large enterprise with slow pace
- 1-2: Zombie startup or org not suited for senior IC ML work

**5. 结构性风险 (risk_score)** — Structural/macro risks to the company? (LOWER = better; will be displayed inverted)
- 9-10: High existential risk (regulatory, key-person dependency, pivot risk)
- 7-8: Meaningful risks (single customer, narrow market, commodity risk)
- 5-6: Moderate risks, manageable
- 3-4: Low risk profile, diversified
- 1-2: Very stable, multiple revenue streams, proven business model

**overall_score** = weighted average: funding×0.25 + revenue×0.25 + moat×0.20 + stage×0.20 + (10-risk)×0.10, rounded to nearest integer.

### Step 4: Produce JSON array

Include financial profile fields in every entry. Use two patterns:

**Public company** (has stock ticker → `is_public: 1`):
```json
{
  "company": "NVIDIA",
  "summary": "GPU巨头，AI训练推理芯片垄断地位，市值超$3T",
  "funding_score": 10,
  "revenue_score": 10,
  "moat_score": 10,
  "stage_score": 4,
  "risk_score": 3,
  "overall_score": 9,
  "reasoning": {
    "funding": "上市公司，盈利能力极强，无融资压力",
    "revenue": "数据中心业务ARR数百亿，YoY增长超200%",
    "moat": "CUDA生态+H100/H200垄断地位，切换成本极高",
    "stage": "规模过大（3万员工），ML IC发挥空间受限",
    "risk": "地缘政治出口限制，AMD/自研芯片竞争加剧"
  },
  "is_public": 1,
  "market_cap_usd": 2600.0,
  "funding_stage": null,
  "total_raised_usd": null,
  "notable_investors": null,
  "analyst_model": "claude-sonnet-4-6"
}
```

**Private company** (no ticker → `is_public: 0`):
```json
{
  "company": "Harvey",
  "summary": "面向律师事务所的AI法律助手，A16Z领投，ARR增长迅猛",
  "funding_score": 8,
  "revenue_score": 8,
  "moat_score": 7,
  "stage_score": 8,
  "risk_score": 4,
  "overall_score": 8,
  "reasoning": {
    "funding": "Series B $100M+, A16Z领投，估值超10亿，资金充裕",
    "revenue": "律所客户付费意愿强，ARR快速增长，企业合同为主",
    "moat": "法律领域垂直数据+专业工作流，切换成本高",
    "stage": "约200人，产品成熟，正在扩张ML团队",
    "risk": "法律合规监管风险存在，但律所市场相对稳定"
  },
  "is_public": 0,
  "market_cap_usd": null,
  "funding_stage": "Series B",
  "total_raised_usd": 206.0,
  "notable_investors": ["a16z", "Sequoia", "OpenAI Fund"],
  "analyst_model": "claude-sonnet-4-6"
}
```

Use `null` for fields that don't apply or couldn't be verified.
`market_cap_usd` is in **Billion USD** (e.g. 2600.0 = $2.6T).
`total_raised_usd` is in **Million USD** (e.g. 206.0 = $206M; 1100.0 = $1.1B).

Full JSON array format:
```json
[
  { ...company1... },
  { ...company2... }
]
```

Process companies in batches of 5. After each batch, save and report progress.

### Step 5: Save results

```bash
echo '<json_array>' | python3 company_analyze.py --save
```

### Step 6: Report progress

Print: how many companies were analyzed this run, how many remain pending.
Ask the user if they want to continue with the next batch.

If running as a scheduled task (unattended), continue until all pending companies are analyzed
or 20 companies have been processed (to stay within reasonable context limits).
