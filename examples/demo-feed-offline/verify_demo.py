"""只读验证 examples/demo-feed-offline 的演示产物（不修改数据库）。

用法（任意工作目录均可）：
    python examples/demo-feed-offline/verify_demo.py

检查项：feed_items=3、claims=0（订阅阅读队列不产生主张）、
PRAGMA integrity_check / foreign_key_check 正常、无 pubDate 条目的
published_at 为 NULL、旧条目超出 --days 7 窗口、I#1 已读、
daily.txt 含"未联网采集 / 未核验 / 发布时间未知 / 未核实"边界说明。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "demo.sqlite3"
DAILY = HERE / "daily.txt"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(label)


conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
check("PRAGMA integrity_check", integrity == "ok", integrity)
foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
check("PRAGMA foreign_key_check", not foreign_keys, f"{len(foreign_keys)} 个违规")

feed_items = conn.execute("SELECT COUNT(*) FROM feed_items").fetchone()[0]
check("feed_items 计数 = 3", feed_items == 3, f"实际 {feed_items}")
claims = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
check("claims 计数 = 0（订阅不生成主张）", claims == 0, f"实际 {claims}")
documents = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
check("documents 计数 = 0", documents == 0, f"实际 {documents}")
subscriptions = conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
check("subscriptions 计数 = 1", subscriptions == 1, f"实际 {subscriptions}")
distinct_keys = conn.execute("SELECT COUNT(DISTINCT item_key) FROM feed_items").fetchone()[0]
check("去重后 item_key 唯一 = 3", distinct_keys == 3, f"实际 {distinct_keys}")
dupes = conn.execute(
    "SELECT feed_id, item_key, COUNT(*) c FROM feed_items GROUP BY feed_id, item_key HAVING c > 1"
).fetchall()
check("重复导入未产生重复行", not dupes, f"{len(dupes)} 组重复")

rows = conn.execute("SELECT id, title, published_at, read_at FROM feed_items ORDER BY id").fetchall()
by_id = {row["id"]: row for row in rows}
check("I#1 已标记已读", by_id[1]["read_at"] is not None, str(by_id[1]["read_at"]))
check("I#2 无 pubDate → published_at 为 NULL", by_id[2]["published_at"] is None, str(by_id[2]["published_at"]))
old_published = datetime.fromisoformat(by_id[3]["published_at"])
check("I#3 发布于 7 天窗口之外", old_published < datetime.now(timezone.utc) - timedelta(days=7),
      by_id[3]["published_at"])
check("I#3 未读（仅因天数/主题被过滤）", by_id[3]["read_at"] is None, str(by_id[3]["read_at"]))

text = DAILY.read_text(encoding="utf-8")
boundaries = {
    "未联网采集": "未联网采集" in text,
    "未核验": "未核验" in text,
    "发布时间未知": "发布时间未知" in text,
    "未核实 / 未校准": ("未核实" in text and "未校准" in text),
}
for label, ok in boundaries.items():
    check(f"daily.txt 含边界说明「{label}」", ok)
check("daily.txt 不含已读条目 I#1", by_id[1]["title"] not in text)
check("daily.txt 不含旧条目 I#3", by_id[3]["title"] not in text)
check("daily.txt 含未读未定日期条目 I#2", by_id[2]["title"] in text)

conn.close()
print()
if failures:
    print(f"验证失败 {len(failures)} 项：" + "；".join(failures))
    raise SystemExit(1)
print("全部验证通过：演示库完好、订阅阅读数据未污染主张表、日报边界说明齐全。")
