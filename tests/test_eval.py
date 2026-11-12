"""Blind comparison and ablation for general vs individual lines."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from causal_ledger import evaluation, profile
from causal_ledger.db import connect

ROOT = Path(__file__).resolve().parents[1]


def sample_profile():
    return {
        "version": "individual-baseline-v2",
        "basics": {"timezone": "Asia/Shanghai", "rhythm": "刚醒"},
        "position": {"phase": "不确定期", "problem": "转型", "goal": "做产品",
                     "leverage": "写作", "constraints": ["时间"]},
        "causal_position": {"role": "多重角色", "force": "增益", "horizon": "季",
                            "relations": ["资源"]},
        "domains": {"career": 3, "learning": 3, "health": 2},
        "psychology": {"values": ["成就"], "load": 3, "change_stage": "准备"},
    }


FEED = """<rss version="2.0"><channel><title>A</title>
<item><title>AI 工具改变工作方式</title><link>https://example.org/a</link>
<description>职业与技能</description><pubDate>Wed, 12 Nov 2026 00:00:00 GMT</pubDate></item>
<item><title>城市天气</title><link>https://example.org/b</link>
<description>云量</description><pubDate>Wed, 12 Nov 2026 00:00:00 GMT</pubDate></item>
<item><title>健康睡眠研究</title><link>https://example.org/c</link>
<description>睡眠与身体</description><pubDate>Wed, 12 Nov 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""


def seed(db, with_profile=True):
    from causal_ledger.feeds import add_subscription, store_feed
    conn = connect(str(db))
    try:
        sid = add_subscription(conn, "源A", "https://example.org/feed")
        store_feed(conn, sid, FEED.encode("utf-8"), "https://example.org/feed")
        if with_profile:
            profile.save_profile(conn, sample_profile())
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


def test_blind_new_vote_stats(tmp_path):
    db = tmp_path / "eval.sqlite3"
    seed(db)
    conn = connect(str(db))
    try:
        directory = tmp_path / "runA"
        result = evaluation.blind_new(conn, directory, days=2, limit=10, per_source=2)
        assert Path(result["a"]).is_file() and Path(result["b"]).is_file()
        meta = json.loads((directory / "blind.json").read_text(encoding="utf-8"))
        assert {meta["a_kind"], meta["b_kind"]} == {"general", "individual"}
        assert meta["run_id"]
        with pytest.raises(FileExistsError):
            evaluation.blind_new(conn, directory, days=2)
        choice = "A"
        voted = evaluation.blind_vote(conn, directory, choice)
        assert voted["winner"] == meta["a_kind"]
        stats = evaluation.blind_stats(conn)
        assert stats["total"] == 1
        assert stats["individual"] + stats["general"] == 1
    finally:
        conn.close()


def test_ablate_variants_and_overlap(tmp_path):
    db = tmp_path / "eval.sqlite3"
    seed(db)
    conn = connect(str(db))
    try:
        results = evaluation.ablate(conn, days=2, limit=10, per_source=2)
        names = [item["name"] for item in results]
        assert names == ["general", "individual", "individual-no-calibration",
                         "individual-uniform-domains", "individual-light"]
        by_name = {item["name"]: item for item in results}
        assert by_name["individual"]["overlap"] == 1.0
        assert 0.0 <= by_name["general"]["overlap"] <= 1.0
        assert isinstance(by_name["general"]["same_order"], int)
        assert "calibration" not in by_name  # sanity: results are dicts, not meta
    finally:
        conn.close()


def test_cli_eval_blind_vote_stats_ablate(tmp_path):
    db = tmp_path / "eval-cli.sqlite3"
    seed(db)
    directory = tmp_path / "blind-cli"
    blind = cli(db, "eval", "blind", "--dir", str(directory))
    assert blind.returncode == 0, blind.stderr
    assert "盲评已生成" in blind.stdout and "blind.json" in blind.stdout
    vote = cli(db, "eval", "vote", "A", "--dir", str(directory))
    assert vote.returncode == 0, vote.stderr
    assert "揭晓" in vote.stdout
    stats = cli(db, "eval", "stats")
    assert stats.returncode == 0, stats.stderr
    assert "盲评投票" in stats.stdout
    ablate = cli(db, "eval", "ablate")
    assert ablate.returncode == 0, ablate.stderr
    assert "消融" in ablate.stdout and "individual" in ablate.stdout


def test_blind_requires_profile(tmp_path):
    db = tmp_path / "no-profile.sqlite3"
    seed(db, with_profile=False)
    result = cli(db, "eval", "blind", "--dir", str(tmp_path / "run"))
    assert result.returncode != 0
    assert "盲评需要个体档案" in result.stderr
