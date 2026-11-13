# Causality Daily

一个**本地优先的因果推送**原型：抓取 RSS/Atom、去重、生成带出处与不确定性的《今日事报》，支持个体因果基线、保险箱、7 天校准、盲评/消融，以及零月费的云端静态发布。

> 所有分数都是未校准的实验分，不是真伪概率；订阅/检索/摘要均未核验；不自动外发、不自动核实、不安装本机定时任务。

## 快速开始

```powershell
python --version          # 3.11+
python -m pytest tests -q # 本地测试（不访问网络、不调用模型）
python -m causal_ledger --help

# 看一下离线演示
python -m causal_ledger --db demo.sqlite3 import-json examples/sample_a.json
python -m causal_ledger --db demo.sqlite3 claims
```

主要能力：

- 出处链/来源先验/实验分/预测登记与人工结算；
- RSS/Atom 订阅、去重、待读推荐、一次 `daily` 生成；
- 文本简报、自包含 HTML、多页简报站；
- 个体因果基线（`profile`）、保险箱（Windows DPAPI）、7 天校准（`calibrate`）、盲评/消融（`eval`）；
- 本地单应用（`serve`）：阅读往期、提交末尾备注；
- 零月费云发布（`cloud` + GitHub Actions + Pages + 可选多通道推送：邮件 / ntfy / Bark / Telegram / ServerChan），发布新版《今日事报》链式页；
- DeepSeek 二次合成：加 `DEEPSEEK_API_KEY` 后由模型翻译/改写/成链；无 key 自动回退确定性版本。
- 个人承因原型：`bearing` 把日期、档案、信息、外部透镜（黄历/命理等 JSON）归一化为「今日承因」卡；新闻只是因源之一。
- 传统透镜：内置简化建除十二神；可选 `--birth YYYY-MM-DD` 给日主关系提示（启发式，不是完整命盘）。
- 样板：[samples/个人承因日报样板.html](samples/个人承因日报样板.html)；产出链路见 [docs/承因样板产出链路.md](docs/承因样板产出链路.md)、地基见 [docs/承因地基.md](docs/承因地基.md)。
- 周报：周一 07:30 自动汇总上一周；周报保留，日报归档；见 [docs/周报与保留策略.md](docs/周报与保留策略.md)；
- 新旧格式边界见 [docs/新旧对照.md](docs/新旧对照.md)。

## 零月费云发布

```powershell
python -m causal_ledger cloud --config config\feeds.json --reports reports --no-fetch --no-push
```

GitHub Actions 每天 07:30（北京时间）生成并发布静态日报；配置见 [docs/零月费部署.md](docs/零月费部署.md)。

## 边界

- 不做自动事实核查、自动结算、自动外发；
- 个体档案涉及保险箱字段时默认不读取；云端通线不读取本地档案；
- 公开仓库只放代码/配置/文档，不放数据库、档案、备注。
