"""Multi-page visual brief site: index.html + detail/item-<id>.html.

True "brief -> detail" navigation without JavaScript: each summary card links to
a separate local detail page with the extracted content, provenance, ledger state
and actions. All pages inline their own CSS, use no external resources, escape all
dynamic strings and accept only http(s) links.

Optionally the selected items' article text can be fetched (no model) to improve
excerpts; failures fall back to the stored feed summary with an explicit note.
"""
from __future__ import annotations

import html
import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from .feeds import recommend
from . import dedup
from .html_report import (
    _badge,
    _bars,
    _daily_counts,
    _display_link,
    _esc,
    _excerpt,
    _href,
    _linked_claim_info,
    _linked_claims,
    _row_dict,
    _source_counts,
)
from .report import _brief_selection

STYLE = """
:root { color-scheme: light; --ink:#111827; --muted:#6b7280; --line:#e5e7eb;
  --paper:#f7f8fa; --card:#fff; --accent:#1d4ed8; --accent-soft:#eff6ff;
  --ok:#047857; --ok-soft:#ecfdf5; --warn:#b45309; --warn-soft:#fffbeb; --danger:#b91c1c; }
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin:0; background:var(--paper); color:var(--ink);
  font:15px/1.65 "Segoe UI","Microsoft YaHei",system-ui,sans-serif; }
.page { max-width:1100px; margin:0 auto; padding:32px 24px 56px; }
.brand { font-size:12px; letter-spacing:.18em; color:var(--accent); text-transform:uppercase; }
h1 { margin:6px 0 4px; font:700 30px/1.25 "Noto Serif SC","Songti SC",Georgia,serif; }
h2 { margin:0 0 14px; font:700 19px/1.3 "Noto Serif SC","Songti SC",Georgia,serif; }
h2 .hint { font:400 12px/1.4 "Segoe UI",sans-serif; color:var(--muted); margin-left:8px; }
h3 { margin:0; }
h4 { margin:0 0 8px; font-size:13px; letter-spacing:.04em; color:var(--muted); text-transform:uppercase; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
.masthead { border-bottom:1px solid var(--line); padding-bottom:16px; margin-bottom:20px; }
.lede, .meta { margin:0; color:var(--muted); }
.today { margin:10px 0 0; font-size:15.5px; }
.jumpnav { margin-top:12px; display:flex; gap:16px; flex-wrap:wrap; font-size:13px; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin:20px 0 28px; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }
.tile .num { display:block; font-size:24px; font-weight:700; line-height:1.2; }
.tile .label { font-size:12px; color:var(--muted); }
.tile.warn .num { color:var(--warn); } .tile.danger .num { color:var(--danger); }
section { margin:32px 0; }
.alerts { display:grid; gap:10px; }
.alert { display:flex; gap:14px; background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }
.alert.urgent { border-left:4px solid var(--danger); }
.alert-kind { flex:0 0 auto; font-size:12px; font-weight:700; color:var(--accent);
  background:var(--accent-soft); border-radius:999px; padding:3px 10px; height:fit-content; }
.alert.urgent .alert-kind { color:var(--danger); background:#fef2f2; }
.alert p { margin:0 0 4px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(420px,1fr)); gap:14px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px;
  display:flex; flex-direction:column; gap:8px; box-shadow:0 1px 2px rgba(17,24,39,.03); }
.badges { display:flex; flex-wrap:wrap; gap:6px; }
.badge { font-size:11px; padding:2px 9px; border-radius:999px; border:1px solid var(--line);
  color:var(--muted); background:#fafafa; }
.badge.source { color:var(--accent); border-color:#dbeafe; background:var(--accent-soft); }
.badge.ok { color:var(--ok); border-color:#d1fae5; background:var(--ok-soft); }
.badge.warn { color:var(--warn); border-color:#fde68a; background:var(--warn-soft); }
.card h3 { font-size:16px; line-height:1.45; font-weight:650; }
.card h3 a { color:var(--ink); } .card h3 a:hover { color:var(--accent); }
.excerpt { margin:0; color:#374151; font-size:13.5px; }
.personal { margin:0; padding:8px 10px; border-left:3px solid #9e3b2e;
  background:#f8f6f0; color:#4a463e; font-size:12.5px; border-radius:0 6px 6px 0; }
.card-foot { margin-top:auto; display:flex; justify-content:space-between; align-items:center;
  gap:10px; padding-top:8px; border-top:1px dashed var(--line); }
.time, .muted { color:var(--muted); font-size:12px; }
.links { display:flex; gap:12px; }
.links a { font-size:12.5px; }
.links .external { color:var(--muted); }
.panels { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.panel { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:16px; }
svg.bars { width:100%; height:auto; }
.bar-label { fill:#6b7280; font-size:12px; } .bar-value { fill:#111827; font-size:12px; }
code { display:inline-block; background:#f3f4f6; border:1px solid var(--line); border-radius:6px;
  padding:2px 7px; font-size:12px; color:#1f2937; margin:4px 6px 0 0; word-break:break-all; }
.detail { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:22px 24px; }
.detail-head h1 { font-size:25px; }
.detail-body { display:grid; grid-template-columns:minmax(0,1.6fr) minmax(260px,1fr); gap:22px; margin-top:14px; }
.fulltext { margin:0 0 14px; font-size:14.5px; color:#1f2937; white-space:pre-wrap; }
.ledger { border-radius:10px; padding:12px 14px; font-size:13.5px; }
.ledger.empty { background:var(--warn-soft); border:1px solid #fde68a; }
.ledger.ok { background:var(--ok-soft); border:1px solid #d1fae5; }
.ledger p { margin:0 0 8px; }
.claims, .preds { margin:6px 0 0; padding-left:18px; }
.claims li { margin:6px 0; } .preds li { margin:4px 0; color:#374151; }
.prov { display:grid; grid-template-columns:92px 1fr; gap:4px 10px; margin:0 0 14px; font-size:12.5px; }
.prov dt { color:var(--muted); } .prov dd { margin:0; word-break:break-all; }
.note { background:#f8fafc; border:1px dashed var(--line); border-radius:10px; padding:10px 12px; font-size:12.5px; color:#475569; }
.detail-foot { display:flex; justify-content:space-between; gap:12px; border-top:1px dashed var(--line);
  margin-top:18px; padding-top:10px; font-size:13px; }
.navpager { display:flex; justify-content:space-between; margin-top:14px; font-size:13px; }
.boundary { margin-top:36px; border-top:1px solid var(--line); padding-top:14px; color:var(--muted); font-size:12.5px; }
.empty { background:var(--card); border:1px dashed var(--line); border-radius:12px; padding:18px; color:var(--muted); }
@media (max-width:820px) { .panels, .detail-body { grid-template-columns:1fr; } }
@media print { body{background:#fff;} .jumpnav,.links,.detail-foot,.navpager,.alerts{display:none;}
  .page{max-width:none;padding:0;} }
"""


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>{STYLE}</style>
</head>
<body>
<div class="page">
{body}
</div>
</body>
</html>
"""


def _fetch_note(kind: str, error: str | None) -> str:
    if kind == "fetched":
        return ('<p class="note">正文来自联网抓取（无模型），仅作摘录；'
                "可能被站点反爬页/付费墙影响，未核验。</p>")
    note = '<p class="note">未抓取原文正文，以下为订阅摘要（未核验）。</p>'
    if error:
        note += f'<p class="note">抓取失败：{_esc(error)}；已回退订阅摘要。</p>'
    return note


def render_brief_site(conn: sqlite3.Connection, days: int = 2, limit: int = 10,
                      per_source: int = 2, topics=None, collection: list[dict] | None = None,
                      no_fetch: bool = False, fetch_details: bool = False,
                      fetch_text=None, today: str | None = None,
                      personalization: dict | None = None) -> dict:
    """Build the site in memory: {"index": str, "details": {relative_path: str}, "stats": {...}}."""
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

    entries, unlinked = [], []
    fetched_ok = fetched_fail = 0
    for row in shown:
        target_url = dedup.normalize_url(row["url"])
        similar = sum(1 for other in pool
                      if (target_url is not None and dedup.normalize_url(other["url"]) == target_url)
                      or dedup.titles_similar(row["title"] or "", other["title"] or ""))
        same_story = "同题1源" if similar <= 1 else f"同题 {similar} 源"
        document_id, claim_count, pending = _linked_claim_info(conn, row["id"])
        when = (row["published_at"] or row["fetched_at"] or "")[:16]
        content = " ".join((row["summary"] or "").split())
        kind, fetch_error = "summary", None
        if fetch_details and fetch_text is not None and row["url"]:
            try:
                fetched = fetch_text(row["url"])
                if isinstance(fetched, str) and fetched.strip():
                    content = " ".join(fetched.split())
                    kind = "fetched"
                    fetched_ok += 1
                else:
                    fetch_error = "抓取内容为空"
                    fetched_fail += 1
            except Exception as error:  # noqa: BLE001 - shown as a note, not raised
                fetch_error = str(error)[:160] or "抓取失败"
                fetched_fail += 1
        entries.append({
            "row": row, "similar": similar, "same_story": same_story, "when": when,
            "document_id": document_id, "claim_count": claim_count, "pending": pending,
            "content": content, "kind": kind, "fetch_error": fetch_error,
            "external": _href(row["url"]), "detail": f"detail/item-{row['id']}.html",
        })
        if document_id is None:
            unlinked.append(row)

    cards = []
    for entry in entries:
        row = entry["row"]
        if entry["document_id"] is None:
            state_text, state_cls = "未接账", "warn"
        else:
            state_text = f"已接账 · 主张 {entry['claim_count']} / 待结算 {entry['pending']}"
            state_cls = "ok"
        badges = "".join([
            _badge(row["feed_name"], "source"),
            _badge(entry["same_story"]),
            _badge(state_text, state_cls),
            _badge("未核验", "muted"),
        ])
        external = (f'<a class="external" href="{entry["external"]}" target="_blank" '
                    'rel="noopener noreferrer">原文 ↗</a>') if entry["external"] else ""
        personal_html = ""
        if row.get("personal_why"):
            personal_html += f'<p class="personal">与你有关：{_esc(row["personal_why"])}</p>'
        if row.get("personal_segment"):
            personal_html += f'<p class="personal">你在哪一段：{_esc(row["personal_segment"])}</p>'
        cards.append(f"""
<article class="card">
  <div class="badges">{badges}</div>
  <h3><a href="{entry["detail"]}">{_esc(row["title"])}</a></h3>
  <p class="excerpt">{_esc(_excerpt(entry["content"])) or "（该条目没有可摘录内容，请进详情或原文）"}</p>
  {personal_html}
  <div class="card-foot">
    <span class="time">{_esc(entry["when"])}</span>
    <span class="links"><a href="{entry["detail"]}">详情 →</a>{external}</span>
  </div>
</article>""")

    details = {}
    for index, entry in enumerate(entries):
        row = entry["row"]
        claims = _linked_claims(conn, entry["document_id"]) if entry["document_id"] is not None else []
        if entry["document_id"] is None:
            ledger = (
                '<div class="ledger empty"><p><strong>未接账。</strong>'
                "该条目尚未转成文档/主张，因此没有出处链、分数或预测。</p>"
                f'<code>ingest {row["id"]} --claim "一句话主张" --domain tech</code></div>'
            )
            actions = f'<code>read {row["id"]}</code>'
        else:
            claim_items = []
            for claim in claims:
                preds = "".join(
                    f'<li>P#{p["id"]} {_esc(p["status"])} · 截止 {_esc(p["due_date"])} · '
                    f'{_esc(p["text"])}'
                    + (f' · Brier {p["brier"]:.3f}' if p["brier"] is not None else "")
                    + f'<br><code>settle P#{p["id"]} hit|miss --note 依据</code></li>'
                    for p in claim["predictions"]
                )
                claim_items.append(
                    f'<li>#{claim["id"]} [{claim["credibility"]:.2f}] {_esc(claim["layer"])} · '
                    f'{_esc(claim["text"])}'
                    + (f'<ul class="preds">{preds}</ul>' if preds else "")
                    + "</li>"
                )
            ledger = (
                f'<div class="ledger ok"><p><strong>已接账 D#{entry["document_id"]}</strong>'
                f" · 主张 {entry['claim_count']} / 待结算 {entry['pending']}（分数未经校准）</p>"
                + (f'<ul class="claims">{"".join(claim_items)}</ul>' if claim_items else "")
                + "</div>"
            )
            actions = f'<code>read {row["id"]}</code>' + "".join(
                f'<code>predict {c["id"]} --text 可观察承诺 --due YYYY-MM-DD '
                "--hint 核验方法 [--p 0-1]</code>" for c in claims)
        provenance = [
            ("订阅来源", row["feed_name"]),
            ("条目编号", f"I#{row['id']}"),
            ("源称发布时间", row["published_at"] or "发布时间未知"),
            ("首次采集", row["fetched_at"]),
            ("同题源数", f"{entry['similar']}（启发式，仅提示）"),
            ("原文链接", _display_link(row["url"])),
        ]
        prov_rows = "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in provenance)
        external_foot = (f'<a href="{entry["external"]}" target="_blank" '
                         'rel="noopener noreferrer">打开原文 ↗</a>') if entry["external"] else ""
        prev_link = (f'<a href="item-{entries[index - 1]["row"]["id"]}.html">← 上一条</a>'
                     if index > 0 else '<span></span>')
        next_link = (f'<a href="item-{entries[index + 1]["row"]["id"]}.html">下一条 →</a>'
                     if index + 1 < len(entries) else '<span></span>')
        badge_html = "".join([
            _badge(row["feed_name"], "source"), _badge(entry["same_story"]),
            _badge("未核验", "muted"),
        ])
        body = f"""
<header class="masthead">
  <div class="brand">causal_ledger · detail</div>
  <p class="meta"><a href="../index.html">← 返回今日简报</a></p>
</header>
<article class="detail">
  <header class="detail-head">
    <div class="badges">{badge_html}</div>
    <h1>{_esc(row["title"])}</h1>
    <p class="meta">{_esc(entry["when"])} · {_esc(row["feed_name"])}</p>
  </header>
  <div class="detail-body">
    <section>
      <h4>{"原文正文摘录（联网抓取）" if entry["kind"] == "fetched" else "订阅摘要原文"}</h4>
      <p class="fulltext">{_esc(entry["content"]) or "（没有可用内容）"}</p>
      {f'<p class="personal">与你有关：{_esc(row["personal_why"])}</p>' if row.get("personal_why") else ''}
      {f'<p class="personal">你在哪一段：{_esc(row["personal_segment"])}</p>' if row.get("personal_segment") else ''}
      {_fetch_note(entry["kind"], entry["fetch_error"])}
      {ledger}
    </section>
    <aside>
      <h4>出处与时间</h4>
      <dl class="prov">{prov_rows}</dl>
      <h4>可以做什么</h4>
      <p class="muted">只作用于本地账簿；不会自动核实或外发。</p>
      {actions}
    </aside>
  </div>
  <footer class="detail-foot">
    <a href="../index.html">↑ 返回简报</a>
    {external_foot}
  </footer>
  <nav class="navpager">{prev_link}{next_link}</nav>
</article>
<footer class="boundary">
  实验分未经校准，不是真实概率；内容来自订阅摘要或联网抓取，均未核验；
  预测与接账由人工完成；数据只存本地，不自动外发、不安装定时任务。
</footer>"""
        details[entry["detail"]] = _page(f"{row['title']} · 因果簿详情", body)

    alerts = []
    for row in due_rows:
        alerts.append(
            '<div class="alert urgent"><span class="alert-kind">结算到期</span>'
            f'<div><p>P#{row["id"]} 截止 {_esc(row["due_date"])}：{_esc(row["text"])}</p>'
            f'<code>settle P#{row["id"]} hit|miss --note 依据</code></div></div>'
        )
    if unlinked:
        alerts.append(
            '<div class="alert"><span class="alert-kind">建议接账</span>'
            f'<div><p>推荐条目 I#{unlinked[0]["id"]} 尚未转成主张，可离线接账：</p>'
            f'<code>ingest {unlinked[0]["id"]} --claim "一句话主张"</code></div></div>'
        )
    if not alerts:
        alerts.append(
            '<div class="alert"><span class="alert-kind">无需操作</span>'
            "<div><p>今天没有到期预测，也没有需要立刻接账的条目。</p></div></div>"
        )

    linked_n = sum(1 for entry in entries if entry["document_id"] is not None)
    if fetch_details:
        collect_text += f" · 原文正文抓取 {fetched_ok}/{len(entries)}"
        if fetched_fail:
            collect_text += f"（失败 {fetched_fail}，已回退订阅摘要）"
    if entries:
        sources_text = "、".join(dict.fromkeys(entry["row"]["feed_name"] for entry in entries))
        today_line = f"今天 {len(entries)} 条值得看：来自 {sources_text}；其中 {linked_n} 条已接账。"
        if due_count:
            today_line += f" 另有 {due_count} 条预测到期，等你裁决。"
    else:
        today_line = "今天暂无符合条件的未读条目。"

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
    cards_html = "".join(cards) or '<p class="empty">暂无符合条件的未读条目。</p>'
    index_body = f"""
<header class="masthead">
  <div class="brand">causal_ledger · daily brief</div>
  <h1>因果簿 · 今日简报</h1>
  <p class="lede">{_esc(today)} · {_esc(collect_text)} · 关注主题：{_esc("、".join(topics)) if topics else "未设置（使用全部订阅）"}</p>
  <p class="today">{_esc(today_line)}</p>
  <nav class="jumpnav"><a href="#actions">待办</a><a href="#brief">值得注意</a>
    <a href="#stats">来源与趋势</a><span class="muted">详情为独立页面</span></nav>
</header>
<div class="kpis">{tiles_html}</div>
<section id="actions"><h2>待你处理</h2><div class="alerts">{''.join(alerts)}</div></section>
<section id="brief"><h2>值得注意 <span class="hint">{len(entries)} 条 · 未核验 · 点"详情"进入独立页面</span></h2>
  <div class="cards">{cards_html}</div></section>
<section id="stats"><h2>来源与趋势</h2><div class="panels">
  <div class="panel"><h4>近 7 天采集</h4>{_bars(_daily_counts(conn, days=7))}</div>
  <div class="panel"><h4>来源条目分布</h4>{_bars(_source_counts(conn), "#047857")}</div>
</div></section>
<footer class="boundary">
  实验分未经校准，不是真实概率；订阅条目、人工接账、预测与推荐结果均未核验；
  同题源数来自链接/标题相似启发式，仅提示不合并；数据只存本地，不自动外发、不安装定时任务。
  本地已保留 {digest_total} 期推送记录。
</footer>"""
    return {
        "index": _page(f"因果簿简报 {today}", index_body),
        "details": details,
        "stats": {"items": len(entries), "fetched_ok": fetched_ok,
                  "fetched_fail": fetched_fail, "due": due_count},
    }
