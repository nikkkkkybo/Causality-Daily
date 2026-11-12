"""CLI help consistency checks (P1-4): every command documents itself and boundaries."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ["import", "import-json", "claims", "explain", "predict", "settle", "due",
            "sources", "report", "feed", "collect", "inbox", "item", "read", "daily",
            "docs", "doc", "search", "ingest", "digests", "watch", "profile",
            "calibrate", "eval", "cloud", "serve"]


def cli(*args):
    env = {key: value for key, value in os.environ.items()
           if key not in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "CL_LLM_API_KEY")}
    return subprocess.run([sys.executable, "-m", "causal_ledger", *args],
                          cwd=ROOT, env={**env, "PYTHONIOENCODING": "utf-8"},
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_top_level_help_lists_commands_and_boundaries():
    result = cli("--help")
    assert result.returncode == 0, result.stderr
    for command in COMMANDS:
        assert command in result.stdout, command
    assert "未校准" in result.stdout
    assert "未核验" in result.stdout
    assert "不自动外发" in result.stdout


def test_every_command_has_usage_help():
    for command in COMMANDS:
        result = cli(command, "--help")
        assert result.returncode == 0, command
        assert "usage:" in result.stdout, command
