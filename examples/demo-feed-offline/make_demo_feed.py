"""生成完全虚构的 RSS2 演练文件（离线：只写本地文件，不联网）。

用法（在工作目录 D:\\硬理搜索 下）：
    python examples/demo-feed-offline/make_demo_feed.py

生成的 demo-feed.xml 含 3 条虚构 item：
  1) 2 天前有 pubDate、标题含"电池"；
  2) 无 pubDate、标题含"电池"（用于演示"发布时间未知"边界）；
  3) 30 天前有 pubDate、非电池主题（用于演示 --days 7 与 --topic 过滤）。
所有标题/摘要均为虚构内容，仅用于演练，不是真实来源或结论。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).with_name("demo-feed.xml")
now = datetime.now(timezone.utc).replace(microsecond=0)


def rfc2822(moment: datetime) -> str:
    return format_datetime(moment)


def item(guid: str, title: str, link: str, summary: str, published: datetime | None) -> str:
    lines = [
        "    <item>",
        f"      <guid isPermaLink=\"false\">{escape(guid)}</guid>",
        f"      <title>{escape(title)}</title>",
        f"      <link>{escape(link)}</link>",
    ]
    if published is not None:
        lines.append(f"      <pubDate>{rfc2822(published)}</pubDate>")
    lines.append(f"      <description>{escape(summary)}</description>")
    lines.append("    </item>")
    return "\n".join(lines)


items = [
    item(
        "demo-battery-recent",
        "虚构演练：某实验室宣称新型钠电池循环寿命翻倍（完全虚构）",
        "https://example.org/demo/battery-recent",
        "完全虚构，仅用于演练：本条目没有真实出处，也不构成任何科学证据。"
        "标题与摘要中的电池、寿命等说法均为演示占位文本。",
        now - timedelta(days=2),
    ),
    item(
        "demo-battery-undated",
        "虚构演练：社区流传的电池回收补贴传闻（完全虚构）",
        "https://example.org/demo/battery-undated",
        "完全虚构，仅用于演练：本条故意不提供 pubDate，用来演示"
        "「发布时间未知，按首次采集时间筛选」的边界说明。请勿当作真实新闻。",
        None,
    ),
    item(
        "demo-grid-old",
        "虚构演练：区域电网储能项目招标观察（完全虚构）",
        "https://example.org/demo/grid-old",
        "完全虚构，仅用于演练：本条 pubDate 为 30 天前，主题也不含电池，"
        "用来演示 --days 7 与 --topic 电池 的过滤效果。",
        now - timedelta(days=30),
    ),
]

xml = "\n".join(
    [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0">',
        "  <channel>",
        "    <title>虚构演练订阅（完全虚构，仅用于离线功能演练）</title>",
        "    <link>https://example.org/demo-feed.xml</link>",
        '    <description>完全虚构的演示数据；不抓取、不联网，不代表任何真实来源、事实或结论。</description>',
        *items,
        "  </channel>",
        "</rss>",
        "",
    ]
)

OUT.write_text(xml, encoding="utf-8")
print(f"已生成虚构 RSS 文件：{OUT}（{len(items)} 条 item；最近发布约 2 天前，旧条目 30 天前，另有 1 条无 pubDate）")
