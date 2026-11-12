from argparse import Namespace
from datetime import date, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from causal_ledger.db import connect
from causal_ledger.provenance import add_prediction, import_extraction
from causal_ledger.report import render_report
from causal_ledger.settle import settle

ROOT = Path(__file__).resolve().parents[1]


def cli(db, *args):
    return subprocess.run(
        [sys.executable, "-m", "causal_ledger", "--db", str(db), *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        env={**{key: value for key, value in os.environ.items()
                if key not in ("CL_LLM_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY")},
             "PYTHONIOENCODING": "utf-8"}, timeout=30,
    )


def fixture_json(tmp_path):
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps({
        "reporter": {"name": "虚构演练", "type": "official"},
        "claims": [{"text": "虚构产品正在研发", "canonical_key": "虚构产品|研发|本期",
                    "layer": "formation", "hard_signal": "待查公告"}],
    }, ensure_ascii=False), encoding="utf-8")
    return path


def test_cli_offline_roundtrip(tmp_path):
    db = tmp_path / "demo.sqlite3"
    imported = cli(db, "import-json", str(fixture_json(tmp_path)))
    assert imported.returncode == 0, imported.stderr
    assert "实验分" in imported.stdout
    due = (date.today() + timedelta(days=7)).isoformat()
    created = cli(db, "predict", "1", "--text", "虚构新品出现", "--due", due, "--hint", "演练核验渠道")
    assert created.returncode == 0, created.stderr
    for command in (("claims",), ("explain", "1"), ("due",), ("sources",), ("report",)):
        result = cli(db, *command)
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stdout
    explanation = cli(db, "explain", "1").stdout
    assert "待核查" in explanation and "未登记" in explanation
    settled = cli(db, "settle", "1", "hit", "--note", "虚构演练：已观察到，不代表真实新闻")
    assert settled.returncode == 0, settled.stderr
    assert "hit" in settled.stdout and "作废" not in settled.stdout
    report = cli(db, "report").stdout
    assert "hit" in report and "未经校准" in report and "未登记概率" in report
    assert "不代表真实新闻" in report
    assert "已结算 1" in report
    duplicate = cli(db, "settle", "1", "void", "--note", "重复")
    assert duplicate.returncode != 0 and "Traceback" not in duplicate.stderr


def test_llm_generated_probability_is_not_registered(monkeypatch):
    from causal_ledger import cli as cli_module
    due = (date.today() + timedelta(days=7)).isoformat()
    generated = {"claims": [{"text": "虚构承诺", "predictions": [
        {"text": "虚构承诺", "due_date": due, "check_hint": "待核验", "p": 0.99},
    ]}]}
    monkeypatch.setattr(cli_module, "extract_claims", lambda *args, **kwargs: generated)
    args = Namespace(text="虚构承诺", file=None, url=None, reporter=None,
                     reporter_type=None, dump=False, title="虚构演练")
    conn = connect(":memory:")
    try:
        cli_module.cmd_import(args, conn)
        assert conn.execute("SELECT p FROM predictions").fetchone()[0] is None
    finally:
        conn.close()


def test_cli_explicit_prediction_probability(tmp_path):
    db = tmp_path / "demo.sqlite3"
    assert cli(db, "import-json", str(fixture_json(tmp_path))).returncode == 0
    due = (date.today() + timedelta(days=7)).isoformat()
    result = cli(db, "predict", "1", "--text", "另一虚构预测", "--due", due, "--hint", "演练", "--p", "0.7")
    assert result.returncode == 0, result.stderr
    result = cli(db, "settle", "1", "hit", "--note", "虚构命中")
    assert result.returncode == 0 and "Brier=0.090" in result.stdout


def test_remote_import_needs_explicit_consent_before_database_creation(tmp_path):
    db = tmp_path / "private.sqlite3"
    result = cli(db, "import", "--text", "不得发送的测试文本")
    assert result.returncode != 0
    assert "--allow-remote" in result.stderr
    assert not db.exists()


def test_import_inputs_are_mutually_exclusive(tmp_path):
    result = cli(tmp_path / "demo.sqlite3", "import", "--text", "a", "--file", "missing")
    assert result.returncode != 0
    assert "not allowed" in result.stderr or "不能" in result.stderr


def test_malformed_json_is_friendly_and_empty(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"claims":[{"text":""}]}', encoding="utf-8")
    db = tmp_path / "demo.sqlite3"
    result = cli(db, "import-json", str(path))
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


@pytest.mark.parametrize("days", [0, -1])
def test_report_rejects_invalid_window(days):
    conn = connect(":memory:")
    try:
        with pytest.raises(ValueError):
            render_report(conn, days=days)
    finally:
        conn.close()


def test_report_explicit_date_bounds_all_event_sections():
    conn = connect(":memory:")
    try:
        for name, stamp in (("历史条目", "2020-01-01T12:00:00+00:00"), ("未来条目", "2030-01-01T12:00:00+00:00")):
            import_extraction(conn, {"claims": [{"text": name}]})
            conn.execute("UPDATE claims SET created_at=? WHERE text=?", (stamp, name))
        conn.execute("DELETE FROM credibility_log")
        conn.commit()
        result = render_report(conn, days=1, today="2020-01-01")
        assert "历史条目" in result
        assert "未来条目" not in result
    finally:
        conn.close()
