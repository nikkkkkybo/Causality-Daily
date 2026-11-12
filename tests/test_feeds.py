"""Offline RSS/Atom queue regressions; only transport is monkeypatched."""
from __future__ import annotations

import importlib
import importlib.util
import socket
import sqlite3
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

import pytest

from causal_ledger import extract
from causal_ledger.db import atomic, connect


BASE = "https://example.org/feeds/main.xml"
NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def test_module_exports_queue_api():
    assert importlib.util.find_spec("causal_ledger.feeds") is not None
    module = importlib.import_module("causal_ledger.feeds")
    for name in ("add_subscription", "set_subscription_enabled", "parse_feed", "store_feed",
                 "collect_feeds", "mark_read", "recommend"):
        assert callable(getattr(module, name, None)), name


@pytest.fixture
def feeds():
    return importlib.import_module("causal_ledger.feeds")


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Live network is forbidden")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)


def rss(*items):
    return ("<rss version='2.0'><channel>" + "".join(items) + "</channel></rss>").encode()


def item(title="Queue entry", guid="id-1", url="https://example.org/article", date=None,
         summary="Unverified summary"):
    fields = [("title", title), ("guid", guid), ("link", url), ("pubDate", date),
              ("description", summary)]
    return "<item>" + "".join(f"<{tag}>{escape(value)}</{tag}>" for tag, value in fields
                              if value is not None) + "</item>"


def seed(conn, feed_id, key, published=None, fetched=None, title=None, summary="", url=None):
    return conn.execute(
        "INSERT INTO feed_items(feed_id,item_key,title,url,summary,published_at,published_raw,fetched_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (feed_id, key, title or key, url, summary, published, published,
         fetched or NOW.isoformat(timespec="seconds")),
    ).lastrowid


@pytest.mark.parametrize("url", [
    "file:///private", "ftp://example.org/feed", "https://user:secret@example.org/feed",
    "https://example.org:80/", "http://example.org:8080/", "https://example.org:/",
    "https://example.org/\r\nInjected", "https://example.org\\@evil.example/feed",
    "https://127.0.0.1/feed", "http://10.0.0.1/", "https://169.254.169.254/",
    "http://[::1]/", "http://[fc00::1]/", "https://[2002:7f00:1::1]/",
    "https://localhost/feed", "http://service.local/feed", "http://127.1/",
    "http://2130706433/", "http://0x7f000001/", "http://127.0.0.1./",
    "http://192.0.2.1/", "http://example..org/feed", "http://[fe80::1%25eth0]/",
])
def test_add_rejects_unsafe_urls_without_network(feeds, conn, url):
    with pytest.raises(ValueError):
        feeds.add_subscription(conn, "Example", url)
    assert conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 0


def test_add_trims_and_enable_requires_existing_subscription(feeds, conn):
    feed_id = feeds.add_subscription(conn, "  Example  ", f"  {BASE}  ")
    row = dict(conn.execute("SELECT * FROM subscriptions WHERE id=?", (feed_id,)).fetchone())
    assert row["name"] == "Example"
    assert row["url"] == BASE
    assert row["enabled"] == 1
    assert datetime.fromisoformat(row["created_at"]).utcoffset() == timedelta(0)
    with pytest.raises(ValueError):
        feeds.add_subscription(conn, "Duplicate", BASE)
    with pytest.raises(ValueError):
        feeds.add_subscription(conn, " \n ", "https://other.example/feed")
    feeds.set_subscription_enabled(conn, feed_id, False)
    assert conn.execute("SELECT enabled FROM subscriptions").fetchone()[0] == 0
    feeds.set_subscription_enabled(conn, feed_id, True)
    with pytest.raises(ValueError):
        feeds.set_subscription_enabled(conn, feed_id + 1000, False)
    with pytest.raises(ValueError):
        feeds.set_subscription_enabled(conn, feed_id, 2)


@pytest.mark.parametrize("url", ["https://8.8.8.8/feed", "http://[2606:4700:4700::1111]/feed"])
def test_add_accepts_public_literals_without_dns(feeds, conn, url):
    assert feeds.add_subscription(conn, "Example", url) > 0


def test_rss_relative_bases_html_and_utc_date(feeds):
    raw = b'''<rss xml:base="https://example.org/news/" version="2.0"><channel xml:base="../blog/">
      <item xml:base="2026/"><guid>urn:example:first</guid>
        <title>&lt;b&gt;First &amp;amp; second&lt;/b&gt;</title>
        <link xml:base="../articles/">one#section</link>
        <description>&lt;p&gt;Visible&lt;/p&gt;&lt;script&gt;HIDDEN&lt;/script&gt;&lt;style&gt;CSS&lt;/style&gt;</description>
        <pubDate>Fri, 20 Nov 2026 15:00:00 +0800</pubDate>
      </item><item><title> </title></item></channel></rss>'''
    parsed = feeds.parse_feed(raw, BASE)
    assert len(parsed) == 1
    row = parsed[0]
    assert set(row) == {"item_key", "title", "url", "summary", "published_at", "published_raw"}
    assert row["title"] == "First & second"
    assert row["summary"] == "Visible"
    assert row["url"] == "https://example.org/blog/articles/one"
    assert row["published_at"] == "2026-11-20T07:00:00+00:00"
    assert row["published_raw"] == "Fri, 20 Nov 2026 15:00:00 +0800"


def test_atom_namespace_links_xhtml_and_published_precedence(feeds):
    raw = b'''<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://example.org/news/">
      <entry xml:base="articles/"><id>tag:example.org,2026:first</id>
        <title type="html">&lt;b&gt;Atom title&lt;/b&gt;</title>
        <link rel="self" href="entry.xml"/><link rel="enclosure" href="audio.mp3"/>
        <link rel="alternate" xml:base="../" href="first"/>
        <summary type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>Visible <b>text</b></p>
          <script>HIDDEN</script></div></summary>
        <published>2026-11-20T12:34:56.123+02:30</published><updated>2026-11-21T10:00:00Z</updated>
      </entry><entry><title>Fallback</title><updated>2026-11-19T08:00:00Z</updated>
        <content type="html">&lt;p&gt;Content fallback&lt;/p&gt;</content><link href="second"/>
      </entry></feed>'''
    first, second = feeds.parse_feed(raw, BASE)
    assert first["title"] == "Atom title"
    assert first["summary"] == "Visible text"
    assert first["url"] == "https://example.org/news/first"
    assert first["published_at"] == "2026-11-20T10:04:56+00:00"
    assert second["summary"] == "Content fallback"
    assert second["published_at"] == "2026-11-19T08:00:00+00:00"


@pytest.mark.parametrize("date", [None, "not a date", "2026-11-20T10:00:00", "2026-11-20",
                                   "Fri, 20 Nov 2026 10:00:00 -0000"])
def test_unknown_invalid_or_naive_dates_are_not_fabricated(feeds, date):
    row = feeds.parse_feed(rss(item(date=date)), BASE)[0]
    assert row["published_at"] is None
    assert row["published_raw"] == date


def test_atom_invalid_published_does_not_borrow_updated(feeds):
    raw = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Entry</title>
      <published>unknown</published><updated>2026-11-20T12:00:00Z</updated></entry></feed>'''
    row = feeds.parse_feed(raw, BASE)[0]
    assert row["published_raw"] == "unknown"
    assert row["published_at"] is None


def test_identity_prefers_guid_then_url_then_stable_content_hash(feeds):
    one = feeds.parse_feed(rss(item()), BASE)[0]
    changed = feeds.parse_feed(rss(item(title="Changed", url="https://example.org/new")), BASE)[0]
    assert one["item_key"] == changed["item_key"]
    url_key = feeds.parse_feed(rss(item(guid=None)), BASE)[0]["item_key"]
    assert url_key == feeds.parse_feed(rss(item(guid=None, title="Changed")), BASE)[0]["item_key"]
    hash_raw = rss(item(guid=None, url=None))
    fallback = feeds.parse_feed(hash_raw, BASE)[0]["item_key"]
    assert fallback == feeds.parse_feed(hash_raw, "https://other.example/feed")[0]["item_key"]
    assert fallback != feeds.parse_feed(rss(item(guid=None, url=None, summary="Changed")), BASE)[0]["item_key"]
    assert len({one["item_key"], url_key, fallback}) == 3


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://127.0.0.1/x", "http://localhost/x",
                                  "https://user:secret@example.org/x", "https://example.org:444/x",
                                  "https://example.org/\tpath", "http://127.1/x", "file:///etc/passwd"])
def test_malicious_entry_link_is_discarded_not_whole_feed(feeds, url):
    rows = feeds.parse_feed(rss(item(url=url), item(guid="second")), BASE)
    assert len(rows) == 2
    assert rows[0]["url"] is None
    assert rows[1]["url"] == "https://example.org/article"


def test_invalid_xml_base_cannot_make_relative_link_safe(feeds):
    raw = b'''<rss><channel><item xml:base="http://127.0.0.1/"><title>Bad base</title>
       <link>article</link></item><item><title>Good</title><link>/good</link></item></channel></rss>'''
    first, second = feeds.parse_feed(raw, BASE)
    assert first["url"] is None
    assert second["url"] == "https://example.org/good"


def test_empty_xml_base_inherits_parent_for_relative_links(feeds):
    raw = b'''<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://example.org/news/">
      <entry xml:base=""><title>Inherited</title><link xml:base="" href="article"/></entry></feed>'''
    assert feeds.parse_feed(raw, BASE)[0]["url"] == "https://example.org/news/article"


@pytest.mark.parametrize("space", ["\u00a0", "\u2028", "\u2029"])
def test_unicode_whitespace_is_not_safe_url_syntax(feeds, conn, space):
    url = "https://example.org/article" + space + "injected"
    with pytest.raises(ValueError):
        feeds.add_subscription(conn, "Example", url)
    assert feeds.parse_feed(rss(item(url=url)), BASE)[0]["url"] is None


def test_text_is_bounded_and_strips_scripts_and_terminal_controls(feeds):
    summary = "<script>HIDDEN</script><p>Visible</p>\x1b[31mred\x1b[0m\x1b]0;TITLE\x07" + "x" * 4500
    # XML prohibits literal ESC, so feed it through an HTML entity in CDATA.
    summary = summary.replace("\x1b", "&#27;").replace("\x07", "&#7;")
    raw = ("<rss><channel><item><title>" + "T" * 600 + "</title><description><![CDATA[" +
           summary + "]]></description></item></channel></rss>").encode()
    row = feeds.parse_feed(raw, BASE)[0]
    assert len(row["title"]) == 500
    assert len(row["summary"]) == 4000
    assert "HIDDEN" not in row["summary"]
    assert "TITLE" not in row["summary"]
    assert "[31m" not in row["summary"]
    assert not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in row["summary"])


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"])
@pytest.mark.parametrize("declaration", [
    '<!DOCTYPE rss [<!ENTITY attack "EXPANDED">]>',
    '<!DOCTYPE rss SYSTEM "https://example.org/external.dtd">',
    '<!ENTITY attack "EXPANDED">',
])
def test_rejects_dtd_entities_in_all_common_encodings(feeds, encoding, declaration):
    raw = (declaration + "<rss><channel/></rss>").encode(encoding)
    with pytest.raises(ValueError, match="DTD|entit|实体"):
        feeds.parse_feed(raw, BASE)


@pytest.mark.parametrize("raw", [b"", b"<rss>", b"<html><body>not a feed</body></html>", b"<rss/>"])
def test_rejects_malformed_or_unsupported_feeds(feeds, raw):
    with pytest.raises(ValueError):
        feeds.parse_feed(raw, BASE)


def test_byte_limit_and_item_limit(feeds):
    with pytest.raises(ValueError, match="limit|2 MiB|上限"):
        feeds.parse_feed(b" " * (2 * 1024 * 1024 + 1), BASE)
    valid = rss(item())
    assert len(feeds.parse_feed(valid + b" " * (2 * 1024 * 1024 - len(valid)), BASE)) == 1
    assert len(feeds.parse_feed(rss(*(item(guid=str(i)) for i in range(105))), BASE)) == 100
    assert feeds.parse_feed(rss(), BASE) == []


def test_store_preserves_first_seen_content_and_read_timestamp(feeds, conn):
    feed_id = feeds.add_subscription(conn, "Example", BASE)
    first = feeds.store_feed(conn, feed_id, rss(item(url="relative")))
    assert first == {"feed_id": feed_id, "added": 1, "duplicates": 0}
    original = dict(conn.execute("SELECT * FROM feed_items").fetchone())
    assert original["url"] == "https://example.org/feeds/relative"
    feeds.mark_read(conn, original["id"])
    read_at = conn.execute("SELECT read_at FROM feed_items").fetchone()[0]
    conn.execute("UPDATE feed_items SET fetched_at='2000-01-01T00:00:00+00:00'")
    conn.execute("UPDATE subscriptions SET last_error='previous failure'")
    duplicate = feeds.store_feed(conn, feed_id, rss(item(title="Changed", summary="Changed")))
    assert duplicate == {"feed_id": feed_id, "added": 0, "duplicates": 1}
    after = dict(conn.execute("SELECT * FROM feed_items").fetchone())
    assert after["title"] == original["title"]
    assert after["summary"] == original["summary"]
    assert after["fetched_at"] == "2000-01-01T00:00:00+00:00"
    assert after["read_at"] == read_at
    subscription = conn.execute("SELECT checked_at,last_error FROM subscriptions").fetchone()
    assert datetime.fromisoformat(subscription["checked_at"]).utcoffset() == timedelta(0)
    assert subscription["last_error"] is None
    feeds.mark_read(conn, original["id"])
    assert conn.execute("SELECT read_at FROM feed_items").fetchone()[0] == read_at
    with pytest.raises(ValueError):
        feeds.mark_read(conn, original["id"] + 1000)


def test_store_counts_repeated_keys_and_separate_feeds(feeds, conn):
    first = feeds.add_subscription(conn, "First", BASE)
    second = feeds.add_subscription(conn, "Second", "https://example.org/second.xml")
    raw = rss(item(), item(title="Later version"))
    assert feeds.store_feed(conn, first, raw)["duplicates"] == 1
    assert feeds.store_feed(conn, second, raw)["added"] == 1
    assert conn.execute("SELECT COUNT(*) FROM feed_items").fetchone()[0] == 2


def test_store_validates_before_writes_and_rolls_back_failed_insert(feeds, conn):
    feed_id = feeds.add_subscription(conn, "Example", BASE)
    with pytest.raises(ValueError):
        feeds.store_feed(conn, feed_id, b"<rss>malformed")
    with pytest.raises(ValueError):
        feeds.store_feed(conn, 9999, rss(item()))
    assert conn.execute("SELECT checked_at FROM subscriptions").fetchone()[0] is None
    conn.execute("CREATE TEMP TRIGGER fail_item BEFORE INSERT ON feed_items "
                 "WHEN NEW.title='Fail' BEGIN SELECT RAISE(ABORT, 'test constraint'); END")
    with pytest.raises(sqlite3.IntegrityError):
        feeds.store_feed(conn, feed_id, rss(item(), item(title="Fail", guid="second")))
    assert conn.execute("SELECT COUNT(*) FROM feed_items").fetchone()[0] == 0
    assert conn.execute("SELECT checked_at FROM subscriptions").fetchone()[0] is None


def test_store_and_subscription_changes_obey_outer_transaction(feeds, conn):
    with pytest.raises(RuntimeError):
        with atomic(conn):
            feed_id = feeds.add_subscription(conn, "Example", BASE)
            feeds.store_feed(conn, feed_id, rss(item()))
            raise RuntimeError("rollback outer")
    assert conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM feed_items").fetchone()[0] == 0


def test_collect_isolates_errors_uses_redirect_base_and_skips_disabled(feeds, conn, monkeypatch):
    bad = feeds.add_subscription(conn, "Bad", BASE)
    good = feeds.add_subscription(conn, "Good", "https://example.org/good.xml")
    disabled = feeds.add_subscription(conn, "Disabled", "https://example.org/disabled.xml")
    feeds.set_subscription_enabled(conn, disabled, False)
    called = []

    def fetch(url, timeout=30):
        called.append(url)
        if url == BASE:
            raise ValueError("<script>hidden()</script>\x1b[31mfailed\x1b[0m " + "x" * 2000)
        return rss(item(url="relative")), "https://example.org/redirected/feed.xml"

    monkeypatch.setattr(extract, "fetch_feed", fetch, raising=False)
    results = feeds.collect_feeds(conn)
    assert called == [BASE, "https://example.org/good.xml"]
    assert results[0]["feed_id"] == bad
    assert results[0]["added"] == results[0]["duplicates"] == 0
    error = results[0]["error"]
    assert 0 < len(error) <= 500
    assert "\x1b" not in error and "<script>" not in error and "hidden()" not in error
    assert conn.execute("SELECT last_error FROM subscriptions WHERE id=?", (bad,)).fetchone()[0] == error
    assert results[1] == {"feed_id": good, "added": 1, "duplicates": 0}
    assert conn.execute("SELECT url FROM feed_items").fetchone()[0] == "https://example.org/redirected/relative"
    assert feeds.collect_feeds(conn, [disabled]) == []
    assert feeds.collect_feeds(conn, []) == []


def test_collect_rejects_unknown_ids_and_over_limit_before_fetch(feeds, conn, monkeypatch):
    ids = [feeds.add_subscription(conn, str(i), f"https://example.org/{i}.xml") for i in range(21)]
    called = []

    def fetch(url, timeout=30):
        called.append(url)
        return rss(), url

    monkeypatch.setattr(extract, "fetch_feed", fetch, raising=False)
    with pytest.raises(ValueError, match="20"):
        feeds.collect_feeds(conn)
    with pytest.raises(ValueError):
        feeds.collect_feeds(conn, [ids[0], 99999])
    assert called == []
    selected = feeds.collect_feeds(conn, [ids[0], ids[0], ids[1]])
    assert len(selected) == 2
    assert len(called) == 2


def test_collect_malformed_feed_preserves_prior_entries_and_continues(feeds, conn, monkeypatch):
    bad = feeds.add_subscription(conn, "Bad", BASE)
    good = feeds.add_subscription(conn, "Good", "https://example.org/second.xml")
    feeds.store_feed(conn, bad, rss(item()))

    def fetch(url, timeout=30):
        return (b"<!DOCTYPE rss><rss/>" if url == BASE else rss(item())), url

    monkeypatch.setattr(extract, "fetch_feed", fetch, raising=False)
    results = feeds.collect_feeds(conn, [bad, good])
    assert results[0]["error"]
    assert results[1]["added"] == 1
    assert conn.execute("SELECT COUNT(*) FROM feed_items").fetchone()[0] == 2


def test_recommend_dates_unread_enabled_order_and_unknown_reason(feeds, conn):
    source = feeds.add_subscription(conn, "Example", BASE)
    off = feeds.add_subscription(conn, "Disabled", "https://example.org/off.xml")
    feeds.set_subscription_enabled(conn, off, False)
    stamp = lambda days: (NOW + timedelta(days=days)).isoformat(timespec="seconds")
    recent = seed(conn, source, "Recent", published=stamp(-1), fetched=stamp(-90))
    boundary = seed(conn, source, "Boundary", published=stamp(-7))
    unknown = seed(conn, source, "Unknown", published=None, fetched=stamp(0))
    seed(conn, source, "Old", published=stamp(-8))
    seed(conn, source, "Future", published=stamp(1))
    seed(conn, source, "Unknown old", fetched=stamp(-8))
    seed(conn, source, "Unknown future", fetched=stamp(1))
    read = seed(conn, source, "Read", published=stamp(0))
    feeds.mark_read(conn, read)
    seed(conn, off, "Disabled", published=stamp(0))
    results = feeds.recommend(conn, now=NOW, per_source=10)
    assert [row["id"] for row in results] == [recent, boundary, unknown]
    assert "发布时间未知" in results[-1]["reason"]
    assert all(row["feed_name"] == "Example" for row in results)
    assert all("未核验" in row["reason"] for row in results)
    item_fields = set(dict(conn.execute("SELECT * FROM feed_items LIMIT 1").fetchone()))
    assert all(set(row) == item_fields | {"feed_name", "reason"} for row in results)
    assert all("5000" in row["reason"] and "10" in row["reason"] for row in results)


def test_recommend_topics_casefold_or_source_cap_and_shared_url(feeds, conn):
    one = feeds.add_subscription(conn, "One", BASE)
    two = feeds.add_subscription(conn, "Two", "https://example.org/two.xml")
    date = (NOW - timedelta(hours=1)).isoformat()
    first = seed(conn, one, "first", published=date, title="Straße launch", url="https://example.org/shared")
    seed(conn, two, "duplicate", published=date, title="STRASSE copy", url="https://example.org/shared")
    second = seed(conn, two, "second", published=date, summary="Battery update")
    seed(conn, one, "source capped", published=date, title="BATTERY")
    seed(conn, two, "no topic", published=date)
    results = feeds.recommend(conn, topics=["STRASSE", "battery"], per_source=1, now=NOW)
    assert [row["id"] for row in results] == [first, second]
    assert "STRASSE" in results[0]["reason"]
    assert "battery" in results[1]["reason"]
    assert "独立证据" in results[0]["reason"]
    assert len(feeds.recommend(conn, limit=1, now=NOW)) == 1
    assert feeds.recommend(conn, topics=["no match"], now=NOW) == []


@pytest.mark.parametrize("kwargs", [
    {"days": 0}, {"days": 366}, {"days": 1.5}, {"days": True}, {"limit": 0},
    {"limit": 101}, {"limit": "2"}, {"per_source": 0}, {"per_source": 101},
    {"now": datetime(2026, 11, 20)}, {"now": "2026-11-20"},
])
def test_recommend_rejects_invalid_bounds_and_naive_now(feeds, conn, kwargs):
    with pytest.raises(ValueError):
        feeds.recommend(conn, **kwargs)


def test_recommend_respects_aware_offset_now_and_default_now(feeds, conn):
    source = feeds.add_subscription(conn, "Example", BASE)
    seed(conn, source, "current", fetched=datetime.now(timezone.utc).isoformat())
    assert len(feeds.recommend(conn)) == 1
    seed(conn, source, "dated", published="2026-11-20T10:00:00+00:00", fetched="2000-01-01T00:00:00+00:00")
    shifted_now = NOW.astimezone(timezone(timedelta(hours=8)))
    assert feeds.recommend(conn, now=NOW) == feeds.recommend(conn, now=shifted_now)


def test_recommend_caps_candidate_reads_and_reports_limitation(feeds, conn):
    source = feeds.add_subscription(conn, "Example", BASE)
    conn.executemany(
        "INSERT INTO feed_items(feed_id,item_key,title,summary,published_at,fetched_at) VALUES(?,?,?,?,?,?)",
        [(source, str(i), "target" if i == 5000 else "Other", "", NOW.isoformat(), NOW.isoformat())
         for i in range(5001)],
    )
    # Equal dates use ascending id: item 5001 is outside the bounded candidate window.
    assert feeds.recommend(conn, now=NOW, topics=["target"]) == []
    result = feeds.recommend(conn, now=NOW, limit=1)
    assert "5000" in result[0]["reason"]
    assert result[0]["item_key"] == "0"
