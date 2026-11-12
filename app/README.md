# 今日事报 · 本地单应用（Phase 1，Wi-Fi）

只做一件事：每天读一份**今日事报**，在末尾留下你的偏重备注。不做核验、不做推荐流、不做账簿 UI。

## 启动

```powershell
cd D:\硬理搜索
python -m causal_ledger --db app\app.sqlite3 serve --reports app\reports
```

浏览器打开 `http://127.0.0.1:8765/`。首页列出往期；点日期进入事报；事报末尾自动出现"本日备注"输入框。

## 手机（同一 Wi-Fi）

```powershell
python -m causal_ledger --db app\app.sqlite3 serve --reports app\reports --host 0.0.0.0 --port 8765 --token 你的口令
```

手机浏览器访问 `http://<这台电脑的局域网IP>:8765/?token=你的口令`。

- 默认只绑定 `127.0.0.1`；`--host 0.0.0.0` 才会对局域网开放，务必设置 `--token`。
- 这是 HTTP + 局域网，不是公网服务；电脑关机或离开同一网络就访问不了。
- 目前没有 HTTPS/PWA 安装；可先"添加到主屏幕"当书签用。

## 备注怎么用

- 末尾备注写入 SQLite `notes` 表（`day, text, created_at`），是**下一次取舍的潜意识**，不是命令，也不会出现在事报正文。
- 不写就不提交；留空点提交只会提示"留空即可"。
- 备注只存本地；后续生成若有模型参与，才会作为隐性偏好进入筛选。

## 个体因果基线（可选）

- 打开 `app/questionnaire.html`，本地离线填写，导出 `individual-baseline.json`；
- 导入：`python -m causal_ledger --db app\app.sqlite3 profile import individual-baseline.json`；
- 查看：`python -m causal_ledger --db app\app.sqlite3 profile show`；清除：`profile clear`；
- 有档案后，`daily --brief/--html/--html-dir` 会自动走个体线（强潜意识），没有档案则走通线；
- 出生/事件/心境等"保险箱"字段用 **Windows DPAPI 加密**（绑定本机用户），公开 JSON 不含原文；`profile unlock --reason` 按次解锁并写入审计，`profile audit` 可查；
- 换机器/换 Windows 用户后保险箱无法解密，这是有意设计；
- 7 天校准：`python -m causal_ledger --db app\app.sqlite3 calibrate --days 7` 先预览，确认后加 `--apply`；`daily` 会自动应用最近一次校准；
- 盲评：`eval blind --dir eval-runs/<日期>` 生成 A/B，读完用 `eval vote A|B --dir ...` 记录；`eval stats` 看胜负，`eval ablate` 看各变体重叠。

## 文件

- `app/reports/YYYY-MM-DD.html`：每天一份事报（当前为 `2026-11-12.html`）；
- `app/questionnaire.html`：**个体因果基线前置问卷**（v2，本地离线、无网络、可逐题设隐私档、导出 JSON）；
- `app/app.sqlite3`：应用数据库（notes 等表）；
- 服务逻辑：`causal_ledger/app.py`；命令：`causal_ledger serve`。

## 还没做的（Phase 2/3）

- 微信每日推送（需要服务端/第三方推送通道，手机 App 无法自己发微信消息）；
- 固定时间自动生成（需要计划任务或 serverless 定时任务）；
- 双端同步（PC/手机共读同一份生成结果 + 备注回传）；
- 模型接入与半黑盒模板审计（模型 key 不建议放手机端）。

这些等通道、时间和托管方式确定后再接。
