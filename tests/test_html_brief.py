"""Offline regressions for the visual (HTML) brief."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from causal_ledger.db import connect
from causal_ledger.feeds import add_subscription, store_feed
from causal_ledger.html_report import render_brief_html

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)


def rss(*items):
    return ("<rss version='2.0'><channel>" + "".join(items) + "</channel></rss>").encode()


def item(title, guid, url, published):
    return (f"<item><title>{title}</title><guid>{guid}</guid><link>{url}</link>"
            f"<pubDate>{format_datetime(published)}</pubDate>"
            f"<description>完全虚构摘要</description></item>")


def seed(conn):
    first = add_subscription(conn, "虚构源一", "https://example.org/one.xml")
    second = add_subscription(conn, "虚构源二", "https://example.org/two.xml")
    store_feed(conn, first, rss(item("虚构电池工厂投产", "g1",
                                     "https://example.org/a", NOW - timedelta(hours=2))))
    store_feed(conn, second, rss(item("虚构地区选举结果", "g2",
                                      "https://example.org/b", NOW - timedelta(hours=3))))


def cli(db, *args):
    env = {key: value for key, value in os.environ.items()
           if key not in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "CL_LLM_API_KEY")}
    return subprocess.run([sys.executable, "-m", "causal_ledger", "--db", str(db), *args],
                          cwd=ROOT, env={**env, "PYTHONIOENCODING": "utf-8"},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_html_brief_contains_dashboard_sections_and_escapes():
    conn = connect(":memory:")
    try:
        seed(conn)
        page = render_brief_html(conn, days=2, collection=[{"feed_id": 1, "added": 2, "duplicates": 0}])
        assert page.startswith("<!DOCTYPE html>")
        assert "因果簿 · 今日简报" in page
        assert "待你处理" in page
        assert "值得注意" in page and "2 条" in page
        assert "近 7 天采集" in page and "来源条目分布" in page
        assert "详细内容" in page
        assert "详情 ↓" in page and "原文 ↗" in page
        assert 'id="d-1"' in page and 'href="#d-1"' in page
        assert "出处与时间" in page and "同题源数" in page
        assert "完全虚构摘要" in page
        assert "未核验" in page and "不自动外发" in page
        assert 'target="_blank" rel="noopener noreferrer"' in page
        assert "虚构电池工厂投产" in page
        assert "https://example.org/a" in page
    finally:
        conn.close()


def test_html_brief_excerpt_is_short_and_detail_keeps_full_text():
    conn = connect(":memory:")
    try:
        feed = add_subscription(conn, "虚构源", "https://example.org/feed.xml")
        long_summary = "完全虚构的摘要。" * 40
        store_feed(conn, feed, rss(
            f"<item><title>虚构长摘要条目</title><guid>g1</guid>"
            f"<link>https://example.org/a</link>"
            f"<pubDate>{format_datetime(NOW - timedelta(hours=1))}</pubDate>"
            f"<description>{long_summary}</description></item>",
        ))
        page = render_brief_html(conn, days=2)
        card = page.split('class="cards"', 1)[1].split('</article>', 1)[0]
        detail = page.split('class="detail"', 1)[1]
        assert "…" in card
        assert long_summary[:60] in detail
        assert len(card) < len(detail)
    finally:
        conn.close()


def test_html_excerpt_strips_media_boilerplate():
    conn = connect(":memory:")
    try:
        feed = add_subscription(conn, "虚构源", "https://example.org/feed.xml")
        store_feed(conn, feed, rss(
            item("虚构标题", "g1", "https://example.org/a", NOW - timedelta(hours=1)),
        ))
        conn.execute("UPDATE feed_items SET summary=? WHERE id=1", (
            "IT之家 11 月 11 日消息，完全虚构的正文内容。"
            "欢迎关注 IT之家官方微信公众号，更多精彩内容。",))
        conn.commit()
        page = render_brief_html(conn, days=2)
        card = page.split('class="cards"', 1)[1].split("</article>", 1)[0]
        assert "11 月 11 日消息" not in card
        assert "完全虚构的正文内容" in card
        assert "欢迎关注" not in card
        assert "同题1源" in page
    finally:
        conn.close()


def test_html_brief_escapes_dangerous_titles_and_links():
    conn = connect(":memory:")
    try:
        feed = add_subscription(conn, "虚构源", "https://example.org/feed.xml")
        store_feed(conn, feed, rss(
            item("安全标题", "g1", "https://example.org/a", NOW - timedelta(hours=1)),
        ))
        # Bypass the RSS cleaner to inject hostile values straight into the store.
        conn.execute("UPDATE feed_items SET title=?, url=? WHERE id=1",
                     ('<script>alert("x")</script>危险标题', "javascript:alert(1)"))
        conn.commit()
        page = render_brief_html(conn, days=2)
        assert "<script>alert" not in page
        assert "&lt;script&gt;" in page
        assert 'href="javascript:' not in page
        assert "javascript:" not in page
    finally:
        conn.close()


def test_html_brief_empty_database():
    conn = connect(":memory:")
    try:
        page = render_brief_html(conn, days=2)
        assert "暂无符合条件的未读条目" in page
        assert "待结算" in page
    finally:
        conn.close()


def test_cli_daily_html_writes_file_and_records_format(tmp_path):
    db = tmp_path / "html.sqlite3"
    conn = connect(db)
    seed(conn)
    conn.close()
    output = tmp_path / "brief.html"
    result = cli(db, "daily", "--html", "--no-fetch", "--days", "2", "--output", str(output))
    assert result.returncode == 0, result.stderr
    page = output.read_text(encoding="utf-8")
    assert page.startswith("<!DOCTYPE html>")
    with sqlite3.connect(db) as raw:
        summary = json.loads(raw.execute("SELECT summary_json FROM digests").fetchone()[0])
        path = raw.execute("SELECT path FROM digests").fetchone()[0]
    assert summary["format"] == "html"
    assert path == str(output)


def test_cli_html_requires_output(tmp_path):
    db = tmp_path / "html-no-output.sqlite3"
    result = cli(db, "daily", "--html", "--no-fetch")
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "output" in (result.stderr + result.stdout)
