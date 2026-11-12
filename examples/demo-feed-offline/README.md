# demo-feed-offline：完全虚构的「订阅 → 去重 → 待读 → 已读 → 本地日报」离线演练

**本目录所有标题、摘要、日期、条目均为虚构，仅用于功能演练；不联网、不调用模型、不构成任何真实证据，也不代表任何真实来源、事实或结论。**

演示库中只有 `subscriptions`（1 条）和 `feed_items`（3 条）；`claims`、`documents`、`sources` 全部为 0——
订阅阅读队列不生成主张，也不参与实验分。

## 产物

| 文件 | 说明 |
| --- | --- |
| `make_demo_feed.py` | 生成虚构 RSS2；`pubDate` 相对生成时刻（近期=2 天前，旧=30 天前），另有 1 条故意不写 `pubDate` |
| `demo-feed.xml` | 生成的虚构 RSS2 源文件（UTF-8，含 channel title，3 条 item，主题词「电池」） |
| `demo.sqlite3` | 演示库：F#1 订阅 + 3 条条目 + I#1 已读 |
| `daily.txt` | `daily --no-fetch` 实际生成的日报（27 行，含「未联网采集 / 未核验 / 发布时间未知」边界说明） |
| `verify_demo.py` | 只读验证脚本（19 项检查：计数、去重、integrity、foreign key、已读、日报边界） |

## 复现步骤（工作目录 `D:\硬理搜索`，PowerShell，Python 3.11.9）

```powershell
$env:PYTHONIOENCODING='utf-8'
Remove-Item examples/demo-feed-offline/demo.sqlite3, examples/demo-feed-offline/daily.txt -ErrorAction SilentlyContinue
python examples/demo-feed-offline/make_demo_feed.py
python -m causal_ledger --db examples/demo-feed-offline/demo.sqlite3 feed add --name 虚构演练订阅 --url https://example.org/demo-feed.xml
python -m causal_ledger --db examples/demo-feed-offline/demo.sqlite3 feed import-file 1 examples/demo-feed-offline/demo-feed.xml
python -m causal_ledger --db examples/demo-feed-offline/demo.sqlite3 feed import-file 1 examples/demo-feed-offline/demo-feed.xml
python -m causal_ledger --db examples/demo-feed-offline/demo.sqlite3 feed list
python -m causal_ledger --db examples/demo-feed-offline/demo.sqlite3 inbox --topic 电池 --days 7
python -m causal_ledger --db examples/demo-feed-offline/demo.sqlite3 item 1
python -m causal_ledger --db examples/demo-feed-offline/demo.sqlite3 read 1
python -m causal_ledger --db examples/demo-feed-offline/demo.sqlite3 inbox --topic 电池 --days 7
python -m causal_ledger --db examples/demo-feed-offline/demo.sqlite3 daily --no-fetch --output examples/demo-feed-offline/daily.txt
python examples/demo-feed-offline/verify_demo.py
```

`feed add` 只登记 URL，不抓取；`feed import-file` 只读本地 XML；`daily --no-fetch` 明确不请求订阅网站。
`daily --output` 不覆盖已存在文件（复跑会报错退出，属预期行为）。

## 预期结果

- 首次导入：`新增 3，重复 0`；同一文件再次导入：`新增 0，重复 3`（按 `guid` 去重）。
- `inbox --topic 电池 --days 7` 推荐 I#1（2 天前发布）与 I#2（发布时间未知）；30 天前的 I#3 被天数窗口排除。
- `read 1` 后 I#1 从待读消失；`daily.txt` 只含 I#2，并保留全部边界说明（未联网采集、未核验、源称发布（未核实）、实验分未校准）。
- `verify_demo.py` 输出 19 项 `[PASS]`，退出码 0。
