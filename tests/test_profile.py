"""Profile storage, completeness, mode selection and personal ranking."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from causal_ledger import personalize, profile
from causal_ledger.db import connect

ROOT = Path(__file__).resolve().parents[1]


def sample_profile(**overrides):
    data = {
        "version": "individual-baseline-v2",
        "basics": {"timezone": "Asia/Shanghai", "rhythm": "刚醒，想先看一眼"},
        "position": {
            "phase": "不确定期",
            "problem": "把工作从执行转向定义问题",
            "goal": "做出一个自己的产品",
            "leverage": "写作、行业人脉",
            "constraints": ["时间", "钱"],
        },
        "causal_position": {
            "role": "多重角色",
            "protect": "收入稳定",
            "force": "增益",
            "horizon": "季",
            "relations": ["资源", "规则"],
        },
        "domains": {"career": 3, "wealth": 2, "learning": 3, "health": 2},
        "psychology": {"values": ["成就", "学习"], "load": 3, "change_stage": "准备"},
    }
    data.update(overrides)
    return data


def test_validate_profile_rejects_bad_input():
    with pytest.raises(ValueError):
        profile.validate_profile("not a dict")
    with pytest.raises(ValueError):
        profile.validate_profile({"domains": {"unknown": 1}})
    with pytest.raises(ValueError):
        profile.validate_profile({"domains": {"career": 4}})
    normalized = profile.validate_profile({"domains": {"career": 3}})
    assert normalized["domains"] == {"career": 3.0}


def test_completeness_and_mode_thresholds():
    assert profile.completeness(None) == 0.0
    empty = profile.completeness({})
    assert empty == 0.0
    rich = profile.completeness(sample_profile())
    assert 0.7 <= rich <= 1.0
    assert profile.choose_mode(None) == "general"
    assert profile.choose_mode({"completeness": 0.2}) == "general"
    assert profile.choose_mode({"completeness": 0.5}) == "light"
    assert profile.choose_mode({"completeness": 0.8}) == "individual"
    assert profile.choose_mode(None, "individual") == "general"
    assert profile.choose_mode({"completeness": 0.8}, "light") == "light"
    with pytest.raises(ValueError):
        profile.choose_mode(None, "nonsense")


def test_save_load_clear_roundtrip(tmp_path):
    conn = connect(str(tmp_path / "db.sqlite3"))
    try:
        assert profile.load_profile(conn) is None
        saved = profile.save_profile(conn, sample_profile())
        assert saved["completeness"] > 0.7
        loaded = profile.load_profile(conn)
        assert loaded is not None
        assert loaded["completeness"] == saved["completeness"]
        assert loaded["features"]["domains"]["career"] == 1.0
        assert loaded["features"]["constraints"] == ["时间", "钱"]
        assert loaded["features"]["completeness"] == saved["completeness"]
        profile.save_profile(conn, sample_profile(domains={"career": 1}), mode="light")
        again = profile.load_profile(conn)
        assert again["mode"] == "light"
        assert profile.clear_profile(conn) == 1
        assert profile.load_profile(conn) is None
    finally:
        conn.close()


def test_derive_features_is_non_sensitive():
    features = profile.derive_features({"data": sample_profile()})
    assert features["domains"]["learning"] == 1.0
    assert features["role"] == "多重角色"
    assert features["goals"]["goal"] == "做出一个自己的产品"
    assert "birth" not in features and "events" not in features


@pytest.mark.skipif(not __import__("causal_ledger.crypto", fromlist=["crypto"]).available(),
                    reason="需要 Windows DPAPI")
def test_vault_encrypts_sensitive_fields_and_audits_unlock(tmp_path):
    conn = connect(str(tmp_path / "vault.sqlite3"))
    try:
        data = sample_profile(
            position={**sample_profile()["position"], "events": "家人生病，换工作"},
            causality={"birth": "1990-01-01 08:00 上海"},
            quadrants={"mind": {"change": "一次失败的项目"}},
            privacy={"answers": {"birth": {"value": "1990-01-01 08:00 上海", "privacy": "保险箱"}}},
        )
        saved = profile.save_profile(conn, data)
        assert saved["has_vault"] is True
        raw = conn.execute("SELECT data_json FROM profiles WHERE id=1").fetchone()[0]
        assert "家人生病" not in raw and "1990-01-01" not in raw and "失败的项目" not in raw
        loaded = profile.load_profile(conn)
        assert loaded["has_vault"] is True
        assert "position.events" in loaded["vault_fields"]
        assert profile.vault_status(conn)["fields"] == loaded["vault_fields"]
        # show/ranking path must not need the vault
        assert loaded["features"]["role"] == "多重角色"
        vault = profile.unlock_vault(conn, "一次性查看出生数据以做时间层")
        assert vault["causality.birth"] == "1990-01-01 08:00 上海"
        assert vault["position.events"] == "家人生病，换工作"
        audit = profile.list_vault_audit(conn)
        assert audit[0]["action"] == "unlock"
        assert "时间层" in audit[0]["reason"]
        with pytest.raises(ValueError):
            profile.unlock_vault(conn, "")
        profile.clear_profile(conn)
        assert conn.execute("SELECT COUNT(*) FROM vault").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.skipif(not __import__("causal_ledger.crypto", fromlist=["crypto"]).available(),
                    reason="需要 Windows DPAPI")
def test_cli_profile_show_does_not_leak_vault_and_unlock_is_audited(tmp_path):
    db = tmp_path / "vault-cli.sqlite3"
    source = tmp_path / "vault.json"
    payload = sample_profile(causality={"birth": "1990-01-01 08:00 上海"})
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    imported = cli(db, "profile", "import", str(source))
    assert imported.returncode == 0, imported.stderr
    shown = cli(db, "profile", "show")
    assert shown.returncode == 0, shown.stderr
    assert "保险箱" in shown.stdout
    assert "1990-01-01" not in shown.stdout
    unlocked = cli(db, "profile", "unlock", "--reason", "测试解锁")
    assert unlocked.returncode == 0, unlocked.stderr
    assert "1990-01-01" not in unlocked.stdout
    audit = cli(db, "profile", "audit")
    assert audit.returncode == 0, audit.stderr
    assert "unlock" in audit.stdout and "测试解锁" in audit.stdout


def test_personalize_ranks_items_and_adds_lines():
    features = profile.derive_features({"data": sample_profile()})
    items = [
        {"id": 1, "title": "城市漫步指南", "summary": "旅行与生活方式"},
        {"id": 2, "title": "AI 工具改变工作方式", "summary": "职业与技能"},
        {"id": 3, "title": "家庭储蓄与消费", "summary": "钱和财务"},
    ]
    ranked = personalize.rank_items(items, features, "individual")
    assert ranked[0]["personal_relevance"] >= ranked[-1]["personal_relevance"]
    assert ranked[0]["personal_why"]
    assert ranked[0]["personal_segment"]
    lines = personalize.lines_for(ranked[0], "individual")
    assert any("与你有关" in line for line in lines)
    assert any("你在哪一段" in line for line in lines)


def test_personalize_general_mode_does_not_touch_items():
    features = profile.derive_features({"data": sample_profile()})
    items = [{"id": 1, "title": "AI 工作", "summary": "职业"}]
    same = personalize.rank_items(items, features, "general")
    assert same == items
    assert personalize.lines_for(items[0], "general") == []


def test_personalize_no_domain_match_is_kept_generic():
    features = profile.derive_features({"data": sample_profile()})
    item = {"id": 9, "title": "某地天气", "summary": "明天的云量"}
    annotated = personalize.annotate(item, features, "individual")
    assert annotated["personal_relevance"] == 0.0
    assert "personal_why" not in annotated


def cli(db, *args):
    return subprocess.run(
        [sys.executable, "-m", "causal_ledger", "--db", str(db), *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        env={**{key: value for key, value in os.environ.items()
                if key not in ("CL_LLM_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY")},
             "PYTHONIOENCODING": "utf-8"}, timeout=30,
    )


FEED = """<rss version="2.0"><channel><title>A</title>
<item><title>AI 工具改变工作方式</title><link>https://example.org/a</link>
<description>职业与技能</description><pubDate>Wed, 12 Nov 2026 00:00:00 GMT</pubDate></item>
<item><title>城市的天气</title><link>https://example.org/b</link>
<description>云量</description><pubDate>Wed, 12 Nov 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""


def seed_feed(db):
    from causal_ledger.feeds import add_subscription, store_feed
    conn = connect(str(db))
    try:
        sid = add_subscription(conn, "源A", "https://example.org/feed")
        store_feed(conn, sid, FEED.encode("utf-8"), "https://example.org/feed")
    finally:
        conn.close()


def personalization_for(conn):
    stored = profile.save_profile(conn, sample_profile())
    loaded = profile.load_profile(conn)
    return {"mode": "individual", "features": loaded["features"],
            "completeness": loaded["completeness"]}


def test_render_brief_personalization_adds_lines(tmp_path):
    from causal_ledger.report import render_brief
    db = tmp_path / "brief.sqlite3"
    seed_feed(db)
    conn = connect(str(db))
    try:
        personalization = personalization_for(conn)
        text = render_brief(conn, days=2, no_fetch=True, personalization=personalization)
        assert "模式：个体线" in text
        assert "与你有关" in text and "你在哪一段" in text
        general = render_brief(conn, days=2, no_fetch=True,
                               personalization={"mode": "general",
                                                "features": personalization["features"]})
        assert "与你有关" not in general
    finally:
        conn.close()


def test_daily_cli_mode_records_digest_and_downgrades(tmp_path):
    db = tmp_path / "daily.sqlite3"
    seed_feed(db)
    conn = connect(str(db))
    try:
        profile.save_profile(conn, sample_profile())
    finally:
        conn.close()
    run = cli(db, "daily", "--brief", "--no-fetch", "--mode", "auto")
    assert run.returncode == 0, run.stderr
    assert "模式：个体线" in run.stdout
    conn = connect(str(db))
    try:
        summary = json.loads(conn.execute(
            "SELECT summary_json FROM digests ORDER BY id DESC LIMIT 1").fetchone()[0])
        assert summary["mode"] == "individual"
    finally:
        conn.close()

    empty_db = tmp_path / "empty.sqlite3"
    downgraded = cli(empty_db, "daily", "--brief", "--no-fetch", "--mode", "individual")
    assert downgraded.returncode == 0, downgraded.stderr
    assert "未设置档案，已降级为通线" in downgraded.stdout


def test_cli_profile_import_show_clear(tmp_path):
    db = tmp_path / "profile.sqlite3"
    source = tmp_path / "profile.json"
    source.write_text(json.dumps(sample_profile(), ensure_ascii=False), encoding="utf-8-sig")
    imported = cli(db, "profile", "import", str(source))
    assert imported.returncode == 0, imported.stderr
    assert "档案已导入" in imported.stdout
    shown = cli(db, "profile", "show")
    assert shown.returncode == 0, shown.stderr
    assert "完整度" in shown.stdout and "事业" in shown.stdout
    cleared = cli(db, "profile", "clear")
    assert cleared.returncode == 0, cleared.stderr
    empty = cli(db, "profile", "show")
    assert empty.returncode == 0
    assert "暂无档案" in empty.stdout
