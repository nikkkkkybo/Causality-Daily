"""7-day calibration: note parsing, preview/apply, and daily application."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from causal_ledger import calibrate, profile
from causal_ledger.db import connect, now_iso

ROOT = Path(__file__).resolve().parents[1]


def sample_profile(**overrides):
    data = {
        "version": "individual-baseline-v2",
        "basics": {"timezone": "Asia/Shanghai", "rhythm": "刚醒"},
        "position": {"phase": "不确定期", "problem": "转型", "goal": "做产品",
                     "leverage": "写作", "constraints": ["时间"]},
        "causal_position": {"role": "多重角色", "force": "增益", "horizon": "季",
                            "relations": ["资源"]},
        "domains": {"career": 2, "health": 2, "learning": 3},
        "psychology": {"values": ["成就"], "load": 3, "change_stage": "准备"},
    }
    data.update(overrides)
    return data


def add_note(conn, day: str, text: str) -> int:
    cursor = conn.execute(
        "INSERT INTO notes(day, text, created_at) VALUES (?,?,?)",
        (day, text, now_iso()),
    )
    conn.commit()
    return cursor.lastrowid


FEED = """<rss version="2.0"><channel><title>A</title>
<item><title>AI 工具改变工作方式</title><link>https://example.org/a</link>
<description>职业与技能</description><pubDate>Wed, 12 Nov 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""


def seed_feed(db):
    from causal_ledger.feeds import add_subscription, store_feed
    conn = connect(str(db))
    try:
        sid = add_subscription(conn, "源A", "https://example.org/feed")
        store_feed(conn, sid, FEED.encode("utf-8"), "https://example.org/feed")
    finally:
        conn.close()


def cli(db, *args):
    return subprocess.run(
        [sys.executable, "-m", "causal_ledger", "--db", str(db), *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        env={**{key: value for key, value in os.environ.items()
                if key not in ("CL_LLM_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY")},
             "PYTHONIOENCODING": "utf-8"}, timeout=30,
    )


def test_propose_parses_domains_and_sentiment(tmp_path):
    conn = connect(str(tmp_path / "cal.sqlite3"))
    try:
        today = date.today()
        add_note(conn, today.isoformat(), "事业这条很有用，继续")
        add_note(conn, today.isoformat(), "健康这条无关，别再推")
        add_note(conn, today.isoformat(), "今天天气不错")
        add_note(conn, (today - timedelta(days=30)).isoformat(), "事业这条很有用")
        proposal = calibrate.propose(conn, days=7)
        assert proposal["notes_count"] == 3
        assert proposal["deltas"]["career"] == pytest.approx(0.05)
        assert proposal["deltas"]["health"] == pytest.approx(-0.05)
        assert proposal["skipped"] == 1
        assert proposal["evidence"]["career"][0]["sentiment"] == 1
    finally:
        conn.close()


def test_apply_latest_and_effective_features_clamped(tmp_path):
    conn = connect(str(tmp_path / "cal.sqlite3"))
    try:
        profile.save_profile(conn, sample_profile())
        add_note(conn, date.today().isoformat(), "事业这条很有用")
        add_note(conn, date.today().isoformat(), "健康这条无关")
        proposal = calibrate.propose(conn, days=7)
        calibration_id = calibrate.apply(conn, proposal)
        assert calibration_id > 0
        stored = calibrate.latest(conn)
        assert stored["deltas"]["career"] == pytest.approx(0.05)
        loaded = profile.load_profile(conn)
        adjusted, note = calibrate.effective_features(conn, loaded["features"])
        assert adjusted["domains"]["career"] > loaded["features"]["domains"]["career"]
        assert adjusted["domains"]["health"] < loaded["features"]["domains"]["health"]
        assert "校准" in note and f"C#{calibration_id}" in note
        with pytest.raises(ValueError):
            calibrate.apply(conn, {"days": 7, "notes_count": 0, "deltas": {}})
    finally:
        conn.close()


def test_cli_calibrate_preview_apply_and_daily(tmp_path):
    db = tmp_path / "daily-cal.sqlite3"
    seed_feed(db)
    conn = connect(str(db))
    try:
        profile.save_profile(conn, sample_profile())
        add_note(conn, date.today().isoformat(), "事业这条很有用，继续")
    finally:
        conn.close()

    preview = cli(db, "calibrate")
    assert preview.returncode == 0, preview.stderr
    assert "预览" in preview.stdout and "事业" in preview.stdout
    applied = cli(db, "calibrate", "--apply")
    assert applied.returncode == 0, applied.stderr
    assert "已写入校准" in applied.stdout

    daily = cli(db, "daily", "--brief", "--no-fetch", "--mode", "auto")
    assert daily.returncode == 0, daily.stderr
    assert "校准：应用" in daily.stdout
    conn = connect(str(db))
    try:
        summary = json.loads(conn.execute(
            "SELECT summary_json FROM digests ORDER BY id DESC LIMIT 1").fetchone()[0])
        assert summary["calibration"]
    finally:
        conn.close()


def test_cli_calibrate_requires_profile(tmp_path):
    db = tmp_path / "empty-cal.sqlite3"
    result = cli(db, "calibrate")
    assert result.returncode != 0
    assert "请先导入个体档案" in result.stderr


def test_propose_validation():
    conn = connect(":memory:")
    try:
        with pytest.raises(ValueError):
            calibrate.propose(conn, days=0)
        with pytest.raises(ValueError):
            calibrate.propose(conn, days=91)
    finally:
        conn.close()
