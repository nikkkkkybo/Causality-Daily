"""Offline regressions for ingesting a feed item into a document.

The bridge must be explicit: reading/ingesting a subscription item never creates
claims by itself, never marks anything verified, and extraction needs consent.
"""
from __future__ import annotations

import sqlite3

import pytest

from causal_ledger import ingest
from causal_ledger.db import connect
from causal_ledger.feeds import add_subscription, store_feed

BASE = "https://example.org/feed.xml"


def rss(*items):
    return ("<rss version='2.0'><channel>" + "".join(items) + "</channel></rss>").encode()


def item(title="虚构条目", guid="g1", url="https://example.org/a", summary="完全虚构摘要"):
    parts = [f"<title>{title}</title>"]
    if guid:
        parts.append(f"<guid>{guid}</guid>")
    if url:
        parts.append(f"<link>{url}</link>")
    parts.append(f"<description>{summary}</description>")
    return "<item>" + "".join(parts) + "</item>"


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def seeded(conn):
    feed_id = add_subscription(conn, "虚构订阅", BASE)
    store_feed(conn, feed_id, rss(item(), item(title="虚构无链接条目", guid="g2", url=None)))
    return conn


def test_get_item_missing_raises(conn):
    with pytest.raises(ValueError):
        ingest.get_item(conn, 999)


def test_store_item_document_creates_linked_document(seeded):
    doc_id, created = ingest.store_item_document(
        seeded, 1, title="条目标题", reporter_name="虚构订阅", reporter_type=None,
        raw="完全虚构摘要", url="https://example.org/a",
    )
    assert created is True
    row = seeded.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    assert row["title"] == "条目标题"
    assert row["raw"] == "完全虚构摘要"
    assert row["url"] == "https://example.org/a"
    assert ingest.linked_document_id(seeded, 1) == doc_id
    assert seeded.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
    assert seeded.execute("SELECT read_at FROM feed_items WHERE id=1").fetchone()[0] is None


def test_store_item_document_is_idempotent(seeded):
    first, created = ingest.store_item_document(
        seeded, 1, title="条目标题", reporter_name="虚构订阅", reporter_type=None,
        raw="摘要", url=None,
    )
    second, again = ingest.store_item_document(
        seeded, 1, title="另一个标题", reporter_name="虚构订阅", reporter_type=None,
        raw="别的摘要", url=None,
    )
    assert first == second and created and not again
    assert seeded.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert seeded.execute("SELECT title FROM documents").fetchone()[0] == "条目标题"


def test_prepare_content_uses_summary_without_network(seeded, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("network is forbidden")
    monkeypatch.setattr("causal_ledger.extract.fetch_url", forbidden)
    content, url = ingest.prepare_content(seeded, 1)
    assert content == "完全虚构摘要"
    assert url == "https://example.org/a"


def test_prepare_content_fetch_uses_safe_fetch(seeded, monkeypatch):
    calls = []

    def fake_fetch(url, timeout=30):
        calls.append(url)
        return "抓取到的完全虚构正文"
    monkeypatch.setattr("causal_ledger.extract.fetch_url", fake_fetch)
    content, url = ingest.prepare_content(seeded, 1, fetch=True)
    assert content == "抓取到的完全虚构正文"
    assert url == "https://example.org/a"
    assert calls == ["https://example.org/a"]


def test_prepare_content_requires_url_for_fetch(seeded):
    with pytest.raises(ValueError):
        ingest.prepare_content(seeded, 2, fetch=True)


def test_store_item_document_requires_content(seeded):
    with pytest.raises(ValueError):
        ingest.store_item_document(seeded, 1, title="x", reporter_name="虚构订阅",
                                   reporter_type=None, raw="   ", url=None)


def test_link_document_rejects_conflicting_link(seeded):
    first, _ = ingest.store_item_document(seeded, 1, title="一", reporter_name="虚构订阅",
                                          reporter_type=None, raw="摘要", url=None)
    second, _ = ingest.store_item_document(seeded, 2, title="二", reporter_name="虚构订阅",
                                           reporter_type=None, raw="摘要", url=None)
    with pytest.raises(ValueError):
        ingest.link_document(seeded, 1, second)
    with pytest.raises(ValueError):
        ingest.link_document(seeded, 999, first)
    with pytest.raises(ValueError):
        ingest.link_document(seeded, 1, 999)


# ---------------------------------------------------------------- CLI (offline)

def _cli(db, *args):
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items()
           if k not in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "CL_LLM_API_KEY")}
    return subprocess.run([sys.executable, "-m", "causal_ledger", "--db", str(db), *args],
                          cwd=root, env={**env, "PYTHONIOENCODING": "utf-8"},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_cli_ingest_offline_flow(tmp_path):
    db = tmp_path / "demo.sqlite3"
    rss_path = tmp_path / "feed.xml"
    rss_path.write_text(
        '<rss version="2.0"><channel><title>虚构</title>'
        '<item><guid>g1</guid><title>虚构电池条目</title>'
        '<link>https://example.org/a</link><description>完全虚构摘要</description></item>'
        '</channel></rss>', encoding="utf-8")
    assert _cli(db, "feed", "add", "--name", "虚构订阅", "--url", BASE).returncode == 0
    assert _cli(db, "feed", "import-file", "1", str(rss_path)).returncode == 0

    first = _cli(db, "ingest", "1")
    assert first.returncode == 0, first.stderr
    assert "未核验" in first.stdout
    assert "未抽取" in first.stdout or "尚未抽取" in first.stdout

    second = _cli(db, "ingest", "1")
    assert second.returncode == 0
    assert "已转换" in second.stdout

    item_out = _cli(db, "item", "1")
    assert "已转换文档" in item_out.stdout
    docs = _cli(db, "docs")
    assert docs.returncode == 0 and "虚构电池条目" in docs.stdout and "未核实" in docs.stdout
    doc = _cli(db, "doc", "1")
    assert doc.returncode == 0 and "虚构电池条目" in doc.stdout
    assert "关联主张（0）" in doc.stdout

    with sqlite3.connect(db) as raw:
        assert raw.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert raw.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0
        assert raw.execute("SELECT COUNT(*) FROM feed_ingests").fetchone()[0] == 1


def test_cli_ingest_extract_requires_allow_remote_before_db(tmp_path):
    db = tmp_path / "consent.sqlite3"
    result = _cli(db, "ingest", "1", "--extract")
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert not db.exists()


def test_cli_ingest_extract_uses_model_and_strips_p(tmp_path, monkeypatch):
    from causal_ledger import cli as module

    db = tmp_path / "extract.sqlite3"
    conn = connect(db)
    feed_id = add_subscription(conn, "虚构订阅", BASE)
    store_feed(conn, feed_id, rss(item()))
    conn.close()

    extraction = {
        "reporter": {"name": "虚构模型", "type": "unknown"},
        "claims": [{
            "text": "虚构主张一", "canonical_key": "虚构主体|发布|新品",
            "domain": "tech", "layer": "plan", "origin": None,
            "quote": "虚构原文", "hard_signal": None,
            "predictions": [{"text": "虚构预测", "due_date": "2999-01-01",
                             "check_hint": "查官网", "p": 0.99}],
        }],
    }
    monkeypatch.setattr(module, "extract_claims", lambda *a, **k: extraction)
    module.main(["--db", str(db), "ingest", "1", "--extract", "--allow-remote"])

    reopened = connect(db)
    try:
        assert reopened.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
        assert reopened.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert reopened.execute("SELECT p FROM predictions").fetchone()[0] is None
        assert reopened.execute("SELECT COUNT(*) FROM feed_ingests").fetchone()[0] == 1
        assert reopened.execute("SELECT read_at FROM feed_items").fetchone()[0] is None
    finally:
        reopened.close()


def test_ingest_extract_link_failure_rolls_back_everything(tmp_path, monkeypatch):
    from causal_ledger import cli as module

    db = tmp_path / "rollback.sqlite3"
    conn = connect(db)
    feed_id = add_subscription(conn, "虚构订阅", BASE)
    store_feed(conn, feed_id, rss(item()))
    conn.close()

    extraction = {
        "reporter": {"name": "虚构模型", "type": "unknown"},
        "claims": [{"text": "虚构主张", "canonical_key": "虚构|回滚|测试",
                    "domain": "tech", "layer": "manifestation", "origin": None,
                    "quote": "虚构原文", "hard_signal": None, "predictions": []}],
    }
    monkeypatch.setattr(module, "extract_claims", lambda *a, **k: extraction)

    def fail_link(*args, **kwargs):
        raise ValueError("模拟桥接失败")
    monkeypatch.setattr(module, "link_document", fail_link)
    with pytest.raises(SystemExit) as caught:
        module.main(["--db", str(db), "ingest", "1", "--extract", "--allow-remote"])
    assert "模拟桥接失败" in str(caught.value.code)

    reopened = connect(db)
    try:
        for table in ("documents", "claims", "chains", "credibility_log", "feed_ingests"):
            assert reopened.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert reopened.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    finally:
        reopened.close()


def test_feed_ingests_unique_constraint_is_database_backstop(seeded):
    ingest.store_item_document(seeded, 1, title="一", reporter_name="虚构订阅",
                               reporter_type=None, raw="摘要", url=None)
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO feed_ingests(feed_item_id, document_id, created_at) VALUES (1, 1, 'now')"
        )
