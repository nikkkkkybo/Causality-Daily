"""Offline regressions for the compact daily brief (push v2)."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from causal_ledger.db import connect
from causal_ledger.feeds import add_subscription, store_feed
from causal_ledger.report import render_brief

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


def rss(*items):
    return ("<rss version='2.0'><channel>" + "".join(items) + "</channel></rss>").encode()


def item(title, guid, url, published):
    pub = f"<pubDate>{format_datetime(published)}</pubDate>" if published else ""
    return (f"<item><title>{title}</title><guid>{guid}</guid><link>{url}</link>"
            f"{pub}<description>完全虚构摘要</description></item>")


def seed(conn):
    first = add_subscription(conn, "虚构源一", "https://example.org/one.xml")
    second = add_subscription(conn, "虚构源二", "https://example.org/two.xml")
    published = NOW - timedelta(hours=2)
    store_feed(conn, first, rss(
        item("虚构电池工厂投产", "g1", "https://example.org/a?utm_source=one", published),
        item("虚构光伏装机增长", "g2", "https://example.org/b", published - timedelta(minutes=1)),
    ))
    store_feed(conn, second, rss(
        item("虚构电池工厂投产（转载）", "g3", "https://example.org/a", published),
        item("虚构地区选举结果", "g4", "https://example.org/c", published - timedelta(minutes=2)),
    ))


def cli(db, *args):
    env = {key: value for key, value in os.environ.items()
           if key not in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "CL_LLM_API_KEY")}
    return subprocess.run([sys.executable, "-m", "causal_ledger", "--db", str(db), *args],
                          cwd=ROOT, env={**env, "PYTHONIOENCODING": "utf-8"},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_render_brief_is_compact_with_boundaries_and_links():
    conn = connect(":memory:")
    try:
        seed(conn)
        text = render_brief(conn, days=2, limit=10, per_source=5,
                            collection=[{"feed_id": 1, "added": 2, "duplicates": 0},
                                        {"feed_id": 2, "added": 2, "duplicates": 0}])
        assert "因果簿简报" in text
        assert "未核验" in text and "不自动外发" in text
        assert "未经校准" in text
        assert "值得注意" in text and "台账状态" in text
        assert "https://example.org" in text
        assert "虚构电池工厂投产" in text
        assert len([line for line in text.splitlines() if line.startswith("  ")]) <= 12
    finally:
        conn.close()


def test_brief_prefers_cross_source_signal_and_feed_diversity():
    conn = connect(":memory:")
    try:
        seed(conn)
        text = render_brief(conn, days=2, limit=10, per_source=5, collection=[])
        # The cross-source duplicate should be surfaced before ordinary items.
        first_item_block = text.split("值得注意", 1)[1].split("台账状态", 1)[0]
        assert first_item_block.index("虚构电池工厂投产") < first_item_block.index("虚构地区选举结果")
        # Both feeds should appear when there is room.
        assert "虚构源一" in first_item_block and "虚构源二" in first_item_block
    finally:
        conn.close()


def test_render_brief_empty_database():
    conn = connect(":memory:")
    try:
        text = render_brief(conn, days=2, collection=[])
        assert "因果簿简报" in text
        assert "暂无" in text
    finally:
        conn.close()


def test_brief_shows_unlinked_action_and_ledger_state():
    conn = connect(":memory:")
    try:
        seed(conn)
        text = render_brief(conn, days=2, collection=[])
        assert "未接账" in text
        assert "接账：ingest" in text
        assert "已接账条目：0" in text
    finally:
        conn.close()


def test_brief_shows_ingested_claims_and_due_settlement():
    from causal_ledger import ingest
    from causal_ledger.provenance import add_prediction, import_extraction

    conn = connect(":memory:")
    try:
        seed(conn)
        summary = import_extraction(conn, {
            "reporter": {"name": "虚构源一", "type": "unknown"},
            "claims": [{"text": "虚构电池工厂投产", "canonical_key": "虚构电池工厂|投产|一期",
                        "domain": "tech", "layer": "manifestation", "origin": None,
                        "quote": "完全虚构摘要"}],
        }, title="接账文档")
        ingest.link_document(conn, 1, summary["document_id"])
        prediction_id = add_prediction(
            conn, summary["claims"][0]["claim_id"], "虚构官网发布投产公告",
            (date.today() + timedelta(days=1)).isoformat(), "查官网", p=0.6)
        conn.execute("UPDATE predictions SET due_date=? WHERE id=?",
                     (date.today().isoformat(), prediction_id))
        conn.commit()
        text = render_brief(conn, days=2, collection=[])
        assert "已接账 D#" in text
        assert "待结算：1" in text
        assert "结算到期" in text and f"P#{prediction_id}" in text
        assert "settle P#" in text
    finally:
        conn.close()


def test_cli_daily_brief_writes_file_and_records_digest(tmp_path):
    db = tmp_path / "brief.sqlite3"
    conn = connect(db)
    seed(conn)
    conn.close()
    output = tmp_path / "brief.txt"
    result = cli(db, "daily", "--brief", "--no-fetch", "--days", "2", "--output", str(output))
    assert result.returncode == 0, result.stderr
    text = output.read_text(encoding="utf-8")
    assert "因果簿简报" in text
    assert "推荐依据" not in text
    assert "未接账" in text
    with sqlite3.connect(db) as raw:
        assert raw.execute("SELECT COUNT(*) FROM digests").fetchone()[0] == 1
