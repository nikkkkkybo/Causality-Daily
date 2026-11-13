"""Offline regressions for the multi-page brief site (index + detail pages)."""
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
from causal_ledger.html_site import render_brief_site

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)


def rss(*items):
    return ("<rss version='2.0'><channel>" + "".join(items) + "</channel></rss>").encode()


def item(title, guid, url, published, summary="完全虚构摘要"):
    return (f"<item><title>{title}</title><guid>{guid}</guid><link>{url}</link>"
            f"<pubDate>{format_datetime(published)}</pubDate>"
            f"<description>{summary}</description></item>")


def seed(conn):
    first = add_subscription(conn, "虚构源一", "https://example.org/one.xml")
    second = add_subscription(conn, "虚构源二", "https://example.org/two.xml")
    store_feed(conn, first, rss(item("虚构电池工厂投产", "g1", "https://example.org/a",
                                     NOW - timedelta(hours=2))))
    store_feed(conn, second, rss(item("虚构地区选举结果", "g2", "https://example.org/b",
                                      NOW - timedelta(hours=3))))


def cli(db, *args):
    env = {key: value for key, value in os.environ.items()
           if key not in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "CL_LLM_API_KEY")}
    return subprocess.run([sys.executable, "-m", "causal_ledger", "--db", str(db), *args],
                          cwd=ROOT, env={**env, "PYTHONIOENCODING": "utf-8"},
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_site_index_links_to_detail_pages():
    conn = connect(":memory:")
    try:
        seed(conn)
        site = render_brief_site(conn, days=2)
        index = site["index"]
        assert index.startswith("<!DOCTYPE html>")
        assert "因果簿 · 今日简报" in index
        assert 'href="detail/item-1.html"' in index
        assert 'href="detail/item-2.html"' in index
        assert "详情 →" in index
        assert "原文 ↗" in index
        details = site["details"]
        assert set(details) == {"detail/item-1.html", "detail/item-2.html"}
        first = details["detail/item-1.html"]
        assert first.startswith("<!DOCTYPE html>")
        assert "虚构电池工厂投产" in first
        assert "返回今日简报" in first and "../index.html" in first
        assert "出处与时间" in first and "订阅摘要原文" in first
        assert "下一条 →" in first
        second = details["detail/item-2.html"]
        assert "上一条" in second
    finally:
        conn.close()


def test_site_fetch_details_uses_fetched_text_and_falls_back():
    conn = connect(":memory:")
    try:
        seed(conn)

        def fake_fetch(url):
            if url.endswith("/b"):
                raise ValueError("模拟抓取失败")
            return "完全虚构的原文正文。" * 30

        site = render_brief_site(conn, days=2, fetch_details=True, fetch_text=fake_fetch)
        assert site["stats"]["fetched_ok"] == 1
        assert site["stats"]["fetched_fail"] == 1
        first = site["details"]["detail/item-1.html"]
        assert "联网抓取" in first and "完全虚构的原文正文" in first
        second = site["details"]["detail/item-2.html"]
        assert "抓取失败" in second and "完全虚构摘要" in second
        assert "完全虚构的原文正文" in site["index"]
    finally:
        conn.close()


def test_site_escapes_hostile_titles_and_links():
    conn = connect(":memory:")
    try:
        feed = add_subscription(conn, "虚构源", "https://example.org/feed.xml")
        store_feed(conn, feed, rss(item("安全标题", "g1", "https://example.org/a",
                                        NOW - timedelta(hours=1))))
        conn.execute("UPDATE feed_items SET title=?, url=? WHERE id=1",
                     ('<script>alert("x")</script>危险标题', "javascript:alert(1)"))
        conn.commit()
        site = render_brief_site(conn, days=2)
        index, detail = site["index"], next(iter(site["details"].values()))
        for page in (index, detail):
            assert "<script>alert" not in page
            assert "&lt;script&gt;" in page
            assert "javascript:" not in page
        assert 'href="detail/item-1.html"' in index
    finally:
        conn.close()


def test_cli_html_dir_writes_site_and_refuses_overwrite(tmp_path):
    db = tmp_path / "site.sqlite3"
    conn = connect(db)
    seed(conn)
    conn.close()
    target = tmp_path / "site"
    result = cli(db, "daily", "--html-dir", str(target), "--no-fetch", "--days", "2")
    assert result.returncode == 0, result.stderr
    index = target / "index.html"
    assert index.is_file()
    assert (target / "detail" / "item-1.html").is_file()
    assert (target / "detail" / "item-2.html").is_file()
    text = index.read_text(encoding="utf-8")
    assert 'href="detail/item-1.html"' in text
    with sqlite3.connect(db) as raw:
        summary = json.loads(raw.execute("SELECT summary_json FROM digests").fetchone()[0])
    assert summary["format"] == "html-site"

    again = cli(db, "daily", "--html-dir", str(target), "--no-fetch", "--days", "2")
    assert again.returncode != 0
    assert "Traceback" not in again.stderr
    assert index.read_text(encoding="utf-8") == text


def test_cli_html_dir_flag_conflicts(tmp_path):
    db = tmp_path / "conflict.sqlite3"
    one = cli(db, "daily", "--html-dir", str(tmp_path / "s"), "--html",
              "--output", str(tmp_path / "x.html"))
    assert one.returncode != 0 and "Traceback" not in one.stderr
    two = cli(db, "daily", "--fetch-details", "--no-fetch")
    assert two.returncode != 0 and "Traceback" not in two.stderr
