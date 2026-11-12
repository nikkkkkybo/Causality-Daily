"""Self-contained visual brief: a two-layer page (summary + detail anchors).

Layer 1 (简报) answers "值不值得看、为什么现在看": source, recency, cross-source
signal, ledger link state, plus a short excerpt extracted from the feed summary.
Layer 2 (详细) answers "原文怎么说、证据与台账状态、我能做什么": full stored
summary, provenance, linked claims/predictions, read/ingest/predict commands.

No external resources, no JavaScript. Every dynamic string is escaped and links
are restricted to http(s). The page is a local artifact: not delivered anywhere.
"""
from __future__ import annotations

import html
import re
import sqlite3
import urllib.parse
from datetime import date, datetime, time, timedelta, timezone

from . import dedup
from .feeds import recommend
from .report import _brief_selection, _linked_claim_info

_BOILERPLATE_PREFIX = re.compile(
    r"^[^，。！？\n]{0,24}?\d{1,2}\s*月\s*\d{1,2}\s*日\s*消息\s*[，,：:]?\s*")
_BOILERPLATE_SUFFIX = re.compile(
    r"(?:欢迎关注|关注我们|微信公众号|更多精彩内容|责任编辑|原标题)[^。！？]{0,60}[。！？]?\s*$")


def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _href(url) -> str:
    if not isinstance(url, str):
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return _esc(dedup.display_url(url) or url)


def _display_link(url) -> str:
    if not _href(url):
        return "无可用链接"
    return dedup.display_url(url) or url


def _excerpt(text, limit: int = 140) -> str:
    if not isinstance(text, str):
        return ""
    collapsed = " ".join(text.split())
    collapsed = _BOILERPLATE_PREFIX.sub("", collapsed)
    collapsed = _BOILERPLATE_SUFFIX.sub("", collapsed).strip()
    if not collapsed:
        return ""
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "…"


def _bars(values: list[tuple[str, int]], color: str = "#2563eb") -> str:
    if not values:
        return '<p class="muted">（无数据）</p>'
    width, row_height, label_width = 460, 26, 140
    maximum = max((count for _, count in values), default=0) or 1
    height = row_height * len(values) + 6
    parts = [f'<svg class="bars" viewBox="0 0 {width} {height}" role="img">']
    for index, (label, count) in enumerate(values):
        y = index * row_height + 3
        bar = max(2, int((width - label_width - 56) * count / maximum))
        parts.append(
            f'<text x="0" y="{y + 14}" class="bar-label">{_esc(label[:16])}</text>'
            f'<rect x="{label_width}" y="{y + 2}" width="{bar}" height="14" rx="4" fill="{color}"/>'
            f'<text x="{label_width + bar + 8}" y="{y + 14}" class="bar-value">{count}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _daily_counts(conn: sqlite3.Connection, days: int = 7) -> list[tuple[str, int]]:
    now = datetime.now(timezone.utc)
    result = []
    for offset in range(days - 1, -1, -1):
        day = (now - timedelta(days=offset)).date()
        count = conn.execute(
            "SELECT COUNT(*) FROM feed_items WHERE substr(fetched_at,1,10)=?", (day.isoformat(),)
        ).fetchone()[0]
        result.append((day.isoformat()[5:], count))
    return result


def _source_counts(conn: sqlite3.Connection, limit: int = 8) -> list[tuple[str, int]]:
    rows = conn.execute(
        "SELECT s.name, COUNT(i.id) AS n FROM subscriptions s"
        " LEFT JOIN feed_items i ON i.feed_id=s.id"
        " GROUP BY s.id ORDER BY n DESC, s.id LIMIT ?", (limit,)
    ).fetchall()
    return [(row["name"], row["n"]) for row in rows]


def _row_dict(row) -> dict:
    return {key: row[key] for key in row.keys()}


def _linked_claims(conn: sqlite3.Connection, document_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT c.id, c.text, c.credibility, c.layer, c.hard_signal FROM claims c"
        " WHERE c.id IN (SELECT claim_id FROM chains WHERE document_id=?)"
        " ORDER BY c.credibility DESC, c.id", (document_id,)
    ).fetchall()
    claims = []
    for row in rows:
        claim = _row_dict(row)
        claim["predictions"] = [
            _row_dict(pred) for pred in conn.execute(
                "SELECT id, text, due_date, status, brier, p FROM predictions"
                " WHERE claim_id=? ORDER BY due_date", (row["id"],)
            ).fetchall()
        ]
        claims.append(claim)
    return claims


def _badge(text: str, cls: str = "") -> str:
    return f'<span class="badge {cls}">{_esc(text)}</span>'


def render_brief_html(conn: sqlite3.Connection, days: int = 2, limit: int = 10,
                      per_source: int = 2, topics=None, collection: list[dict] | None = None,
                      no_fetch: bool = False, today: str | None = None,
                      personalization: dict | None = None) -> str:
    """Render the two-layer visual brief (summary cards + in-page detail sections)."""
    from . import personalize
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError("days 必须是 1 到 365 的整数")
    today = today or date.today().isoformat()
    day = date.fromisoformat(today)
    since = datetime.combine(day, time.min).astimezone(timezone.utc).isoformat()
    topics = list(topics) if topics else []
    items = recommend(conn, days=days, limit=max(limit, 20), per_source=max(per_source, 2),
                      topics=topics or None)
    mode = "general"
    if personalization:
        mode = personalization.get("mode", "general")
        if mode != "general":
            items = personalize.rank_items(items, personalization["features"], mode)
    shown = _brief_selection(items, max_items=4)
    pool = recommend(conn, days=days, limit=100, per_source=100, topics=topics or None)

    collection = collection or []
    success = sum(1 for result in collection if not result.get("error"))
    failures = len(collection) - success
    added = sum(int(result.get("added") or 0) for result in collection)
    if no_fetch or not collection:
        collect_text = "未联网采集（仅使用本地已有条目）"
    else:
        collect_text = f"{success} 源成功 / {failures} 源失败 · 本次新增 {added} 条"

    due_rows = conn.execute(
        "SELECT id, due_date, text FROM predictions"
        " WHERE status='pending' AND due_date<=? ORDER BY due_date LIMIT 3", (today,)
    ).fetchall()
    due_count = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE status='pending' AND due_date<=?", (today,)
    ).fetchone()[0]
    pending_count = conn.execute("SELECT COUNT(*) FROM predictions WHERE status='pending'").fetchone()[0]
    new_claims = conn.execute(
        "SELECT COUNT(*) FROM claims WHERE created_at>=?", (since,)
    ).fetchone()[0]
    claim_total = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    ingested_total = conn.execute("SELECT COUNT(*) FROM feed_ingests").fetchone()[0]
    digest_total = conn.execute("SELECT COUNT(*) FROM digests").fetchone()[0]
    now = datetime.now(timezone.utc)
    unread_count = conn.execute(
        "SELECT COUNT(*) FROM feed_items i JOIN subscriptions s ON s.id=i.feed_id"
        " WHERE s.enabled=1 AND i.read_at IS NULL"
        " AND julianday(COALESCE(i.published_at,i.fetched_at)) BETWEEN julianday(?) AND julianday(?)",
        ((now - timedelta(days=days)).isoformat(), now.isoformat()),
    ).fetchone()[0]

    cards, details = [], []
    unlinked_shown: list[dict] = []
    for index, row in enumerate(shown, 1):
        target_url = dedup.normalize_url(row["url"])
        similar = sum(1 for other in pool
                      if (target_url is not None and dedup.normalize_url(other["url"]) == target_url)
                      or dedup.titles_similar(row["title"] or "", other["title"] or ""))
        same_story = "同题1源" if similar <= 1 else f"同题 {similar} 源"
        when = (row["published_at"] or row["fetched_at"] or "")[:16]
        document_id, claim_count, pending = _linked_claim_info(conn, row["id"])
        if document_id is None:
            state_text, state_cls = "未接账", "warn"
            unlinked_shown.append(row)
        else:
            state_text, state_cls = f"已接账 · 主张 {claim_count} / 待结算 {pending}", "ok"
        external = _href(row["url"])
        detail_anchor = f"#d-{row['id']}"
        badges = "".join([
            _badge(row["feed_name"], "source"),
            _badge(same_story),
            _badge(state_text, state_cls),
            _badge("未核验", "muted"),
        ])
        excerpt = _excerpt(row["summary"])
        personal_html = ""
        if row.get("personal_why"):
            personal_html += f'<p class="personal">与你有关：{_esc(row["personal_why"])}</p>'
        if row.get("personal_segment"):
            personal_html += f'<p class="personal">你在哪一段：{_esc(row["personal_segment"])}</p>'
        cards.append(f"""
<article class="card">
  <div class="badges">{badges}</div>
  <h3><a class="detail-link" href="{detail_anchor}">{_esc(row["title"])}</a></h3>
  <p class="excerpt">{_esc(excerpt) or "（该条目没有摘要，需点开原文查看）"}</p>
  {personal_html}
  <div class="card-foot">
    <span class="time">{_esc(when)}</span>
    <span class="links">
      <a class="jump" href="{detail_anchor}">详情 ↓</a>
      {f'<a class="external" href="{external}" target="_blank" rel="noopener noreferrer">原文 ↗</a>' if external else '<span class="muted">无原文链接</span>'}
    </span>
  </div>
</article>""")

        provenance = [
            ("订阅来源", row["feed_name"]),
            ("条目编号", f"I#{row['id']}"),
            ("源称发布时间", row["published_at"] or "发布时间未知"),
            ("首次采集", row["fetched_at"]),
            ("同题源数", f"{similar}（启发式，仅提示）"),
            ("原文链接", _display_link(row["url"])),
        ]
        prov_rows = "".join(
            f"<dt>{_esc(key)}</dt><dd>{_esc(value)}</dd>" for key, value in provenance
        )
        if document_id is None:
            ledger_block = (
                '<div class="ledger empty"><p><strong>未接账。</strong>'
                "该条目尚未转成文档/主张，因此没有出处链、分数或预测。"
                "人工确认后可离线接账：</p>"
                f'<code>ingest {row["id"]} --claim "一句话主张" --domain tech</code></div>'
            )
            claim_block = ""
        else:
            claims = _linked_claims(conn, document_id)
            claim_html = []
            for claim in claims:
                preds = "".join(
                    f'<li>P#{p["id"]} {_esc(p["status"])} · 截止 {_esc(p["due_date"])} · '
                    f'{_esc(p["text"])}'
                    + (f' · Brier {p["brier"]:.3f}' if p["brier"] is not None else "")
                    + f'<br><code>settle P#{p["id"]} hit|miss --note 依据</code></li>'
                    for p in claim["predictions"]
                )
                claim_html.append(
                    f'<li>#{claim["id"]} [{claim["credibility"]:.2f}] {_esc(claim["layer"])} · '
                    f'{_esc(claim["text"])}'
                    + (f'<ul class="preds">{preds}</ul>' if preds else "")
                    + "</li>"
                )
            ledger_block = (
                f'<div class="ledger ok"><p><strong>已接账 D#{document_id}</strong>'
                f" · 主张 {claim_count} / 待结算 {pending}（分数未经校准）</p>"
                + (f'<ul class="claims">{"".join(claim_html)}</ul>' if claim_html else "")
                + "</div>"
            )
            claim_block = (
                "".join(f'<code>predict {c["id"]} --text 可观察承诺 --due YYYY-MM-DD '
                        "--hint 核验方法 [--p 0-1]</code>" for c in claims)
            )
        details.append(f"""
<article class="detail" id="d-{row['id']}">
  <header class="detail-head">
    <div class="badges">{badges}</div>
    <h3>{_esc(row["title"])}</h3>
    <p class="meta">{_esc(when)} · {_esc(row["feed_name"])}</p>
  </header>
  <div class="detail-body">
    <section class="content">
      <h4>订阅摘要原文（未核验）</h4>
      <p class="fulltext">{_esc(" ".join((row["summary"] or "").split())) or "（该条目没有摘要）"}</p>
      {f'<p class="personal">与你有关：{_esc(row["personal_why"])}</p>' if row.get("personal_why") else ''}
      {f'<p class="personal">你在哪一段：{_esc(row["personal_segment"])}</p>' if row.get("personal_segment") else ''}
      {ledger_block}
    </section>
    <aside class="evidence">
      <h4>出处与时间</h4>
      <dl class="prov">{prov_rows}</dl>
      <h4>可以做什么</h4>
      <p class="muted small">以下命令只作用于本地账簿；不会自动核实或外发。</p>
      <code>read {row["id"]}</code>
      {claim_block}
    </aside>
  </div>
  <footer class="detail-foot">
    <a class="jump" href="#brief">↑ 返回简报</a>
    {f'<a class="external" href="{external}" target="_blank" rel="noopener noreferrer">打开原文 ↗</a>' if external else ""}
  </footer>
</article>""")

    cards_html = "".join(cards) or '<p class="empty">暂无符合条件的未读条目；可先添加订阅或采集。</p>'
    details_html = "".join(details) or '<p class="empty">暂无可展开的详细条目。</p>'

    alerts = []
    for row in due_rows:
        alerts.append(
            '<div class="alert urgent"><span class="alert-kind">结算到期</span>'
            f'<div><p>P#{row["id"]} 截止 {_esc(row["due_date"])}：{_esc(row["text"])}</p>'
            f'<code>settle P#{row["id"]} hit|miss --note 依据</code></div></div>'
        )
    if unlinked_shown:
        alerts.append(
            '<div class="alert"><span class="alert-kind">建议接账</span>'
            f'<div><p>推荐条目 I#{unlinked_shown[0]["id"]} 尚未转成主张，可离线接账：</p>'
            f'<code>ingest {unlinked_shown[0]["id"]} --claim "一句话主张"</code></div></div>'
        )
    if not alerts:
        alerts.append(
            '<div class="alert quiet"><span class="alert-kind">无需操作</span>'
            "<div><p>今天没有到期的预测，也没有需要立刻接账的条目；可阅读原文或继续观察。</p>"
            "<p class=\"muted small\">登记预测：<code>predict 主张ID --text 可观察承诺 "
            "--due YYYY-MM-DD --hint 核验方法 [--p 0-1]</code></p></div></div>"
        )
    alerts_html = "".join(alerts)

    linked_shown = sum(1 for row in shown if _linked_claim_info(conn, row["id"])[0] is not None)
    if shown:
        source_names = "、".join(dict.fromkeys(row["feed_name"] for row in shown))
        today_line = f"今天 {len(shown)} 条值得看：来自 {source_names}；其中 {linked_shown} 条已接账。"
        if due_count:
            today_line += f" 另有 {due_count} 条预测到期，等你裁决。"
        if new_claims:
            today_line += f" 今日新增主张 {new_claims} 条。"
    else:
        today_line = "今天暂无符合条件的未读条目。"

    trend = _daily_counts(conn, days=7)
    sources = _source_counts(conn)
    tiles = [
        ("未读 · %d 天窗口" % days, unread_count, ""),
        ("已接账条目", ingested_total, ""),
        ("主张", claim_total, ""),
        ("待结算预测", pending_count, "warn" if pending_count else ""),
        ("其中到期/逾期", due_count, "danger" if due_count else ""),
    ]
    tiles_html = "".join(
        f'<div class="tile {cls}"><span class="num">{value}</span><span class="label">{label}</span></div>'
        for label, value, cls in tiles
    )
    topic_line = "关注主题：" + ("、".join(topics) if topics else "未设置（使用全部订阅）")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>因果簿简报 {_esc(today)}</title>
<style>
:root {{ color-scheme: light; --ink:#111827; --muted:#6b7280; --line:#e5e7eb;
  --paper:#f7f8fa; --card:#ffffff; --accent:#1d4ed8; --accent-soft:#eff6ff;
  --ok:#047857; --ok-soft:#ecfdf5; --warn:#b45309; --warn-soft:#fffbeb; --danger:#b91c1c; }}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{ margin:0; background:var(--paper); color:var(--ink);
  font: 15px/1.65 "Segoe UI","Microsoft YaHei",system-ui,sans-serif; }}
.page {{ max-width: 1120px; margin: 0 auto; padding: 34px 24px 64px; }}
.masthead {{ border-bottom: 1px solid var(--line); padding-bottom: 18px; margin-bottom: 22px; }}
.brand {{ font-size: 12px; letter-spacing: .18em; color: var(--accent); text-transform: uppercase; }}
h1 {{ margin: 6px 0 4px; font: 700 30px/1.25 "Noto Serif SC","Songti SC",Georgia,serif; }}
.lede {{ margin: 0; color: var(--muted); }}
.today {{ margin: 10px 0 0; font-size: 15.5px; color: #1f2937; }}
.jumpnav {{ position: sticky; top: 0; z-index: 5; margin-top: 14px; display: flex; gap: 16px; flex-wrap: wrap;
  background: rgba(247,248,250,.94); padding: 8px 0; }}
.jumpnav a {{ color: var(--accent); text-decoration: none; font-size: 13px; }}
.jumpnav a:hover {{ text-decoration: underline; }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(134px,1fr)); gap: 10px; margin: 22px 0 30px; }}
.tile {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; }}
.tile .num {{ display:block; font-size: 25px; font-weight: 700; line-height:1.2; }}
.tile .label {{ font-size: 12px; color: var(--muted); }}
.tile.warn .num {{ color: var(--warn); }}
.tile.danger .num {{ color: var(--danger); }}
section {{ margin: 34px 0; }}
h2 {{ margin: 0 0 14px; font: 700 19px/1.3 "Noto Serif SC","Songti SC",Georgia,serif; }}
h2 .hint {{ font: 400 12px/1.4 "Segoe UI",sans-serif; color: var(--muted); margin-left: 8px; }}
.alerts {{ display: grid; gap: 10px; }}
.alert {{ display:flex; gap: 14px; background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; }}
.alert.urgent {{ border-left: 4px solid var(--danger); }}
.alert-kind {{ flex: 0 0 auto; font-size: 12px; font-weight: 700; color: var(--accent); background: var(--accent-soft); border-radius: 999px; padding: 3px 10px; height: fit-content; }}
.alert.urgent .alert-kind {{ color: var(--danger); background: #fef2f2; }}
.alert p {{ margin: 0 0 4px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(430px,1fr)); gap: 14px; }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; display:flex; flex-direction:column; gap: 8px; box-shadow: 0 1px 2px rgba(17,24,39,.03); }}
.badges {{ display:flex; flex-wrap:wrap; gap:6px; }}
.badge {{ font-size: 11px; padding: 2px 9px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); background: #fafafa; }}
.badge.source {{ color: var(--accent); border-color: #dbeafe; background: var(--accent-soft); }}
.badge.ok {{ color: var(--ok); border-color: #d1fae5; background: var(--ok-soft); }}
.badge.warn {{ color: var(--warn); border-color: #fde68a; background: var(--warn-soft); }}
.card h3 {{ margin: 0; font-size: 16px; line-height: 1.45; font-weight: 650; }}
.card h3 a {{ color: var(--ink); text-decoration: none; }}
.card h3 a:hover {{ color: var(--accent); }}
.excerpt {{ margin: 0; color: #374151; font-size: 13.5px; }}
.personal {{ margin: 0; padding: 8px 10px; border-left: 3px solid var(--accent);
  background: #f8f6f0; color: #4a463e; font-size: 12.5px; border-radius: 0 6px 6px 0; }}
.card-foot {{ margin-top: auto; display:flex; justify-content: space-between; align-items:center; gap: 10px; padding-top: 8px; border-top: 1px dashed var(--line); }}
.time {{ font-size: 12px; color: var(--muted); }}
.links {{ display:flex; gap: 12px; }}
.jump, .external {{ font-size: 12.5px; color: var(--accent); text-decoration: none; }}
.external {{ color: var(--muted); }}
.jump:hover, .external:hover {{ text-decoration: underline; }}
.panels {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
.panel {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; }}
svg.bars {{ width: 100%; height: auto; }}
.bar-label {{ fill: #6b7280; font-size: 12px; }}
.bar-value {{ fill: #111827; font-size: 12px; }}
.detail {{ background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 20px 22px; margin: 16px 0; scroll-margin-top: 18px; }}
.detail-head h3 {{ margin: 6px 0 2px; font: 700 21px/1.4 "Noto Serif SC","Songti SC",Georgia,serif; }}
.meta {{ margin: 0; color: var(--muted); font-size: 12.5px; }}
.detail-body {{ display: grid; grid-template-columns: minmax(0,1.6fr) minmax(260px,1fr); gap: 22px; margin-top: 14px; }}
h4 {{ margin: 0 0 8px; font-size: 13px; letter-spacing: .04em; color: var(--muted); text-transform: uppercase; }}
.fulltext {{ margin: 0 0 14px; font-size: 14.5px; color: #1f2937; }}
.ledger {{ border-radius: 10px; padding: 12px 14px; font-size: 13.5px; }}
.ledger.empty {{ background: var(--warn-soft); border: 1px solid #fde68a; }}
.ledger.ok {{ background: var(--ok-soft); border: 1px solid #d1fae5; }}
.ledger p {{ margin: 0 0 8px; }}
.claims, .preds {{ margin: 6px 0 0; padding-left: 18px; }}
.claims li {{ margin: 6px 0; }}
.preds li {{ margin: 4px 0; color: #374151; }}
.prov {{ display: grid; grid-template-columns: 92px 1fr; gap: 4px 10px; margin: 0 0 14px; font-size: 12.5px; }}
.prov dt {{ color: var(--muted); }}
.prov dd {{ margin: 0; word-break: break-all; }}
code {{ display: inline-block; background: #f3f4f6; border: 1px solid var(--line); border-radius: 6px; padding: 2px 7px; font-size: 12px; color: #1f2937; margin: 4px 6px 0 0; word-break: break-all; }}
.small {{ font-size: 12px; }}
.muted {{ color: var(--muted); }}
.empty {{ background: var(--card); border: 1px dashed var(--line); border-radius: 12px; padding: 18px; color: var(--muted); }}
.detail-foot {{ display:flex; justify-content: space-between; border-top: 1px dashed var(--line); margin-top: 16px; padding-top: 10px; }}
.boundary {{ margin-top: 40px; border-top: 1px solid var(--line); padding-top: 16px; color: var(--muted); font-size: 12.5px; }}
@media (max-width: 820px) {{ .panels, .detail-body {{ grid-template-columns: 1fr; }} }}
@media print {{ body {{ background:#fff; }} .jumpnav, .card-foot .links, .detail-foot, .alerts {{ display:none; }} .page {{ max-width:none; padding:0; }} }}
</style>
</head>
<body>
<div class="page">
  <header class="masthead">
    <div class="brand">causal_ledger · daily brief</div>
    <h1>因果簿 · 今日简报</h1>
    <p class="lede">{_esc(today)} · {_esc(collect_text)} · {_esc(topic_line)}</p>
    <p class="today">{_esc(today_line)}</p>
    <nav class="jumpnav">
      <a href="#actions">待办</a><a href="#brief">值得注意</a><a href="#stats">来源与趋势</a><a href="#details">详细内容</a>
    </nav>
  </header>

  <div class="kpis">{tiles_html}</div>

  <section id="actions">
    <h2>待你处理 <span class="hint">人工操作，系统不会自动核实或外发</span></h2>
    <div class="alerts">{alerts_html}</div>
  </section>

  <section id="brief">
    <h2>值得注意 <span class="hint">{len(shown)} 条 · 未核验 · 同题源数为启发式提示</span></h2>
    <div class="cards">{cards_html}</div>
  </section>

  <section id="stats">
    <h2>来源与趋势</h2>
    <div class="panels">
      <div class="panel"><h4>近 7 天采集</h4>{_bars(trend)}</div>
      <div class="panel"><h4>来源条目分布</h4>{_bars(sources, "#047857")}</div>
    </div>
  </section>

  <section id="details">
    <h2>详细内容 <span class="hint">完整摘要、出处与台账状态；可返回简报</span></h2>
    {details_html}
  </section>

  <footer class="boundary">
    实验分未经校准，不是真实概率；订阅条目、人工接账、预测与推荐结果均未核验；
    同题源数来自链接/标题相似启发式，仅提示不合并；数据只存本地，不自动外发、不安装定时任务。
    本地已保留 {digest_total} 期推送记录（`digests` 可查）。
  </footer>
</div>
</body>
</html>
"""
