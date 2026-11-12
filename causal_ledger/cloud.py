"""Zero-monthly-cost cloud publish: build a static 今日事报 + index + optional push.

Designed for GitHub Actions + GitHub Pages:
- reads a small feed config (no database needed between runs);
- creates a temporary SQLite DB, collects feeds, renders the current causal-chain
  "今日事报" page (not the deprecated "因果簿简报" card layout);
- writes reports/YYYY-MM-DD.html and reports/index.html;
- optionally pushes a short WeChat message through ServerChan (key from env).
No personal profile/vault is read or uploaded by this path.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .daily_page import render_daily_page
from .db import connect
from .feeds import add_subscription, collect_feeds, recommend
from .push import send_push, send_serverchan
from .synth import DEFAULT_MODEL, SynthesisError, synthesize
from .weekly_page import render_weekly_page

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_FEEDS = 30
TITLE = "今日事报"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def default_day() -> str:
    """The report date is Beijing's calendar date, not the runner's UTC date."""
    try:
        return datetime.now(LOCAL_TZ).date().isoformat()
    except Exception:  # noqa: BLE001 - fall back on systems without tzdata
        return date.today().isoformat()


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
    weekly = sorted((path.stem for path in reports_dir.glob("week-*.html")
                     if path.stem.startswith("week-")), reverse=True)
    dailies = sorted((path.stem for path in reports_dir.glob("*.html")
                      if DATE_RE.fullmatch(path.stem)), reverse=True)

    def links(names, empty):
        if not names:
            return f'<li class="empty">{empty}</li>'
        return "".join(f'<li><a href="{name}.html">{name}</a></li>' for name in names)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · 归档</title>
<style>
body {{ margin:0; background:#f6f2ea; color:#1b1a17;
  font:16px/1.7 "Songti SC","Noto Serif SC",Georgia,serif; }}
.page {{ max-width:760px; margin:0 auto; padding:56px 22px; }}
h1 {{ font-size:38px; letter-spacing:.06em; }}
h2 {{ font-size:19px; margin:28px 0 6px; letter-spacing:.05em; }}
p {{ color:#6f685c; }}
ul {{ list-style:none; padding:0; border-top:1px solid #d8d0c0; }}
li {{ border-bottom:1px solid #d8d0c0; padding:14px 2px; }}
a {{ color:#1b1a17; text-decoration:none; font-size:19px; }}
a:hover {{ color:#9e3b2e; }}
.empty {{ color:#8a8376; font-size:14px; }}
.muted {{ color:#8a8376; font-size:12px; }}
.foot {{ color:#8a8376; font-size:12px; margin-top:28px; }}
</style></head><body><div class="page">
<h1>{title}</h1>
<p><a href="latest.html">最新一期 →</a>　<span class="muted">这个链接不会随日期变化</span></p>
<p>每周保留《本周事报》；日报在周报产出后归档。条目均未核验。</p>
<h2>本周事报</h2>
<ul>{links(weekly, "还没有周报；周一 07:30 自动汇总。")}</ul>
<h2>日报（周报产出前的临时页）</h2>
<ul>{links(dailies, "暂无日报。")}</ul>
<p class="foot">通线 · 不读个人档案 · 自动发布，不自动核实</p>
</div></body></html>
"""


def _item_record(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "title": item.get("title") or "",
        "summary": (item.get("summary") or "")[:500],
        "feed_name": item.get("feed_name") or "",
        "published_at": item.get("published_at"),
        "fetched_at": item.get("fetched_at"),
        "url": item.get("url"),
    }


def build_report(config_path, reports_dir, *, days: int = 2, limit: int = 30,
                 per_source: int = 2, no_fetch: bool = False,
                 today: str | None = None, use_llm: bool = True,
                 model: str = DEFAULT_MODEL) -> dict:
    feeds = load_feeds(config_path)
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    day = today or default_day()
    if not DATE_RE.fullmatch(day):
        raise ValueError("today 必须是 YYYY-MM-DD")
    with tempfile.TemporaryDirectory() as temporary:
        conn = connect(str(Path(temporary) / "cloud.sqlite3"))
        try:
            for feed in feeds:
                add_subscription(conn, feed["name"], feed["url"])
            results = [] if no_fetch else collect_feeds(conn)
            items = recommend(conn, days=days, limit=limit, per_source=per_source)
            synthesis = None
            synthesis_error = None
            key = os.environ.get("DEEPSEEK_API_KEY", "")
            if use_llm and key and items:
                try:
                    synthesis = synthesize(items, day=day, scope="day", api_key=key,
                                           model=model)
                except SynthesisError as error:
                    synthesis_error = str(error)
                except Exception as error:  # noqa: BLE001 - fallback, never break the report
                    synthesis_error = type(error).__name__
            html = render_daily_page(items, day, synthesis=synthesis)
        finally:
            conn.close()
    report = reports_dir / f"{day}.html"
    report.write_text(html, encoding="utf-8")
    (reports_dir / "latest.html").write_text(html, encoding="utf-8")
    data_dir = reports_dir / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / f"{day}.json").write_text(
        json.dumps({"day": day, "items": [_item_record(item) for item in items]},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    (reports_dir / "index.html").write_text(_index_html(reports_dir), encoding="utf-8")
    failures = [row for row in results if row.get("error")]
    return {"day": day, "report": str(report), "index": str(reports_dir / "index.html"),
            "data": str(data_dir / f"{day}.json"), "html": html,
            "synthesis": synthesis is not None, "synthesis_error": synthesis_error,
            "feeds": len(feeds), "failures": len(failures), "results": results}


def _previous_sunday(today: str | None = None) -> date:
    current = date.fromisoformat(today) if today else date.fromisoformat(default_day())
    return current - timedelta(days=(current.weekday() + 1) % 7)


def build_weekly(reports_dir, *, end_day: str | None = None,
                 use_llm: bool = True, model: str = DEFAULT_MODEL) -> dict:
    """Roll up the previous Mon–Sun daily JSONs, write the weekly page, and remove
    that week's daily pages/data (the weekly page keeps the source list)."""
    reports_dir = Path(reports_dir)
    end = date.fromisoformat(end_day) if end_day else _previous_sunday()
    start = end - timedelta(days=6)
    if start > end:
        raise ValueError("end_day 不能早于开始日")
    data_dir = reports_dir / "data"
    items, seen, source_days = [], set(), []
    for offset in range(7):
        day = (start + timedelta(days=offset)).isoformat()
        path = data_dir / f"{day}.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        source_days.append(day)
        for item in payload.get("items") or []:
            key = item.get("url") or item.get("title")
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    if not items:
        raise ValueError("没有可汇总的日报数据；保留日报，等下周再试")
    synthesis = None
    synthesis_error = None
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if use_llm and key:
        try:
            synthesis = synthesize(items, day=f"{start.isoformat()}~{end.isoformat()}",
                                   scope="week", api_key=key, model=model)
        except SynthesisError as error:
            synthesis_error = str(error)
        except Exception as error:  # noqa: BLE001
            synthesis_error = type(error).__name__
    weekly = reports_dir / f"week-{start.isoformat()}.html"
    weekly.write_text(render_weekly_page(items, start.isoformat(), end.isoformat(),
                                         synthesis=synthesis), encoding="utf-8")
    removed = []
    for offset in range(7):
        day = (start + timedelta(days=offset)).isoformat()
        for path in (reports_dir / f"{day}.html", data_dir / f"{day}.json"):
            if path.is_file():
                path.unlink()
                removed.append(str(path))
    (reports_dir / "index.html").write_text(_index_html(reports_dir), encoding="utf-8")
    return {"week_start": start.isoformat(), "week_end": end.isoformat(),
            "report": str(weekly), "index": str(reports_dir / "index.html"),
            "days": len(source_days), "items": len(items), "removed": removed,
            "synthesis": synthesis is not None, "synthesis_error": synthesis_error}


def send_serverchan(key: str, title: str, desp: str, *, opener=urllib.request.urlopen) -> dict:
    """Compatibility wrapper; the real implementation lives in push.py."""
    from .push import send_serverchan as _send
    return _send(key, title, desp, opener=opener)


def push_body(day: str, base_url: str, *, title: str = TITLE) -> tuple[str, str]:
    link = f"{base_url.rstrip('/')}/{day}.html" if base_url else ""
    lines = [f"{title} · {day}", "今日因果推送已生成；条目未核验。"]
    if link:
        lines.append(f"[打开今日事报]({link})")
    else:
        lines.append("（未配置 PAGES_BASE_URL，消息不含链接）")
    return f"{title} {day}", "\n\n".join(lines)


def push_weekly_body(start: str, end: str, base_url: str) -> tuple[str, str]:
    link = f"{base_url.rstrip('/')}/week-{start}.html" if base_url else ""
    lines = [f"本周事报 · {start} ~ {end}", "本周事报已汇总；日报已归档，周报保留出处。"]
    if link:
        lines.append(f"[打开本周事报]({link})")
    else:
        lines.append("（未配置 PAGES_BASE_URL，消息不含链接）")
    return f"本周事报 {start} ~ {end}", "\n\n".join(lines)
