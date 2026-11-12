"""Offline regressions for the single-purpose local app (reader + end-note submission)."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from causal_ledger import app as app_module
from causal_ledger.db import connect

REPORT = "<!DOCTYPE html><html><head><title>今日事报</title></head><body><h1>今日事报</h1></body></html>"


def start(tmp_path, token: str = ""):
    reports = tmp_path / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "2026-11-12.html").write_text(REPORT, encoding="utf-8")
    db = tmp_path / "app.sqlite3"
    server = app_module.DailyAppServer(("127.0.0.1", 0), reports, str(db), token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}", db


def stop(server):
    server.shutdown()
    server.server_close()


def get(url, token: str = ""):
    headers = {"X-Token": token} if token else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read().decode("utf-8")


def post(url, payload, token: str = "", raw: bytes | None = None):
    body = raw if raw is not None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Token"] = token
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_index_and_report_inject_end_of_report_note_form(tmp_path):
    server, base, _ = start(tmp_path)
    try:
        status, index = get(base + "/")
        assert status == 200
        assert "今日事报" in index
        assert 'href="/2026-11-12"' in index
        status, page = get(base + "/2026-11-12")
        assert status == 200
        assert "今日事报" in page
        assert "本日备注" in page and "note-submit" in page
        assert '"/notes"' in page
        assert page.rstrip().endswith("</html>")
    finally:
        stop(server)


def test_post_note_persists_and_rejects_empty(tmp_path):
    server, base, db = start(tmp_path)
    try:
        status, payload = post(base + "/notes", {"day": "2026-11-12", "text": "今天多留意记忆外化"})
        assert status == 200 and payload["ok"] is True
        conn = connect(db)
        try:
            rows = app_module.list_notes(conn)
            assert len(rows) == 1
            assert rows[0]["day"] == "2026-11-12"
            assert rows[0]["text"] == "今天多留意记忆外化"
        finally:
            conn.close()
        with pytest.raises(urllib.error.HTTPError) as caught:
            post(base + "/notes", {"day": "2026-11-12", "text": "   "})
        assert caught.value.code == 400
    finally:
        stop(server)


def test_security_rejects_bad_paths_and_oversized_body(tmp_path):
    server, base, _ = start(tmp_path)
    try:
        for path in ("/../etc/passwd", "/2026-11-13", "/not-a-date"):
            with pytest.raises(urllib.error.HTTPError) as caught:
                get(base + path)
            assert caught.value.code == 404
        try:
            post(base + "/notes", None, raw=b"{" + b"x" * (70 * 1024) + b"}")
        except urllib.error.HTTPError as error:
            assert error.code == 413
        except OSError:
            # The server may reject with 413 and close without draining the body;
            # a reset here still means the oversized request was refused.
            pass
        with pytest.raises(urllib.error.HTTPError) as caught:
            post(base + "/notes", None, raw=b"not json")
        assert caught.value.code == 400
    finally:
        stop(server)


def test_token_is_required_for_read_and_write(tmp_path):
    server, base, _ = start(tmp_path, token="secret")
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            get(base + "/")
        assert caught.value.code == 401
        status, _ = get(base + "/", token="secret")
        assert status == 200
        status, _ = post(base + "/notes?token=secret",
                         {"day": "2026-11-12", "text": "口令测试"})
        assert status == 200
        with pytest.raises(urllib.error.HTTPError) as caught:
            post(base + "/notes", {"day": "2026-11-12", "text": "无口令"})
        assert caught.value.code == 401
    finally:
        stop(server)


def test_note_validation():
    conn = connect(":memory:")
    try:
        with pytest.raises(ValueError):
            app_module.record_note(conn, "2026-13-01", "x")
        with pytest.raises(ValueError):
            app_module.record_note(conn, "2026-11-12", "")
        with pytest.raises(ValueError):
            app_module.record_note(conn, "2026-11-12", "x" * 4001)
        note_id = app_module.record_note(conn, "2026-11-12", "  正常备注  ")
        assert note_id > 0
        rows = app_module.list_notes(conn, day="2026-11-12")
        assert rows[0]["text"] == "正常备注"
        with pytest.raises(ValueError):
            app_module.list_notes(conn, limit=0)
    finally:
        conn.close()
