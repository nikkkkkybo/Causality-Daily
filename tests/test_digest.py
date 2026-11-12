"""Offline regressions for digest history records."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from causal_ledger import digest
from causal_ledger.db import connect

ROOT = Path(__file__).resolve().parents[1]


def cli(db, *args, env_extra=None):
    env = {key: value for key, value in os.environ.items()
           if key not in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "CL_LLM_API_KEY")}
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, "-m", "causal_ledger", "--db", str(db), *args],
                          cwd=ROOT, env={**env, "PYTHONIOENCODING": "utf-8"},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_record_and_list_roundtrip():
    conn = connect(":memory:")
    try:
        digest_id = digest.record_digest(conn, kind="daily", path="/tmp/x.txt", ok=True,
                                         summary={"days": 7, "queue": 3})
        rows = digest.list_digests(conn)
        assert len(rows) == 1
        assert rows[0]["id"] == digest_id
        assert rows[0]["kind"] == "daily"
        assert rows[0]["ok"] == 1
        assert json.loads(rows[0]["summary_json"]) == {"days": 7, "queue": 3}
        with pytest.raises(ValueError):
            digest.list_digests(conn, limit=0)
        with pytest.raises(ValueError):
            digest.record_digest(conn, kind="", path=None, ok=True, summary={})
    finally:
        conn.close()


def test_cli_daily_records_digest_and_lists_it(tmp_path):
    db = tmp_path / "digest.sqlite3"
    result = cli(db, "daily", "--no-fetch")
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT kind, ok, path, summary_json FROM digests").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "daily" and rows[0][1] == 1 and rows[0][2] is None
    assert json.loads(rows[0][3])["no_fetch"] is True

    listed = cli(db, "digests")
    assert listed.returncode == 0 and "D#1" in listed.stdout and "daily" in listed.stdout


def test_daily_output_path_is_recorded(tmp_path):
    db = tmp_path / "digest-path.sqlite3"
    output = tmp_path / "daily.txt"
    result = cli(db, "daily", "--no-fetch", "--output", str(output))
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(db) as conn:
        path = conn.execute("SELECT path FROM digests").fetchone()[0]
    assert path == str(output)


def test_partial_failure_digest_ok_zero(tmp_path, monkeypatch):
    from argparse import Namespace
    from causal_ledger import cli as module
    from causal_ledger.db import connect as ledger_connect
    from causal_ledger.feeds import add_subscription

    db = tmp_path / "partial.sqlite3"
    conn = ledger_connect(db)
    add_subscription(conn, "虚构", "https://example.org/feed.xml")
    conn.close()
    monkeypatch.setattr(module, "collect_feeds", lambda *a, **k: [
        {"feed_id": 1, "added": 0, "duplicates": 0, "error": "模拟采集失败"}])
    with pytest.raises(SystemExit) as caught:
        module.main(["--db", str(db), "daily", "--output", str(tmp_path / "partial.txt")])
    assert caught.value.code
    with sqlite3.connect(db) as raw:
        ok = raw.execute("SELECT ok FROM digests").fetchone()[0]
        summary = json.loads(raw.execute("SELECT summary_json FROM digests").fetchone()[0])
    assert ok == 0
    assert summary["feed_failures"] == 1
