# Brent Wei Liu

San Francisco Bay Area, CA, USA
Email: brent.wei.liu@gmail.com · Phone: (408) 734-6572 · LinkedIn: [wliu015](https://www.linkedin.com/in/wliu015/)

## Summary

Senior ML Engineer with 10+ years of experience building personalization, recommendation systems, and search engines at scale. Built production personalized ads recommendation models at Meta (Instagram) and personalized content recommendation systems at Yahoo. Led LLM-powered search ranking and agentic AI systems at Pinterest (Llama-3 fine-tuning, CLIP multimodal retrieval). Currently building autonomous multi-agent pipelines for intelligent decision-making and automation.

## Skills

- **AI/ML:** Deep learning, NLP, transformers, personalization & recommendation systems, multi-stage ranking, embedding retrieval, real-time inference
- **LLM & Agentic AI:** RAG, agentic systems, multi-agent orchestration, tool-calling workflows, LLM fine-tuning (qLoRA), multimodal LLM (CLIP, BLIP-2), prompt engineering, LangChain
- **Frameworks & Tools:** PyTorch, HuggingFace, Python, C++, Java, Golang | Kubernetes, Docker, AWS, MLflow, CI/CD
- **MLOps:** End-to-end ML pipelines, model versioning, monitoring, drift detection, automated retraining, large-scale distributed systems

## Experience

### Pinterest, Inc. — San Francisco, CA

**Senior Machine Learning Engineer @ Search AI Systems** · Aug 2024 – Present

*Project: LLM-based Search Ranking & Relevance*

- Built multimodal pipeline using BLIP-2 to convert image content into text signals for LLM-based ranking.
- Fine-tuned Llama-3-8B cross-encoder (qLoRA) and distilled into an efficient student model for low-latency real-time serving.
- Scaled training data 100× via LLM auto-labeling, enabling cross-lingual generalization across 45+ languages.
- Shipped to production: **+2.18% nDCG@20** and **+1.5% global search fulfillment**.

*Project: CLIP-based Multimodal Retrieval*

- Integrated CLIP-based multimodal retrieval into search candidate generation, improving long-tail query nDCG@20 by **+3.12%** and global search fulfillment by **+1.2%** in production A/B experiments.

### Hertz — San Jose, CA

**Staff Software Engineer** · May 2023 – Jun 2024

- Architected real-time decisioning platform for dynamic pricing and fleet optimization. Tech: Java, Python, Golang, AWS.

### Meta Inc. — Menlo Park, CA

**Senior Software Engineer @ Instagram Ads Personalization & Ranking** · Jul 2020 – Jan 2023

- Built and optimized large-scale personalized ads recommendation models serving billions of impressions daily, connecting modeling improvements directly to advertiser ROI and user engagement metrics.
- Led optimization of multi-head deep learning models for ads ranking, reducing memory usage by 10% and improving global ads score by 0.6%.
- Spearheaded early-stage recall optimization in multi-stage ranking system, increasing global ads score by 0.9%.
- Designed user behavior-based personalization features (DIN-style attention pooling) to capture real-time user intent, improving CTR by 1.2% and CVR by 0.8%.
- Led resource allocation auto-tuning system for real-time ads decisioning, improving ads score by ~0.8% quarterly.

### Yahoo Inc. — Sunnyvale, CA

**Senior Software Engineer @ Content Personalization & Recommendation** · Feb 2016 – Jul 2020

- Built personalized content recommendation systems for Yahoo News and Finance, serving millions of users with real-time content ranking and relevance optimization.
- Designed and built end-to-end MLOps platform enabling scalable model deployment, versioning, monitoring, and lifecycle management of ML models.
- Led development of distributed web crawling platform serving 30% of Yahoo News/Finance content.
- Built NLP classification systems including sentiment and multi-class news categorization using BERT.

### Baidu Inc. — Beijing, China

**Software Engineer @ Vertical Search Ranking Team** · Jan 2011 – Dec 2013

- Built vertical novel search engine reaching 100M daily page views and 3M DAU.
- Optimized distributed crawler (epoll) from 1K to 10K QPS; improved search ranking relevance from 81.5% to 90.2%.

## Projects

### Agentic AI Information Pipeline · 2026, Personal Project

- Designed and built autonomous multi-agent system that fetches, processes, and delivers curated intelligence from 5 data sources (Twitter/X, arXiv, GitHub Trending, Hacker News, LinkedIn Jobs).
- Architected fetch/digest decoupled pipeline with SQLite persistence, LLM-powered summarization with reflection pattern (draft → review → improve), and focus-profile-based personalization.
- Built automated job search agent: LinkedIn scraping → intelligent filtering (seniority, visa, education rules) → content-aware dedup → SQLite → FastAPI REST API + retro-futuristic web dashboard.
- Tech: Python, SQLite, FastAPI, OpenClaw (LLM agent framework), cron scheduling, Gmail API integration.

## Education

### Florida International University — Miami, FL

**Master of Science, Computer Science** · Jan 2014 – Jan 2016

### Beihang University — Beijing, China

**Master of Engineering, Software Engineering** · Aug 2008 – Dec 2010
