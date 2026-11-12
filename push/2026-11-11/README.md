# 今日推送 · 2026-11-11（首次真实公开源演示）

**这是一次性生成的本地演示推送，不是已部署的定时任务，也没有发送给任何第三方。**
内容来自公开 RSS 原文标题/摘要，仅供阅读线索；**所有条目均未核验**，不代表真实性或因果。
页面中的链接指向原始来源，版权与准确性归原作者/发布方。

## 本次使用的公开源（7 个，均可抓取）

| # | 源 | URL | 本次新增条目 |
|---|---|---|---:|
| F#1 | 少数派 | https://sspai.com/feed | 10 |
| F#2 | IT之家 | https://www.ithome.com/rss/ | 60 |
| F#3 | Solidot | https://www.solidot.org/index.rss | 20 |
| F#4 | 爱范儿 | https://www.ifanr.com/feed | 20 |
| F#5 | MIT Technology Review | https://www.technologyreview.com/feed/ | 10 |
| F#6 | Hacker News | https://hnrss.org/frontpage | 20 |
| F#7 | NPR | https://feeds.npr.org/1001/rss.xml | 10 |

合计 **150 条**未读候选；2 天窗口内未读 **135 条**。详细版按"近 2 天 + 每源≤2 + 时间降序"选 10 条；简报版先用跨源同题信号、再按每源轮换取 5 条，避免单一来源刷屏。

## 探测过但本次未使用的源（如实记录）

| 源 | 结果 | 原因 |
|---|---|---|
| BBC World | 失败 | 本机到目标域 TCP/TLS 不通（读超时，无响应头） |
| The Guardian World | 失败 | 同上 |
| Nature | 抓取成功、解析失败 | 返回的不是 RSS2/Atom，当前解析器不支持（疑似 RDF/RSS1.0 或反爬页） |
| arXiv cs.AI | 可用但太慢 | 单次约 62 秒，本次未纳入；适合异步/降频采集 |
| 36氪 | 失败 | 官方 RSS 已失效，返回 HTML |

> 说明：源可用性会随网络环境与站点变化；失败会逐源记录在 `feed list` 的 `last_error`，不会中断其它源。

## 边界

- **未核验**：标题/摘要由来源自称，人工接账的主张由人从标题/摘要改写，均未做事实核查；同题源数只说明"看起来是同一题材"，不是独立证据。
- **未调用模型**：采集、简报、人工接账与预测登记全程未使用 LLM、未消耗 API 额度。
- **未自动外发**：只写本地文件；`daily` 不安装定时任务。
- **主张由人确认**：`ingest --claim` 是人工路径（来源链由人指定）；`feed_items` 本身不会自动变成 `claims`。
- **预测由人登记**：`predict` 的概率与截止日由人给出；命中/失败不自动回写来源或主张评分。
- **疑似重复只提示**：若不同源出现链接/标题相似的条目，会在推荐依据里提示"疑似重复（仅提示，未验证）"，不自动合并。

## 文件与复现

- `push.sqlite3`：本次订阅/条目/台账/历史库（150 条条目；人工接账 1 条主张、2 条人工预测；16 条 digest 记录，含文本/HTML 多次重生成）。
- `今日推送.txt`：详细版推送（采集状态 + 待读推荐 + 账簿日报，逐条含长推荐依据，作为对照存档）。
- `site/index.html` + `site/detail/item-*.html`：**多页简报站（推荐形式）**——首页是简报（统计卡、待办、4 张卡片含摘要摘录）；"详情 →"进入独立详情页（完整**联网抓取的原文正文**、出处与时间、已接账主张/预测、可执行命令、上一条/下一条、返回简报）。本次原文抓取成功 4/4，失败会回退订阅摘要并标注。
- `今日简报.html`：单文件两层可视化版（不拆页，详情用页内锚点），适合单文件分享/存档。
- `今日简报.txt`：同内容的文本版，便于终端/纯文本环境查看。
- 简报内容说明：
  - 4 条"值得注意"（跨源同题优先，再按每源轮换）；
  - 一行台账状态（未读/已接账/主张/到期/待结算/今日新增）；
  - 待办按需出现：`结算到期：P#2 …（settle …）`、`接账：ingest 121 --claim …`、`predict …`；
  - 关注主题未设置时使用全部订阅；设置 `watch add 关键词` 后，`inbox`/`daily` 默认按主题筛选，`--topic` 可覆盖。
- 本次演示还人工接账了 IT之家 I#11（"Adobe 公布四项 AI 实验项目"）：`ingest 11 --claim …` → 主张 #1 → 两条人工预测（其中一条当日到期），用于展示"条目→主张→预测→结算提示"的闭环；**这些都未经核验，来源链与预测均由人工确认**。
- 复现（在 `D:\硬理搜索` 下）：

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m causal_ledger --db push/2026-11-11/push.sqlite3 feed list
python -m causal_ledger --db push/2026-11-11/push.sqlite3 inbox --days 2 --limit 10 --per-source 2
python -m causal_ledger --db push/2026-11-11/push.sqlite3 claims
python -m causal_ledger --db push/2026-11-11/push.sqlite3 due
python -m causal_ledger --db push/2026-11-11/push.sqlite3 digests
python -m causal_ledger --db push/2026-11-11/push.sqlite3 watch list
python -m causal_ledger --db push/2026-11-11/push.sqlite3 daily --brief --no-fetch --days 2 --limit 10 --per-source 2
python -m causal_ledger --db push/2026-11-11/push.sqlite3 daily --html --no-fetch --days 2 --limit 10 --per-source 2 --output push/2026-11-11/新简报.html
python -m causal_ledger --db push/2026-11-11/push.sqlite3 daily --html-dir push/2026-11-11/新站点 --fetch-details --no-fetch --days 2 --limit 10 --per-source 2
```

- 每日新推送建议用新日期目录，避免覆盖已有文件（`daily --output` 不覆盖已存在文件）。
