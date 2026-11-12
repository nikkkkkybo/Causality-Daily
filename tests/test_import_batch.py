"""Offline regressions for batch import-json --dir."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def cli(db, *args):
    env = {key: value for key, value in os.environ.items()
           if key not in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "CL_LLM_API_KEY")}
    return subprocess.run([sys.executable, "-m", "causal_ledger", "--db", str(db), *args],
                          cwd=ROOT, env={**env, "PYTHONIOENCODING": "utf-8"},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def payload(key, text="虚构主张"):
    return {"reporter": {"name": "虚构来源", "type": "official"},
            "claims": [{"text": text, "canonical_key": key, "domain": "tech",
                        "layer": "plan", "origin": None, "quote": "虚构原文",
                        "hard_signal": None, "predictions": []}]}


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def count(db, table):
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_dir_imports_all_json_files(tmp_path):
    folder = tmp_path / "batch"
    folder.mkdir()
    write(folder / "a.json", payload("虚构主体|发布|甲"))
    write(folder / "b.json", payload("虚构主体|发布|乙", text="虚构主张乙"))
    db = tmp_path / "batch.sqlite3"
    result = cli(db, "import-json", "--dir", str(folder))
    assert result.returncode == 0, result.stderr
    assert "完成" in result.stdout and "a.json" in result.stdout and "b.json" in result.stdout
    assert result.stdout.count("完成") == 2
    assert "未经校准" in result.stdout
    assert count(db, "claims") == 2
    assert count(db, "documents") == 2


def test_dir_partial_failure_keeps_successes_and_returns_nonzero(tmp_path):
    folder = tmp_path / "batch"
    folder.mkdir()
    write(folder / "good.json", payload("虚构主体|发布|好"))
    (folder / "bad.json").write_text("{ this is not json", encoding="utf-8")
    db = tmp_path / "partial.sqlite3"
    result = cli(db, "import-json", "--dir", str(folder))
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "失败" in result.stdout and "bad.json" in result.stdout
    assert count(db, "claims") == 1
    assert count(db, "documents") == 1


def test_dir_empty_or_missing_fails_cleanly(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    db = tmp_path / "empty.sqlite3"
    for target in (empty, tmp_path / "missing"):
        result = cli(db, "import-json", "--dir", str(target))
        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        assert result.stderr.strip()


def test_dir_rejects_title_and_requires_one_input(tmp_path):
    folder = tmp_path / "batch"
    folder.mkdir()
    write(folder / "a.json", payload("虚构主体|发布|甲"))
    db = tmp_path / "args.sqlite3"
    titled = cli(db, "import-json", "--dir", str(folder), "--title", "x")
    assert titled.returncode != 0 and "Traceback" not in titled.stderr
    both = cli(db, "import-json", str(folder / "a.json"), "--dir", str(folder))
    assert both.returncode != 0 and "Traceback" not in both.stderr
    neither = cli(db, "import-json")
    assert neither.returncode != 0 and "Traceback" not in neither.stderr


def test_single_file_import_still_works(tmp_path):
    source = tmp_path / "single.json"
    write(source, payload("虚构主体|发布|单"))
    db = tmp_path / "single.sqlite3"
    result = cli(db, "import-json", str(source))
    assert result.returncode == 0, result.stderr
    assert count(db, "claims") == 1


def test_utf8_bom_json_is_accepted(tmp_path):
    folder = tmp_path / "bom"
    folder.mkdir()
    (folder / "bom.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps(payload("虚构主体|发布|BOM"), ensure_ascii=False).encode("utf-8"))
    db = tmp_path / "bom.sqlite3"
    result = cli(db, "import-json", "--dir", str(folder))
    assert result.returncode == 0, result.stderr
    assert count(db, "claims") == 1
    single = tmp_path / "single-bom.json"
    single.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload("虚构主体|发布|单BOM"),
                                                    ensure_ascii=False).encode("utf-8"))
    assert cli(db, "import-json", str(single)).returncode == 0


def test_dir_continues_after_per_file_database_error(tmp_path):
    from causal_ledger.db import connect as ledger_connect

    folder = tmp_path / "db-error"
    folder.mkdir()
    write(folder / "ok.json", payload("虚构主体|发布|甲", text="虚构主张甲"))
    write(folder / "boom.json", payload("虚构主体|发布|乙", text="虚构主张乙"))
    db = tmp_path / "db-error.sqlite3"
    conn = ledger_connect(db)
    conn.execute("CREATE TRIGGER reject_b BEFORE INSERT ON claims "
                 "WHEN NEW.text='虚构主张乙' BEGIN SELECT RAISE(ABORT, '测试失败'); END")
    conn.commit()
    conn.close()
    result = cli(db, "import-json", "--dir", str(folder))
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "失败" in result.stdout
    assert count(db, "claims") == 1
