"""Single-purpose local app: read 今日事报, browse archive, submit end-of-report notes.

Deliberately tiny and local-first:
- static report pages live in a reports directory (one `YYYY-MM-DD.html` per day);
- the server injects a small end-of-report note form (no external assets/JS libs);
- notes are stored in SQLite as raw text for the next generation run to use as a
  silent prior (not a command);
- default bind is loopback; LAN exposure requires an explicit host and a token.

The app is a reader/recorder only. It does not run the ledger, does not verify
anything and does not push messages.
"""
from __future__ import annotations

import json
import re
import socket
import sqlite3
import threading
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .db import atomic, connect, now_iso

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_BODY_BYTES = 64 * 1024
MAX_NOTE_LENGTH = 4000
NOTE_FORM = """
<section id="daily-note" style="margin:56px 0 8px;padding:22px;border-top:1px solid #d8d0c0;">
  <p style="font-size:12px;letter-spacing:.22em;color:#9e3b2e;margin:0 0 8px;">留给你 · 本日备注</p>
  <p style="font-size:13px;color:#8a8376;margin:0 0 12px;">
    写给你想让我记住的角度；可留空。只作为下一次取舍的潜意识，不是命令，也不会出现在事报正文里。
  </p>
  <textarea id="note-text" rows="4" maxlength="4000" placeholder="今天想让我留意的是……"
    style="width:100%;box-sizing:border-box;background:#fbf8f2;border:1px solid #d8d0c0;
    border-radius:8px;padding:12px;color:#1b1a17;font:15px/1.7 'Songti SC','Noto Serif SC',serif;"></textarea>
  <div style="margin-top:10px;display:flex;align-items:center;gap:12px;">
    <button id="note-submit" style="background:#9e3b2e;color:#fbf8f2;border:0;border-radius:999px;
      padding:7px 20px;font-size:13px;letter-spacing:.08em;cursor:pointer;">提交备注</button>
    <span id="note-status" style="font-size:12px;color:#8a8376;"></span>
  </div>
</section>
<script>
(function () {
  var day = "__DAY__";
  var token = "__TOKEN__";
  var box = document.getElementById("note-text");
  var status = document.getElementById("note-status");
  document.getElementById("note-submit").addEventListener("click", function () {
    var text = box.value.trim();
    if (!text) { status.textContent = "留空即可，无需提交。"; return; }
    status.textContent = "提交中…";
    fetch("/notes", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Token": token},
      body: JSON.stringify({day: day, text: text})
    }).then(function (resp) {
      if (!resp.ok) { throw new Error("HTTP " + resp.status); }
      return resp.json();
    }).then(function () {
      status.textContent = "已记录，会进入下一次的潜意识。";
      box.value = "";
    }).catch(function () {
      status.textContent = "提交失败，请稍后再试。";
    });
  });
})();
</script>
"""


def _html_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def record_note(conn: sqlite3.Connection, day: str, text: str) -> int:
    if not isinstance(day, str) or not DATE_RE.fullmatch(day):
        raise ValueError("day 必须是 YYYY-MM-DD")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise ValueError("day 必须是有效日期") from None
    if not isinstance(text, str) or not text.strip():
        raise ValueError("备注不能为空；不想写就留空，不必提交")
    text = text.strip()
    if len(text) > MAX_NOTE_LENGTH:
        raise ValueError(f"备注不能超过 {MAX_NOTE_LENGTH} 个字符")
    with atomic(conn):
        cursor = conn.execute(
            "INSERT INTO notes(day, text, created_at) VALUES (?,?,?)",
            (day, text, now_iso()),
        )
    return cursor.lastrowid


def list_notes(conn: sqlite3.Connection, day: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit 必须是 1 到 100 的整数")
    if day is not None:
        if not DATE_RE.fullmatch(day):
            raise ValueError("day 必须是 YYYY-MM-DD")
        return conn.execute(
            "SELECT * FROM notes WHERE day=? ORDER BY id DESC LIMIT ?", (day, limit)
        ).fetchall()
    return conn.execute("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def _reports(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    names = [path.stem for path in directory.glob("*.html")
             if DATE_RE.fullmatch(path.stem)]
    return sorted(names, reverse=True)


def render_index(directory: Path, notes_count: int = 0) -> str:
    days = _reports(directory)
    items = "".join(
        f'<li><a href="/{day}">{day}</a><span>{_html_escape("今日事报")}</span></li>'
        for day in days
    ) or '<li class="empty">还没有事报。先运行每日生成，把 <code>YYYY-MM-DD.html</code> 放进 reports 目录。</li>'
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>今日事报</title>
<style>
:root {{ color-scheme: light; }}
body {{ margin:0; background:#f6f2ea; color:#1b1a17;
  font:16px/1.7 "Songti SC","Noto Serif SC",Georgia,serif; }}
.page {{ max-width:760px; margin:0 auto; padding:56px 24px 64px; }}
h1 {{ font-size:40px; margin:0 0 4px; letter-spacing:.08em; }}
.kicker {{ font-size:12px; letter-spacing:.28em; color:#9e3b2e; margin-bottom:14px; }}
p {{ color:#6f685c; }}
ul {{ list-style:none; margin:28px 0 0; padding:0; border-top:1px solid #d8d0c0; }}
li {{ border-bottom:1px solid #d8d0c0; padding:14px 2px; display:flex; justify-content:space-between; }}
li a {{ color:#1b1a17; text-decoration:none; font-size:19px; letter-spacing:.04em; }}
li a:hover {{ color:#9e3b2e; }}
li span {{ color:#8a8376; font-size:12px; }}
.empty {{ color:#8a8376; font-size:14px; }}
.foot {{ margin-top:36px; color:#8a8376; font-size:12px; }}
</style></head><body><div class="page">
<div class="kicker">今日事报 · 本地应用</div>
<h1>今日事报</h1>
<p>只做一件事：每天读一份因果推送，并在末尾留下你的偏重。备注 {notes_count} 条。</p>
<ul>{items}</ul>
<p class="foot">本地服务 · 数据留在本机 · 不自动外发</p>
</div></body></html>
"""


def inject_note_form(page: str, day: str, token: str) -> str:
    snippet = NOTE_FORM.replace("__DAY__", _html_escape(day)).replace(
        "__TOKEN__", _html_escape(token or ""))
    if "</body>" in page:
        return page.replace("</body>", snippet + "</body>", 1)
    return page + snippet


class _Handler(BaseHTTPRequestHandler):
    server_version = "DailyApp/0.1"

    def log_message(self, *args):  # keep the terminal quiet
        return

    def _token_ok(self) -> bool:
        expected = self.server.token
        if not expected:
            return True
        supplied = self.headers.get("X-Token", "")
        if not supplied:
            query = parse_qs(urlsplit(self.path).query)
            supplied = (query.get("token") or [""])[0]
        return supplied == expected

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        if not self._token_ok():
            self._json(401, {"error": "需要访问口令"})
            return
        path = urlsplit(self.path).path
        directory: Path = self.server.reports
        if path in ("/", "/index.html"):
            conn = connect(self.server.db_path)
            try:
                notes_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            finally:
                conn.close()
            self._send(200, render_index(directory, notes_count).encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        match = re.fullmatch(r"/(\d{4}-\d{2}-\d{2})(?:\.html)?", path)
        if not match:
            self._json(404, {"error": "not found"})
            return
        day = match.group(1)
        report = directory / f"{day}.html"
        if not report.is_file():
            self._json(404, {"error": "事报不存在"})
            return
        page = report.read_text(encoding="utf-8")
        self._send(200, inject_note_form(page, day, self.server.token).encode("utf-8"),
                   "text/html; charset=utf-8")

    def do_POST(self):
        if not self._token_ok():
            self._json(401, {"error": "需要访问口令"})
            return
        if urlsplit(self.path).path != "/notes":
            self._json(404, {"error": "not found"})
            return
        length = self.headers.get("Content-Length")
        try:
            length = int(length or 0)
        except ValueError:
            self._json(400, {"error": "invalid Content-Length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._json(413, {"error": "备注请求过大或为空"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
            day = payload["day"]
            text = payload["text"]
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self._json(400, {"error": "请求必须是 {day, text} JSON"})
            return
        conn = connect(self.server.db_path)
        try:
            note_id = record_note(conn, day, text)
        except ValueError as error:
            self._json(400, {"error": str(error)})
            return
        finally:
            conn.close()
        self._json(200, {"ok": True, "id": note_id})


class DailyAppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, reports: Path, db_path: str, token: str = ""):
        super().__init__(address, _Handler)
        self.reports = Path(reports)
        self.db_path = str(db_path)
        self.token = token or ""

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        if host in ("0.0.0.0", "::"):
            host = _lan_ip()
        return f"http://{host}:{port}/"


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def serve(reports, host: str = "127.0.0.1", port: int = 8765,
          token: str = "", db_path: str = "app.sqlite3", on_ready=None) -> None:
    """Run the blocking local app. `on_ready(server)` can print the URL."""
    connect(db_path).close()  # ensure schema exists before serving
    server = DailyAppServer((host, port), Path(reports), db_path, token)
    if on_ready is not None:
        on_ready(server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
