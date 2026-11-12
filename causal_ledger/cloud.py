"""Zero-monthly-cost cloud publish: build a static daily report + index + optional push.

Designed for GitHub Actions + GitHub Pages:
- reads a small feed config (no database needed between runs);
- creates a temporary SQLite DB, collects feeds, renders the static HTML brief;
- writes reports/YYYY-MM-DD.html and reports/index.html;
- optionally pushes a short WeChat message through ServerChan (key from env).
No personal profile/vault is read or uploaded by this path.
"""
from __future__ import annotations

import json
import re
import sqlite3
import tempfile
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from .db import connect
from .feeds import add_subscription, collect_feeds
from .html_report import render_brief_html

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_FEEDS = 30
TITLE = "今日事报"


def load_feeds(path) -> list[dict]:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ValueError(f"订阅配置不存在：{path}") from None
    except (OSError, ValueError):
        raise ValueError(f"订阅配置不是有效 JSON：{path}") from None
    if isinstance(raw, dict):
        raw = raw.get("feeds")
    if not isinstance(raw, list) or not raw:
        raise ValueError("订阅配置必须是非空数组，或 {feeds: [...]}")
    if len(raw) > MAX_FEEDS:
        raise ValueError(f"订阅源不能超过 {MAX_FEEDS} 个")
    feeds, seen = [], set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("每个订阅必须是 {name, url}")
        name, url = entry.get("name"), entry.get("url")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("订阅 name 不能为空")
        if not isinstance(url, str):
            raise ValueError(f"订阅 {name} 的 url 必须是字符串")
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"订阅 {name} 的 url 必须是 http(s)")
        key = url.rstrip("/")
        if key in seen:
            raise ValueError(f"订阅 URL 重复：{url}")
        seen.add(key)
        feeds.append({"name": name.strip()[:200], "url": url})
    return feeds


def _index_html(reports_dir: Path, title: str = TITLE) -> str:
    days = sorted((path.stem for path in reports_dir.glob("*.html") if DATE_RE.fullmatch(path.stem)),
                  reverse=True)
    items = "".join(f'<li><a href="{day}.html">{day}</a></li>' for day in days)
    if not items:
        items = '<li class="empty">还没有报告。</li>'
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · 归档</title>
<style>
body {{ margin:0; background:#f6f2ea; color:#1b1a17;
  font:16px/1.7 "Songti SC","Noto Serif SC",Georgia,serif; }}
.page {{ max-width:760px; margin:0 auto; padding:56px 22px; }}
h1 {{ font-size:38px; letter-spacing:.06em; }}
p {{ color:#6f685c; }}
ul {{ list-style:none; padding:0; border-top:1px solid #d8d0c0; }}
li {{ border-bottom:1px solid #d8d0c0; padding:14px 2px; }}
a {{ color:#1b1a17; text-decoration:none; font-size:19px; }}
a:hover {{ color:#9e3b2e; }}
.empty {{ color:#8a8376; font-size:14px; }}
</style></head><body><div class="page">
<h1>{title}</h1>
<p>每日因果推送 · 静态归档 · 未核验</p>
<ul>{items}</ul>
</div></body></html>
"""


def build_report(config_path, reports_dir, *, days: int = 2, limit: int = 10,
                 per_source: int = 2, no_fetch: bool = False,
                 today: str | None = None) -> dict:
    feeds = load_feeds(config_path)
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    day = today or date.today().isoformat()
    if not DATE_RE.fullmatch(day):
        raise ValueError("today 必须是 YYYY-MM-DD")
    with tempfile.TemporaryDirectory() as temporary:
        conn = connect(str(Path(temporary) / "cloud.sqlite3"))
        try:
            for feed in feeds:
                add_subscription(conn, feed["name"], feed["url"])
            results = [] if no_fetch else collect_feeds(conn)
            html = render_brief_html(conn, days=days, limit=limit,
                                     per_source=per_source, collection=results,
                                     no_fetch=no_fetch, today=day)
        finally:
            conn.close()
    report = reports_dir / f"{day}.html"
    report.write_text(html, encoding="utf-8")
    (reports_dir / "index.html").write_text(_index_html(reports_dir), encoding="utf-8")
    failures = [row for row in results if row.get("error")]
    return {"day": day, "report": str(report), "index": str(reports_dir / "index.html"),
            "feeds": len(feeds), "failures": len(failures), "results": results}


def send_serverchan(key: str, title: str, desp: str, *, opener=urllib.request.urlopen) -> dict:
    if not key:
        return {"sent": False, "reason": "未配置 SERVERCHAN_KEY"}
    payload = urllib.parse.urlencode({"title": title[:100], "desp": desp[:30000]}).encode("utf-8")
    request = urllib.request.Request(
        f"https://sctapi.ftqq.com/{key}.send", data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with opener(request, timeout=15) as response:
            status = getattr(response, "status", 200)
            return {"sent": 200 <= status < 300, "status": status}
    except Exception as error:  # noqa: BLE001 - reported, never fatal to report generation
        return {"sent": False, "reason": type(error).__name__}


def push_body(day: str, base_url: str, *, title: str = TITLE) -> tuple[str, str]:
    link = f"{base_url.rstrip('/')}/{day}.html" if base_url else ""
    lines = [f"{title} · {day}", "今日因果推送已生成；条目未核验。"]
    if link:
        lines.append(f"[打开今日事报]({link})")
    else:
        lines.append("（未配置 PAGES_BASE_URL，消息不含链接）")
    return f"{title} {day}", "\n\n".join(lines)
