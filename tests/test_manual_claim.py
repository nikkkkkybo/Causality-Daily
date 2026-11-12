"""Offline regressions for human-curated claim ingestion (no LLM required)."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://example.org/feed.xml"


def cli(db, *args):
    env = {key: value for key, value in os.environ.items()
           if key not in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "CL_LLM_API_KEY")}
    return subprocess.run([sys.executable, "-m", "causal_ledger", "--db", str(db), *args],
                          cwd=ROOT, env={**env, "PYTHONIOENCODING": "utf-8"},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def rss_file(tmp_path):
    path = tmp_path / "feed.xml"
    path.write_text(
        '<rss version="2.0"><channel><title>虚构</title>'
        '<item><guid>g1</guid><title>虚构电池条目</title>'
        '<link>https://example.org/a</link><description>完全虚构摘要</description></item>'
        '</channel></rss>', encoding="utf-8")
    return path


def setup(tmp_path):
    db = tmp_path / "manual.sqlite3"
    assert cli(db, "feed", "add", "--name", "虚构订阅", "--url", BASE).returncode == 0
    assert cli(db, "feed", "import-file", "1", str(rss_file(tmp_path))).returncode == 0
    return db


def manual_json(tmp_path):
    path = tmp_path / "manual.json"
    path.write_text(json.dumps({
        "reporter": {"name": "虚构订阅", "type": "unknown"},
        "claims": [
            {"text": "虚构电池工厂投产", "canonical_key": "虚构电池工厂|投产|一期",
             "domain": "tech", "layer": "manifestation", "origin": None,
             "quote": "完全虚构摘要", "hard_signal": None, "predictions": []},
            {"text": "虚构电池工厂产能翻倍", "canonical_key": "虚构电池工厂|产能|翻倍",
             "domain": "tech", "layer": "plan", "origin": None,
             "quote": "完全虚构摘要", "hard_signal": None, "predictions": []},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return path


def rows(db, sql, params=()):
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


def test_ingest_claim_creates_claim_chain_and_link(tmp_path):
    db = setup(tmp_path)
    result = cli(db, "ingest", "1", "--claim", "虚构电池工厂投产",
                 "--domain", "tech", "--layer", "manifestation",
                 "--quote", "完全虚构摘要")
    assert result.returncode == 0, result.stderr
    assert "人工" in result.stdout or "接账" in result.stdout
    assert rows(db, "SELECT COUNT(*) FROM documents")[0][0] == 1
    assert rows(db, "SELECT COUNT(*) FROM claims")[0][0] == 1
    assert rows(db, "SELECT COUNT(*) FROM chains")[0][0] == 1
    assert rows(db, "SELECT COUNT(*) FROM feed_ingests")[0][0] == 1
    claim = rows(db, "SELECT text, canonical_key, domain, layer, credibility FROM claims")[0]
    assert claim[0] == "虚构电池工厂投产"
    assert claim[1] == "虚构电池工厂投产"
    assert claim[2] == "tech" and claim[3] == "manifestation"
    assert claim[4] > 0
    chain = rows(db, "SELECT reporter_source_id, origin_source_id, quote FROM chains")[0]
    assert chain[0] == chain[1]
    assert chain[2] == "完全虚构摘要"
    assert rows(db, "SELECT read_at FROM feed_items")[0][0] is None
    assert "虚构电池工厂投产" in cli(db, "claims").stdout
    assert "虚构电池工厂投产" in cli(db, "search", "电池").stdout


def test_ingest_claim_records_manual_origin(tmp_path):
    db = setup(tmp_path)
    result = cli(db, "ingest", "1", "--claim", "虚构电池工厂投产",
                 "--origin", "虚构通讯社", "--origin-type", "institutional_media")
    assert result.returncode == 0, result.stderr
    reporter, origin = rows(
        db, "SELECT r.name, o.name FROM chains ch"
        " JOIN sources r ON r.id=ch.reporter_source_id"
        " JOIN sources o ON o.id=ch.origin_source_id")[0]
    assert reporter == "虚构订阅" and origin == "虚构通讯社"
    assert rows(db, "SELECT COUNT(*) FROM sources")[0][0] == 2


def test_ingest_claim_and_extract_are_mutually_exclusive(tmp_path):
    db = tmp_path / "mutex.sqlite3"
    result = cli(db, "ingest", "1", "--claim", "虚构主张", "--extract", "--allow-remote")
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert not db.exists()


def test_import_json_feed_item_links_document_and_is_idempotent(tmp_path):
    db = setup(tmp_path)
    source = manual_json(tmp_path)
    first = cli(db, "import-json", str(source), "--feed-item", "1")
    assert first.returncode == 0, first.stderr
    assert rows(db, "SELECT COUNT(*) FROM claims")[0][0] == 2
    assert rows(db, "SELECT COUNT(*) FROM documents")[0][0] == 1
    assert rows(db, "SELECT COUNT(*) FROM feed_ingests")[0][0] == 1
    assert "已转换文档" in cli(db, "item", "1").stdout

    again = cli(db, "import-json", str(source), "--feed-item", "1")
    assert again.returncode != 0
    assert "Traceback" not in again.stderr
    assert rows(db, "SELECT COUNT(*) FROM claims")[0][0] == 2
    assert rows(db, "SELECT COUNT(*) FROM documents")[0][0] == 1


def test_import_json_unknown_feed_item_leaves_no_rows(tmp_path):
    db = setup(tmp_path)
    result = cli(db, "import-json", str(manual_json(tmp_path)), "--feed-item", "99")
    assert result.returncode != 0
    assert rows(db, "SELECT COUNT(*) FROM documents")[0][0] == 0
    assert rows(db, "SELECT COUNT(*) FROM claims")[0][0] == 0


def test_manual_claim_link_failure_rolls_back(tmp_path, monkeypatch):
    from causal_ledger import cli as module

    db = setup(tmp_path)
    monkeypatch.setattr(module, "link_document",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("模拟接账失败")))
    with pytest.raises(SystemExit) as caught:
        module.main(["--db", str(db), "ingest", "1", "--claim", "虚构主张"])
    assert "模拟接账失败" in str(caught.value.code)
    assert rows(db, "SELECT COUNT(*) FROM documents")[0][0] == 0
    assert rows(db, "SELECT COUNT(*) FROM claims")[0][0] == 0
    assert rows(db, "SELECT COUNT(*) FROM feed_ingests")[0][0] == 0


def test_manual_claim_loop_predict_and_settle(tmp_path):
    db = setup(tmp_path)
    assert cli(db, "ingest", "1", "--claim", "虚构电池工厂投产",
               "--domain", "tech").returncode == 0
    predicted = cli(db, "predict", "1", "--text", "虚构工厂在 2027-01-31 前官网发布投产公告",
                    "--due", "2027-01-31", "--hint", "查虚构公司官网公告", "--p", "0.6")
    assert predicted.returncode == 0, predicted.stderr
    settled = cli(db, "settle", "1", "hit", "--note", "虚构演练：假设已观察到")
    assert settled.returncode == 0, settled.stderr
    assert rows(db, "SELECT status, brier FROM predictions")[0][0] == "hit"
    assert abs(rows(db, "SELECT brier FROM predictions")[0][0] - 0.16) < 1e-9
