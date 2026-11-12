# 因果簿（causal_ledger）原型设计

## 一句话

先建立可追溯的信息账簿：内容 → 主张与出处 → 明确预测 → 人工观察与结算。规则学习是后续目标，不把预测命中冒充因果证明。

## 三体环

```
规则/先验 ──生成──▶ 预测（带截止日）
    ▲                      │
  修正                     ▼
    └──── 簿（结算） ◀── 观测（真实世界的内容 / 硬信号）
```

- 古法与现代模式库只提供**结构**与**假设**，对可信度零直接影响。
- 与任何其他项目无代码或运行依赖。
- 上图是长期研究目标；当前只实现账簿，不实现规则引擎或自动学习。

## 原型范围（结算为心的最薄纵切片）

做：

1. 导入内容（文本 / 文件 / URL / 手工 JSON）
2. 盲读抽取（LLM 只抽取不裁判；提示词里不含用户假设）
3. 出处链（isnad）：每条主张记录"我从谁读到"（reporter）与"链的根"（origin）
4. 同主张合并（canonical_key，包含时间范围）与来源名称去重；不代表已验证证据独立
5. 来源档案：每个来源 × 领域保存类型先验；不自动视作真实可靠率
6. 确定性实验分（无 LLM 参与；未经校准，不是真伪概率）
7. 预测：明确内容、截止日、核验方法，可选人工登记概率 p；p 一经登记不再随主张分数变化
8. 人工结算：hit / miss / void 必须附依据；仅有 p 的 hit/miss 计算 Brier，不回传来源或主张评分
9. 手动/单次日报：待读队列（未核验）、到期、已结算、新主张、实验分变动、来源档案
10. RSS/Atom 订阅：登记、离线导入或安全抓取、条目去重、未读推荐、标记已读；不调用模型，不自动转为主张
11. 导入内容查询与桥接：`docs` / `doc` / `search` 只读检索；`ingest` 把订阅条目转为文档（默认不抽取，`--extract` 需 `--allow-remote`）；`import-json --dir` 批量导入（逐文件独立、失败不中断）
12. 推送与历史：`daily` 生成一次推送并写入本地 `digests` 历史，`digests` 查看；`daily --brief` 输出紧凑文本简报（少量条目 + 同题源/接账状态 + 台账状态 + 待办）；跨源链接/标题相似只在推荐依据里提示"疑似重复（仅提示，未验证）"，不合并、不改分
13. 人工接账（离线）：`ingest ID --claim "一句话主张"` 把订阅条目转成文档+主张+出处链（不调用模型，来源链由人确认）；`import-json --feed-item ID` 可把人工 JSON 的多条主张关联到同一条目
14. 可视化简报：`daily --html --output 新文件.html` 生成单文件两层 HTML；`daily --html-dir DIR [--fetch-details]` 生成**多页简报站**（`DIR/index.html` + `DIR/detail/item-<id>.html`）：简报卡片含来源/时间/同题源/接账状态/摘要摘录与"详情 → / 原文 ↗"，详情页含完整摘要或联网抓取的原文正文、出处与时间、已接账主张与预测、可执行命令；无外部资源/JS，本地打开
15. 关注主题：`watch add/list/remove/enable/disable` 维护关注词；`inbox`/`daily` 在未给 `--topic` 时默认按启用主题筛选（仅标题/摘要），`--topic` 覆盖
16. 本地单应用：`serve --reports 目录` 只服务事报阅读、往期归档与末尾备注；默认仅本机（127.0.0.1），局域网需显式 `--host 0.0.0.0` 并建议 `--token`；备注写入 `notes` 表，作为下一次取舍的潜意识，不是命令
17. 个体因果基线：`profile import/show/clear` 管理本地档案；出生/事件/心境等"保险箱"字段用 **Windows DPAPI 加密**（`vault` 表），公开 JSON 只留派生可用部分；`profile unlock --reason` 按次解密并写入 `vault_audit`；`daily --mode auto|general|light|individual` 选择通线/轻个体线/个体线；无档案或基线不完整自动降级通线；个体线把领域/约束/位置作为"强潜意识"影响排序，并在简报加"与你有关/你在哪一段"（启发式，不是因果证明）
18. 7 天校准：`calibrate [--days 7] [--apply]` 读近 N 天末尾备注，只有同时出现领域词与明确正/负向词才产生小幅权重调整（±0.05/域，单域上限 ±0.15）；先预览，`--apply` 写入 `calibrations`；`daily` 自动应用最近一次校准，只调权重、不改档案原文
19. 盲评与消融：`eval blind --dir DIR` 生成 A/B 两份简报（通线/个体线，归属随机隐藏），`eval vote A|B` 记录并揭晓，`eval stats` 统计；`eval ablate` 对比通线、完整个体线、无校准、均匀权重、轻个体线五个变体的前 4 条与重叠/同序；不宣称准确率
20. 零月费云发布：`cloud --config config/feeds.json --reports reports` 在临时库抓订阅 → 生成静态报告与归档 → 可选 Server酱推送；GitHub Actions 每天 23:30 UTC（07:30 CST）运行，产物由 Pages 发布；不读取本地档案/保险箱，是云端通线
21. 新版《今日事报》：`daily_page.render_daily_page` 输出链式页（今日判断 → 因/果/我的链路/可能看错 → 算法链），无待办/台账/KPI；`cloud` 已切到该页；旧 `render_brief_html`/`html_site` 保留为 legacy 兼容，不再接新功能
22. 周报与保留：`weekly_page.render_weekly_page` 输出《本周事报》（周范围 + 链 + 本周信号清单）；`cloud --weekly-only` 汇总上一周 `reports/data/*.json`，成功后删除该周日报 HTML/JSON，只保留周报；周一 07:30 自动执行

已实现：RSS/Atom 订阅、去重待读推荐、单次 `daily` 推送与本地历史、文本简报与 HTML 可视化简报/多页简报站、文档/主张查询、订阅条目转文档与人工接账、批量导入、跨源重复提示、关注主题、本地单应用、个体因果基线（通线/轻个体线/个体线）、保险箱（DPAPI 加密 + 解锁审计）、7 天校准、盲评与消融、零月费云发布（GitHub Actions + Pages + Server酱，使用新版《今日事报》链式页）、**周报与日报保留策略**、CLI 全局免责声明。尚未实现：旧 `daily --brief/--html/--html-dir` 迁移到新页、本地周报视图、备注云端回传、双端同步、实体关系图、证据独立性验证、自动事实核查、因果识别、来源校准、规则学习、Web UI。

## 评分规则（scoring.py，纯函数，全部留痕）

来源类型先验 p0：

| 类型 | p0 |
|---|---|
| primary_data 一手数据 | 0.80 |
| official 官方 | 0.65 |
| participant 当事人 | 0.60 |
| institutional_media 机构媒体 | 0.55 |
| analysis 分析/推断 | 0.45 |
| anonymous 匿名转述 | 0.25 |
| unknown | 0.30 |

来源 × 领域先验权重：α/(α+β)，伪计数 k=4（α0 = p0·k，β0 = (1−p0)·k）。这些数值是未校准的工程假设；来源类别也可能被误识别。当前不根据预测结算修改 α/β。

主张实验分：

1. 按 origin 来源 ID 去重（同名来源先合并；无引用时 reporter 即 origin）；不同来源不保证不同证据，共同上游仍需核验
2. 合成：c = 1 − Π(1 − r_i)，仅作实验排序，不解释为真伪概率
3. 单来源封顶 0.60
4. 陈述阶段上限：intent 0.50 / plan 0.65 / formation 0.80 / manifestation 1.00
5. 总上限 0.85
6. hard_signal 仅保存待核查线索，不增加来源或分数
7. 增加、命中、未命中或作废预测均不改变主张评分

每一步写入 trace。未知 layer 归为 intent；分数保留 3 位小数。预测命中不证明主张的解释机制；预测失败不自动证明引用它的来源失实。Brier 只评价明确登记的那条预测概率，不能代替事实核查或因果识别。

## 数据模型（SQLite）

- sources(id, name, type)
- source_domain_stats(source_id, domain, alpha, beta, n)
- documents(id, title, url, reporter_source_id, raw, imported_at)
- claims(id, canonical_key UNIQUE, text, domain, layer, hard_signal, credibility, trace)
- chains(id, claim_id, document_id, reporter_source_id, origin_source_id, quote)
- predictions(id, claim_id, text, due_date, check_hint, p, status, brier, note)
- credibility_log(id, claim_id, old, new, reason, at)
- subscriptions(id, name, url UNIQUE, enabled, checked_at, last_error, created_at)
- feed_items(id, feed_id, item_key, title, url, summary, published_at, published_raw, fetched_at, read_at, UNIQUE(feed_id, item_key))
- feed_ingests(id, feed_item_id UNIQUE, document_id, created_at)：订阅条目 → 文档的一次性桥接记录；UNIQUE 保证重复 ingest 幂等
- digests(id, created_at, kind, path, ok, summary_json)：本地推送/日报历史；只记录生成过什么，不代表已投递或定时运行
- watchlists(id, topic UNIQUE, enabled, created_at)：关注主题；只用于筛选标题/摘要，不评分、不核验
- notes(id, day, text, created_at)：本地应用里"事报末尾备注"；只作为下一次取舍的潜意识，不是命令
- profiles(id CHECK(id=1), data_json, mode CHECK(auto/general/light/individual), completeness, updated_at)：个体因果基线；公开 JSON 只含派生可用部分
- vault(id CHECK(id=1), cipher, fields_json, created_at, updated_at)：保险箱密文（Windows DPAPI，绑定本机用户）；只存字段名与密文
- vault_audit(id, action, reason, created_at)：seal/unlock/clear 审计
- calibrations(id, created_at, days, notes_count, deltas_json)：7 天校准历史；只存领域增量，不存备注原文
- blind_votes(id, created_at, run_id, day, choice, a_kind, b_kind, winner_kind)：盲评投票

订阅相关约束：单源 ≤2 MiB / ≤100 条，单次 ≤20 个启用源，推荐只扫描最新 5000 条合格候选；`published_at` 是"源称发布时间"（无法解析或时区缺失为 NULL，不编造），`fetched_at` 是首次采集时间；重复采集保留首次内容与已读状态。`feed_items` 不是主张，订阅阅读不会改变任何分数。

以上为逻辑字段摘要；完整列、默认值、外键与 CHECK 约束以 `causal_ledger/db.py` 的 `SCHEMA` 为准（18 张表 + 2 个索引）。

## 使用与边界

在项目目录运行（Python 3.11+；运行时仅依赖标准库；测试需要 pytest）：

```bash
python -m causal_ledger --help
python -m causal_ledger --db demo.sqlite3 import-json examples/sample_a.json
python -m causal_ledger --db demo.sqlite3 import-json examples/sample_b.json
python -m causal_ledger --db demo.sqlite3 explain 1
python -m causal_ledger --db demo.sqlite3 report

# 订阅（不调用模型）：登记 → 离线导入/安全抓取 → 待读 → 已读 → 单次日报
python -m causal_ledger --db demo.sqlite3 feed add --name 名称 --url https://example.org/feed.xml
python -m causal_ledger --db demo.sqlite3 feed import-file 1 本地订阅.xml
python -m causal_ledger --db demo.sqlite3 collect
python -m causal_ledger --db demo.sqlite3 inbox --days 7 --topic 关键词
python -m causal_ledger --db demo.sqlite3 item 1
python -m causal_ledger --db demo.sqlite3 read 1
python -m causal_ledger --db demo.sqlite3 daily --no-fetch --output 新日报.txt
python -m causal_ledger --db demo.sqlite3 daily --brief --no-fetch --output 新简报.txt
python -m causal_ledger --db demo.sqlite3 daily --html --no-fetch --output 新简报.html
python -m causal_ledger --db demo.sqlite3 daily --html-dir 新站点目录 --fetch-details --no-fetch
python -m causal_ledger --db app/app.sqlite3 serve --reports app/reports --host 127.0.0.1 --port 8765
python -m causal_ledger --db app/app.sqlite3 profile import individual-baseline.json
python -m causal_ledger --db app/app.sqlite3 daily --brief --no-fetch --mode auto
python -m causal_ledger --db app/app.sqlite3 calibrate --days 7
python -m causal_ledger --db app/app.sqlite3 calibrate --days 7 --apply
python -m causal_ledger --db app/app.sqlite3 eval blind --dir eval-runs/2026-11-13
python -m causal_ledger --db app/app.sqlite3 eval vote A --dir eval-runs/2026-11-13
python -m causal_ledger --db app/app.sqlite3 eval ablate
python -m causal_ledger cloud --config config/feeds.json --reports reports --no-fetch --no-push
python -m causal_ledger cloud --reports reports --weekly-only --no-push
python -m causal_ledger --db demo.sqlite3 watch add 关键词

# 查询与桥接（离线）
python -m causal_ledger --db demo.sqlite3 docs
python -m causal_ledger --db demo.sqlite3 doc 1
python -m causal_ledger --db demo.sqlite3 search 关键词 --domain tech --min 0.5
python -m causal_ledger --db demo.sqlite3 import-json --dir 目录/
python -m causal_ledger --db demo.sqlite3 ingest 1
python -m causal_ledger --db demo.sqlite3 ingest 1 --claim "一句话主张" --domain tech --layer formation
python -m causal_ledger --db demo.sqlite3 ingest 1 --extract --allow-remote
python -m causal_ledger --db demo.sqlite3 digests

python -m pytest tests -q
```

- 两份样例完全虚构，不代表真实报道；演练数据库不要与真实资料混用。
- `import --text/--file/--url` 调用外部模型，必须显式加 `--allow-remote`，可能产生费用。密钥优先级：`CL_LLM_API_KEY`（可配 `CL_LLM_BASE_URL`、`CL_LLM_MODEL`）→ `DASHSCOPE_API_KEY`（默认 DashScope，可配 `CL_LLM_BASE_URL`）→ `OPENAI_API_KEY`（可配 `OPENAI_BASE_URL`、`CL_LLM_MODEL`）。
- `import-json` 是离线手工路径；JSON 中 `predictions[].p` 视同人工登记。`import` 模型路径会剥离模型返回的 `p`，概率只能由人显式给出。
- `predict ID --text TEXT --due YYYY-MM-DD --hint HINT [--p 0.7]` 登记预测；概率不从主张评分推导，省略就不算 Brier。相同内容、截止日与核验方法的重复登记保留原概率。
- `settle ID hit|miss|void --note 依据` 是人工记录，不是系统已核实；miss 必须等截止日结束。不要通过事后登记制造漂亮命中率。
- `feed add` 只登记订阅、不联网；`collect`/`daily` 才抓取，逐源记录失败，任一路失败仍保留其余结果并提示不完整。订阅条目是未核验阅读候选，不会自动变成主张。
- `ingest ID` 把订阅条目转为文档：默认只用订阅摘要、不抽取、不联网；`--fetch` 抓取原文全文但不调用模型；`--extract` 才做盲读抽取且必须加 `--allow-remote`（可能产生费用）；`--claim "一句话主张"` 是离线人工接账：创建文档+主张+出处链，来源链由人确认、不调用模型。转换与阅读都不表示内容已核实。
- `import-json FILE --feed-item ID` 可在导入人工 JSON 的同一事务里把文档关联到订阅条目（多主张离线接账）；条目已转换时拒绝重复导入。
- `docs`/`doc`/`search` 是只读查询；`search` 为子串匹配（每个关键词都必须命中主张文本/键或文档标题），匹配不等于相关、也不代表真实；`--since` 按本机自然日换算 UTC。
- 跨源疑似重复：`recommend` 用 URL 归一化（去跟踪参数/www/末尾斜杠）+ 标题指纹相似度，在推荐依据里追加"疑似重复：与 I#x 的链接/标题相似（仅提示，未验证）"；**只提示，不合并、不删条目、不改分**。
- `import-json --dir 目录` 逐文件独立导入，单文件失败不中断、不写入半成品，最终如有失败则非零退出。
- `daily` 每次生成都会写入本地 `digests` 历史（kind/ok/path/参数），`digests` 只读列出；这不代表已投递、也不代表定时任务在运行。
- `daily --html --output 新文件.html` 生成自包含 HTML 可视化简报：统计卡（未读/已接账/主张/待结算/到期/历史）、待办卡片、条目卡片（来源/同题源/接账状态）、近 7 天采集与来源分布的内联 SVG 图；无外部资源、无 JS，浏览器本地打开；仍不自动外发、不安装定时任务。
- `watch add/list/remove/enable/disable` 维护关注主题；`inbox`/`daily`（含 `--brief`/`--html`）未给 `--topic` 时默认用启用主题筛选标题/摘要，`--topic` 显式覆盖。主题只是筛选，不评分、不核验。
- `serve --reports 目录` 是本地单应用：首页列出往期事报，`/YYYY-MM-DD` 阅读并注入"末尾备注"表单，`POST /notes` 写入 SQLite；默认绑定 127.0.0.1，局域网访问需 `--host 0.0.0.0` 并建议 `--token`；只读/记录，不做核验、不推送、不自动外发。
- `profile import FILE` 导入问卷 JSON；`profile show` 只显示派生特征与保险箱字段名；`profile unlock --reason` 按次解密并审计；`profile audit` 查看记录；`profile clear` 删除档案与保险箱。`daily --mode auto` 无档案/不完整时降级通线；轻个体线只调排序；个体线额外显示"与你有关/你在哪一段"。领域匹配是关键词启发式，不是因果证明。
- `calibrate --days 7` 只预览备注 → 权重的调整；`--apply` 写入 `calibrations`，之后 `daily` 自动应用最近一次；只调整领域权重，不改档案原文，证据只记录备注编号。
- `eval blind --dir DIR` 生成通线/个体线 A/B（归属随机、先不看 `blind.json`）；`eval vote A|B` 记录并揭晓；`eval stats` 统计；`eval ablate` 对比五个变体的前 4 条与重叠/同序。样本小只作方向参考，不宣称准确率。
- `cloud --config ... --reports ...` 是零月费云发布：临时库抓订阅、生成**新版《今日事报》链式页**与归档、可选 Server酱；不读档案/保险箱。GitHub Actions 每天 07:30 CST 跑一次，Pages 发布；部署步骤见 [零月费部署](零月费部署.md)。
- `cloud --weekly-only` 汇总上一周日报数据为《本周事报》，页内保留"本周信号清单（保留出处）"，成功后删除该周日报；周一 07:30 自动执行。详见 [周报与保留策略](周报与保留策略.md)。
- 日报窗口采用本机日期的自然日；结算和导入时间以 UTC 存储。`daily` 是单次手动命令，不安装定时任务、不外发；可用 `--no-fetch` 纯离线生成，`--output` 只写新文件、不覆盖。
- 畸形 JSON 在写库前拒绝、不写业务表（首次打开仍会创建 SQLite 文件与空表）；一次导入的数据库修改是原子的。
- 当前为初始开发数据结构，不提供早期实验数据库的自动迁移；测试应使用新数据库。
