# 难抓的公司备忘

记录 watchlist 中暂未覆盖的公司分类，待未来扩展或永久标记跳过。

## 完全自建系统（每家需独立 adapter）

| 公司 | 备注 |
|---|---|
| Google | careers.google.com |
| Apple | jobs.apple.com |
| Meta | metacareers.com |
| Amazon | amazon.jobs |
| Tesla | tesla.com/careers |
| Microsoft | jobs.careers.microsoft.com（有半公开 API 但 TLS cert 指向 azureedge.net） |
| Two Sigma | 量化私募，无公开招聘 API |

## TalentBrew / Radancy 托管（共享平台，每家路径不同）

Intuit、Capital One、Walgreens、UPS、Target、Cargill、P&G、MetLife、DHL、Marriott、Hulu、Sony Pictures、AT&T、Under Armour 等。可写统一 TalentBrew adapter，但每家 endpoint 需单测。

## 其他未实现的 ATS（watchlist 中 enabled=0）

- iCIMS（AMD）
- Avature
- Eightfold（Qualcomm）
- BambooHR
- Gem、Recruitee、SeamlessHiring（ARM）等长尾

## 已支持的 ATS

Greenhouse、Ashby、Lever、Workday、SmartRecruiters、Workable
