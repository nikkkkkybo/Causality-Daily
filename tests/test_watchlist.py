"""Offline regressions for the topic watchlist and its inbox/brief integration."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest

from causal_ledger import watchlist
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
    store_feed(conn, first, rss(item("虚构电池工厂投产", "g1", "https://example.org/a",
                                     NOW - timedelta(hours=2))))
    store_feed(conn, second, rss(item("虚构地区选举结果", "g2", "https://example.org/b",
                                      NOW - timedelta(hours=3))))


def cli(db, *args):
    env = {key: value for key, value in os.environ.items()
           if key not in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "CL_LLM_API_KEY")}
    return subprocess.run([sys.executable, "-m", "causal_ledger", "--db", str(db), *args],
                          cwd=ROOT, env={**env, "PYTHONIOENCODING": "utf-8"},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_watchlist_crud_and_validation():
    conn = connect(":memory:")
    try:
        assert watchlist.add_topic(conn, "  电池  回收 ") > 0
        assert watchlist.enabled_topics(conn) == ["电池 回收"]
        with pytest.raises(ValueError):
            watchlist.add_topic(conn, "电池 回收")
        with pytest.raises(ValueError):
            watchlist.add_topic(conn, "   ")
        with pytest.raises(ValueError):
            watchlist.add_topic(conn, "x" * 201)
        assert len(watchlist.list_topics(conn)) == 1
        watchlist.set_topic_enabled(conn, "电池 回收", False)
        assert watchlist.enabled_topics(conn) == []
        with pytest.raises(ValueError):
            watchlist.remove_topic(conn, "不存在")
        watchlist.remove_topic(conn, "电池 回收")
        assert watchlist.list_topics(conn) == []
    finally:
        conn.close()


def test_cli_watch_add_list_remove(tmp_path):
    db = tmp_path / "watch.sqlite3"
    added = cli(db, "watch", "add", "电池")
    assert added.returncode == 0, added.stderr
    assert "电池" in added.stdout
    listed = cli(db, "watch", "list")
    assert listed.returncode == 0 and "电池" in listed.stdout and "启用" in listed.stdout
    disabled = cli(db, "watch", "disable", "电池")
    assert disabled.returncode == 0
    assert "停用" in cli(db, "watch", "list").stdout
    removed = cli(db, "watch", "remove", "电池")
    assert removed.returncode == 0
    assert "暂无关注主题" in cli(db, "watch", "list").stdout
    missing = cli(db, "watch", "remove", "电池")
    assert missing.returncode != 0 and "Traceback" not in missing.stderr


def test_daily_brief_uses_watchlist_unless_topic_overrides(tmp_path):
    db = tmp_path / "watch-brief.sqlite3"
    conn = connect(db)
    seed(conn)
    conn.close()
    assert cli(db, "watch", "add", "电池").returncode == 0
    brief = cli(db, "daily", "--brief", "--no-fetch", "--days", "2")
    assert brief.returncode == 0, brief.stderr
    assert "虚构电池工厂投产" in brief.stdout and "虚构地区选举结果" not in brief.stdout
    assert "关注主题：电池" in brief.stdout

    overridden = cli(db, "daily", "--brief", "--no-fetch", "--days", "2", "--topic", "选举")
    assert overridden.returncode == 0
    assert "虚构地区选举结果" in overridden.stdout
    assert "虚构电池工厂投产" not in overridden.stdout
    assert "关注主题：选举" in overridden.stdout

    assert cli(db, "watch", "remove", "电池").returncode == 0
    all_items = cli(db, "daily", "--brief", "--no-fetch", "--days", "2")
    assert "关注主题：未设置" in all_items.stdout


def test_html_brief_shows_watch_topics():
    conn = connect(":memory:")
    try:
        seed(conn)
        page = render_brief_html(conn, days=2, topics=["电池"])
        assert "关注主题：电池" in page
        assert "虚构电池工厂投产" in page
        assert "虚构地区选举结果" not in page
    finally:
        conn.close()


def test_watch_schema_is_additive(tmp_path):
    db = tmp_path / "existing.sqlite3"
    with sqlite3.connect(db) as raw:
        raw.execute("CREATE TABLE documents(id INTEGER PRIMARY KEY, title TEXT)")
        raw.execute("INSERT INTO documents VALUES (1, 'existing')")
    conn = connect(db)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "watchlists" in names
        assert conn.execute("SELECT title FROM documents WHERE id=1").fetchone()[0] == "existing"
    finally:
        conn.close()
