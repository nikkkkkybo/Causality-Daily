"""Offline regressions for the query layer: documents and claim search."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from causal_ledger import query
from causal_ledger.db import connect
from causal_ledger.provenance import import_extraction

ROOT = Path(__file__).resolve().parents[1]


def cli(db, *args):
    env = {key: value for key, value in os.environ.items()
           if key not in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "CL_LLM_API_KEY")}
    return subprocess.run([sys.executable, "-m", "causal_ledger", "--db", str(db), *args],
                          cwd=ROOT, env={**env, "PYTHONIOENCODING": "utf-8"},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def extraction(reporter, claims):
    return {"reporter": reporter, "claims": claims}


def claim(text, key, origin=None, layer="manifestation", domain="tech"):
    return {
        "text": text,
        "canonical_key": key,
        "domain": domain,
        "layer": layer,
        "origin": origin,
        "quote": text,
    }


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def seeded(conn):
    import_extraction(conn, extraction(
        {"name": "虚构通讯社", "type": "institutional_media"},
        [claim("虚构电池公司发布新品", "虚构电池公司|发布|新品",
               origin={"name": "虚构通讯社", "type": "institutional_media"})],
    ), title="文档甲", url="https://example.org/a", raw="完全虚构的演练文本甲")
    import_extraction(conn, extraction(
        {"name": "虚构博主", "type": "anonymous"},
        [claim("虚构电池回收补贴传闻", "虚构电池公司|补贴|传闻", layer="intent", domain="policy")],
    ), title="文档乙", url="https://example.org/b", raw="完全虚构的演练文本乙")
    return conn


# ---------------------------------------------------------------- documents

def test_list_documents_returns_fields_and_newest_first(seeded):
    rows = query.list_documents(seeded)
    assert [r["title"] for r in rows] == ["文档乙", "文档甲"]
    row = rows[0]
    assert row["id"] == 2
    assert row["url"] == "https://example.org/b"
    assert row["reporter"] == "虚构博主"
    assert row["imported_at"]
    assert row["claim_count"] == 1


def test_list_documents_since_filter_uses_local_day(seeded):
    seeded.execute("UPDATE documents SET imported_at='2000-01-01T00:00:00+00:00' WHERE id=1")
    rows = query.list_documents(seeded, since="2001-01-01")
    assert [r["id"] for r in rows] == [2]
    assert [r["id"] for r in query.list_documents(seeded, since="1999-01-01")] == [2, 1]


def test_list_documents_source_filter_matches_reporter(seeded):
    rows = query.list_documents(seeded, source="虚构通讯社")
    assert [r["id"] for r in rows] == [1]


def test_list_documents_limit_and_validation(seeded):
    assert len(query.list_documents(seeded, limit=1)) == 1
    for bad in ({"limit": 0}, {"limit": 101}, {"limit": "2"}, {"limit": True},
                {"since": "2026-13-01"}, {"since": "n/a"}, {"source": "  "}):
        with pytest.raises(ValueError):
            query.list_documents(seeded, **bad)


def test_get_document_includes_claims_and_ingest(seeded):
    seeded.execute(
        "INSERT INTO subscriptions(name,url,enabled,created_at)"
        " VALUES ('虚构订阅','https://example.org/feed.xml',1,'2026-11-11T00:00:00+00:00')"
    )
    seeded.execute(
        "INSERT INTO feed_items(feed_id,item_key,title,summary,fetched_at)"
        " VALUES (1,'k','条目','摘要','2026-11-11T00:00:00+00:00')"
    )
    seeded.execute(
        "INSERT INTO feed_ingests(feed_item_id, document_id, created_at)"
        " VALUES (1, 1, '2026-11-11T00:00:00+00:00')"
    )
    detail = query.get_document(seeded, 1)
    assert detail["document"]["title"] == "文档甲"
    assert [c["id"] for c in detail["claims"]] == [1]
    assert detail["claims"][0]["text"] == "虚构电池公司发布新品"
    assert detail["document"]["raw_length"] == len("完全虚构的演练文本甲")


def test_get_document_missing_raises(seeded):
    with pytest.raises(ValueError):
        query.get_document(seeded, 999)


# ---------------------------------------------------------------- search

def test_search_matches_text_and_key_case_insensitively(seeded):
    assert [r["id"] for r in query.search_claims(seeded, ["电池"])] == [1, 2]
    assert [r["id"] for r in query.search_claims(seeded, ["虚构电池公司|发布"])] == [1]
    import_extraction(seeded, extraction(
        {"name": "English", "type": "unknown"},
        [claim("Battery storage update", "battery|store|update")],
    ), title="doc-en")
    results = query.search_claims(seeded, ["BATTERY"])
    assert results and results[0]["text"] == "Battery storage update"


def test_search_requires_all_keywords(seeded):
    assert query.search_claims(seeded, ["电池", "补贴"])[0]["id"] == 2
    assert query.search_claims(seeded, ["电池", "不存在的词"]) == []


def test_search_filters(seeded):
    assert [r["id"] for r in query.search_claims(seeded, ["电池"], domain="policy")] == [2]
    assert [r["id"] for r in query.search_claims(seeded, ["电池"], layer="intent")] == [2]
    assert [r["id"] for r in query.search_claims(seeded, ["电池"], min_credibility=0.5)] == [1]
    assert [r["id"] for r in query.search_claims(seeded, ["电池"], source="虚构通讯社")] == [1]


def test_search_since_filter(seeded):
    seeded.execute("UPDATE claims SET created_at='2000-01-01T00:00:00+00:00' WHERE id=2")
    assert [r["id"] for r in query.search_claims(seeded, ["电池"], since="2001-01-01")] == [1]


def test_search_limit_and_validation(seeded):
    assert len(query.search_claims(seeded, ["电池"], limit=1)) == 1
    for bad in ({"limit": 0}, {"limit": 101}, {"limit": "2"}, {"limit": True},
                {"min_credibility": -0.1}, {"min_credibility": 1.1},
                {"min_credibility": "0.5"}, {"since": "2026-02-30"},
                {"source": ""}, {"domain": ""}, {"layer": 3}):
        with pytest.raises(ValueError):
            query.search_claims(seeded, ["电池"], **bad)
    with pytest.raises(ValueError):
        query.search_claims(seeded, [])
    with pytest.raises(ValueError):
        query.search_claims(seeded, ["  "])


def test_search_returns_disclaimer_metadata(seeded):
    results = query.search_claims(seeded, ["电池"])
    assert all("未经校准" in r["disclaimer"] for r in results)
    assert all(r["origins"] >= 1 for r in results)


def test_search_unicode_casefold(seeded):
    import_extraction(seeded, extraction(
        {"name": "Unicode", "type": "unknown"},
        [claim("Ärger im Büro", "ärger|büro|fall"),
         claim("Straße gesperrt", "straße|gesperrt|fall")],
    ), title="unicode")
    assert [r["text"] for r in query.search_claims(seeded, ["Ärger"])] == ["Ärger im Büro"]
    assert [r["text"] for r in query.search_claims(seeded, ["ärger"])] == ["Ärger im Büro"]
    assert [r["text"] for r in query.search_claims(seeded, ["STRASSE"])] == ["Straße gesperrt"]
    assert [r["text"] for r in query.search_claims(seeded, ["straße"])] == ["Straße gesperrt"]


def test_search_escapes_like_wildcards(seeded):
    import_extraction(seeded, extraction(
        {"name": "Percent", "type": "unknown"},
        [claim("价格100%上涨", "价格|上涨|100%"),
         claim("价格上涨", "价格|上涨|未知")],
    ), title="percent")
    assert [r["text"] for r in query.search_claims(seeded, ["100%"])] == ["价格100%上涨"]
    assert [r["text"] for r in query.search_claims(seeded, ["_%"])] == []
    assert [r["text"] for r in query.search_claims(seeded, ["%"])] == ["价格100%上涨"]


def test_search_empty_database_returns_empty(conn):
    assert query.search_claims(conn, ["任意词"]) == []
    assert query.list_documents(conn) == []
    with pytest.raises(ValueError):
        query.get_document(conn, 1)


def test_since_filter_uses_local_calendar_day(conn):
    import_extraction(conn, extraction(
        {"name": "时区", "type": "unknown"}, [claim("边界主张", "边界|测试|值")],
    ), title="边界")
    local_tz = datetime.now().astimezone().tzinfo
    boundary = datetime(2026, 11, 11, tzinfo=local_tz).astimezone(timezone.utc)
    conn.execute("UPDATE claims SET created_at=?", ((boundary - timedelta(seconds=1)).isoformat(),))
    assert query.search_claims(conn, ["边界"], since="2026-11-11") == []
    conn.execute("UPDATE claims SET created_at=?", (boundary.isoformat(),))
    assert [r["text"] for r in query.search_claims(conn, ["边界"], since="2026-11-11")] == ["边界主张"]


def test_cli_query_commands(tmp_path):
    db = tmp_path / "query.sqlite3"
    source = tmp_path / "a.json"
    source.write_text(json.dumps({
        "reporter": {"name": "虚构来源", "type": "official"},
        "claims": [
            {"text": "虚构电池发布", "canonical_key": "虚构|发布|电池",
             "domain": "tech", "layer": "plan", "origin": None, "quote": "q"},
            {"text": "虚构补贴传闻", "canonical_key": "虚构|补贴|传闻",
             "domain": "policy", "layer": "intent", "origin": None, "quote": "q"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    imported = cli(db, "import-json", str(source))
    assert imported.returncode == 0, imported.stderr

    docs = cli(db, "docs")
    assert docs.returncode == 0 and "a.json" in docs.stdout and "未核实" in docs.stdout
    doc = cli(db, "doc", "1")
    assert doc.returncode == 0 and "虚构电池发布" in doc.stdout and "关联主张（2）" in doc.stdout
    found = cli(db, "search", "电池", "--domain", "tech", "--layer", "plan", "--min", "0")
    assert found.returncode == 0 and "虚构电池发布" in found.stdout and "虚构补贴传闻" not in found.stdout
    none = cli(db, "search", "不存在的词")
    assert none.returncode == 0 and "无匹配" in none.stdout
    bad = cli(db, "search", "电池", "--layer", "bogus")
    assert bad.returncode != 0 and "Traceback" not in bad.stderr
    bad_limit = cli(db, "docs", "--limit", "0")
    assert bad_limit.returncode != 0 and "Traceback" not in bad_limit.stderr
