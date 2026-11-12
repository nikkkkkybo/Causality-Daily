"""Zero-cost cloud publish: feed config, static report, index and ServerChan push."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from causal_ledger import cloud

ROOT = Path(__file__).resolve().parents[1]


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "causal_ledger", *args],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        env={**{key: value for key, value in os.environ.items()
                if key not in ("CL_LLM_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY")},
             "PYTHONIOENCODING": "utf-8"}, timeout=30,
    )


def test_load_feeds_validation(tmp_path):
    good = tmp_path / "feeds.json"
    good.write_text(json.dumps({"feeds": [{"name": "A", "url": "https://example.org/feed"}]}),
                    encoding="utf-8")
    assert cloud.load_feeds(good)[0]["name"] == "A"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"feeds": [{"name": "A", "url": "file:///etc/passwd"}]}),
                   encoding="utf-8")
    with pytest.raises(ValueError):
        cloud.load_feeds(bad)
    duplicate = tmp_path / "dup.json"
    duplicate.write_text(json.dumps({"feeds": [
        {"name": "A", "url": "https://example.org/feed"},
        {"name": "B", "url": "https://example.org/feed/"},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError):
        cloud.load_feeds(duplicate)
    with pytest.raises(ValueError):
        cloud.load_feeds(tmp_path / "missing.json")


def test_build_report_writes_static_files(tmp_path):
    config = tmp_path / "feeds.json"
    config.write_text(json.dumps({"feeds": [{"name": "A", "url": "https://example.org/feed"}]}),
                      encoding="utf-8")
    reports = tmp_path / "reports"
    result = cloud.build_report(config, reports, no_fetch=True, today="2026-11-13")
    assert Path(result["report"]).is_file()
    assert Path(result["index"]).is_file()
    assert Path(result["data"]).is_file()
    assert (reports / "latest.html").is_file()
    text = Path(result["report"]).read_text(encoding="utf-8")
    assert "今日事报" in text
    assert "台账状态" not in text and "待你处理" not in text
    index = Path(result["index"]).read_text(encoding="utf-8")
    assert 'href="latest.html"' in index
    assert 'href="2026-11-13.html"' in index


def test_serverchan_push_uses_key_and_never_logs_it(tmp_path):
    seen = {}

    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False

    def opener(request, timeout=15):
        seen["url"] = request.full_url
        seen["data"] = request.data
        seen["timeout"] = timeout
        return FakeResponse()

    result = cloud.send_serverchan("SECRET_KEY", "今日事报 2026-11-13", "正文", opener=opener)
    assert result["sent"] is True and result["status"] == 200
    assert "SECRET_KEY.send" in seen["url"]
    assert "title=" in seen["data"].decode("utf-8")
    skipped = cloud.send_serverchan("", "标题", "正文", opener=opener)
    assert skipped["sent"] is False


def test_push_body_contains_link():
    title, body = cloud.push_body("2026-11-13", "https://example.github.io/repo/")
    assert "2026-11-13" in title
    assert "https://example.github.io/repo/2026-11-13.html" in body
    _, no_link = cloud.push_body("2026-11-13", "")
    assert "未配置 PAGES_BASE_URL" in no_link


def _seed_week(reports: Path):
    data = reports / "data"
    data.mkdir(parents=True, exist_ok=True)
    for index, day in enumerate(("2026-11-02", "2026-11-03", "2026-11-04",
                                 "2026-11-05", "2026-11-06", "2026-11-07", "2026-11-08")):
        items = [
            {"id": index * 10 + 1, "title": f"AI 模型工具 {day}", "summary": "模型与软件",
             "feed_name": "源A", "published_at": day + "T01:00:00+00:00",
             "url": f"https://example.org/a{index}"},
            {"id": index * 10 + 2, "title": f"价格与招聘 {day}", "summary": "价格与招聘",
             "feed_name": "源B", "published_at": day + "T02:00:00+00:00",
             "url": f"https://example.org/b{index}"},
        ]
        (data / f"{day}.json").write_text(
            json.dumps({"day": day, "items": items}, ensure_ascii=False), encoding="utf-8")
        (reports / f"{day}.html").write_text("<html>daily</html>", encoding="utf-8")


def test_build_weekly_rolls_up_and_removes_dailies(tmp_path):
    reports = tmp_path / "reports"
    _seed_week(reports)
    result = cloud.build_weekly(reports, end_day="2026-11-08")
    weekly = Path(result["report"])
    assert weekly.is_file()
    assert weekly.name == "week-2026-11-02.html"
    text = weekly.read_text(encoding="utf-8")
    assert "本周事报" in text and "本周信号清单（保留出处）" in text
    assert result["days"] == 7 and result["items"] == 14
    assert len(result["removed"]) == 14
    assert not (reports / "2026-11-08.html").exists()
    assert not (reports / "data" / "2026-11-08.json").exists()
    index = (reports / "index.html").read_text(encoding="utf-8")
    assert "week-2026-11-02.html" in index
    assert "本周事报" in index


def test_build_weekly_without_data_keeps_dailies(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    daily = reports / "2026-11-08.html"
    daily.write_text("daily", encoding="utf-8")
    with pytest.raises(ValueError):
        cloud.build_weekly(reports, end_day="2026-11-08")
    assert daily.is_file()


def test_cli_cloud_weekly_only(tmp_path):
    reports = tmp_path / "reports"
    _seed_week(reports)
    result = cli("cloud", "--reports", str(reports), "--weekly-only",
                 "--no-push", "--week-end", "2026-11-08")
    assert result.returncode == 0, result.stderr
    assert "周报已生成" in result.stdout
    assert (reports / "week-2026-11-02.html").is_file()


def test_cli_cloud_no_fetch_no_push(tmp_path):
    config = tmp_path / "feeds.json"
    config.write_text(json.dumps({"feeds": [{"name": "A", "url": "https://example.org/feed"}]}),
                      encoding="utf-8")
    reports = tmp_path / "reports"
    result = cli("--db", str(tmp_path / "db.sqlite3"), "cloud", "--config", str(config),
                 "--reports", str(reports), "--no-fetch", "--no-push", "--date", "2026-11-13")
    assert result.returncode == 0, result.stderr
    assert "云端报告已生成" in result.stdout
    assert (reports / "2026-11-13.html").is_file()
