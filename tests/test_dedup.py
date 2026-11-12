"""Offline regressions for duplicate hints across sources (hints only, never merged)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from causal_ledger import dedup
from causal_ledger.db import connect
from causal_ledger.feeds import add_subscription, store_feed, recommend

NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


def rss(*items):
    return ("<rss version='2.0'><channel>" + "".join(items) + "</channel></rss>").encode()


def item(title, guid, url, published, summary="完全虚构摘要"):
    return (f"<item><title>{title}</title><guid>{guid}</guid><link>{url}</link>"
            f"<pubDate>{format_datetime(published)}</pubDate>"
            f"<description>{summary}</description></item>")


def test_normalize_url_strips_tracking_and_variants():
    assert dedup.normalize_url("https://example.org/a?utm_source=x&utm_medium=y#frag") == \
        "https://example.org/a"
    assert dedup.normalize_url("HTTPS://WWW.Example.org/a/") == "https://example.org/a"
    assert dedup.normalize_url("https://example.org/a?b=2&a=1") == "https://example.org/a?a=1&b=2"
    assert dedup.normalize_url("http://example.org:80/a") == "http://example.org/a"
    assert dedup.normalize_url(None) is None
    assert dedup.normalize_url("not a url") is None


def test_display_url_drops_tracking_but_keeps_host_and_path():
    assert dedup.display_url("https://www.example.org/a?utm_source=x&id=2") == \
        "https://www.example.org/a?id=2"
    assert dedup.display_url("https://example.org/a?utm_medium=y") == "https://example.org/a"
    assert dedup.display_url(None) is None


def test_title_fingerprint_ignores_case_punctuation_and_whitespace():
    assert dedup.title_fingerprint("Hello, World!") == dedup.title_fingerprint("hello world")
    assert dedup.title_fingerprint(" Python 3.16 alpha 2 发布 ") == \
        dedup.title_fingerprint("Python 3.16 Alpha 2 发布")
    assert dedup.title_fingerprint("   ") == ""


def test_titles_similar_near_duplicates_but_not_unrelated():
    assert dedup.titles_similar("Python 3.16 alpha 2 is now available",
                                "Python 3.16 alpha 2 is now available - blog")
    assert not dedup.titles_similar("Python 3.16 alpha 2 is now available",
                                    "Battery recycling subsidy rumor")


def test_recommend_hints_cross_source_duplicates_without_merging():
    conn = connect(":memory:")
    try:
        first = add_subscription(conn, "虚构源一", "https://example.org/one.xml")
        second = add_subscription(conn, "虚构源二", "https://example.org/two.xml")
        published = NOW - timedelta(hours=1)
        store_feed(conn, first, rss(item(
            "虚构电池工厂投产", "g1", "https://example.org/story?utm_source=one", published)))
        store_feed(conn, second, rss(item(
            "虚构电池工厂投产（转载）", "g2", "https://example.org/story", published)))
        rows = recommend(conn, days=7, limit=10, per_source=5, now=NOW)
        assert len(rows) == 2
        hinted = [row for row in rows if "疑似重复" in row["reason"]]
        assert len(hinted) == 1
        other = [row for row in rows if row["id"] != hinted[0]["id"]][0]
        assert f"I#{other['id']}" in hinted[0]["reason"]
        assert "仅提示" in hinted[0]["reason"]
        fields = set(dict(conn.execute("SELECT * FROM feed_items LIMIT 1").fetchone()))
        assert all(set(row) == fields | {"feed_name", "reason"} for row in rows)
    finally:
        conn.close()


def test_recommend_no_hint_for_unrelated_items():
    conn = connect(":memory:")
    try:
        feed = add_subscription(conn, "虚构源", "https://example.org/feed.xml")
        published = NOW - timedelta(hours=1)
        store_feed(conn, feed, rss(
            item("虚构电池工厂投产", "g1", "https://example.org/a", published),
            item("虚构地区选举结果公布", "g2", "https://example.org/b", published),
        ))
        rows = recommend(conn, days=7, limit=10, per_source=5, now=NOW)
        assert len(rows) == 2
        assert all("疑似重复" not in row["reason"] for row in rows)
    finally:
        conn.close()
