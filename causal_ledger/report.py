from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from .provenance import independent_origins

def format_probability(p: float | None) -> str:
    return f"p={p:.2f}" if p is not None else "p=未登记"


def _fmt_claim(conn: sqlite3.Connection, r: sqlite3.Row) -> str:
    return f"  #{r['id']} [{r['credibility']:.2f}] {r['layer']:<13} 链{independent_origins(conn, r['id'])} {r['domain']:<10} {r['text']}"


def render_reading_queue(
    conn: sqlite3.Connection, days: int = 7, limit: int = 10,
    topics: list[str] | None = None, per_source: int = 2,
    personalization: dict | None = None,
) -> str:
    from .feeds import recommend
    from . import personalize
    from .profile import MODE_LABELS

    items = recommend(conn, days=days, limit=limit, topics=topics, per_source=per_source)
    mode = "general"
    if personalization:
        mode = personalization.get("mode", "general")
        if mode != "general":
            items = personalize.rank_items(items, personalization["features"], mode)
    out = ["待读推荐（未核验）",
           f"回看 {days} 天，最多 {limit} 条，每个订阅最多 {per_source} 条；按时间及主题筛选，不代表真实性或因果。",
           "只在已配置订阅中选择；不代表全网覆盖，不同订阅也不保证证据独立。"]
    if personalization:
        out.append(f"模式：{MODE_LABELS.get(mode, mode)}")
    if not items:
        out.append("暂无符合条件的未读条目；可添加订阅、导入订阅文件或调整主题条件。")
    for item in items:
        out.append(f"  I#{item['id']} {item['title']}  — {item['feed_name']}")
        out.append(f"      源称发布（未核实）：{item['published_at'] or '发布时间未知'}；首次采集：{item['fetched_at']}")
        out.append(f"      推荐依据：{item['reason']}")
        out.append(f"      原文链接：{item['url'] or '无可用链接'}")
        if item["summary"]:
            out.append(f"      摘要：{item['summary'][:240]}")
        for line in personalize.lines_for(item, mode):
            out.append(f"      {line}")
    out.append("阅读后可用 read 条目ID 标记；订阅条目不会自动转为已核实主张。")
    return "\n".join(out)


def _brief_selection(items: list[dict], max_items: int = 4) -> list[dict]:
    """Pick a few items worth a human look: cross-source signals first, then feed diversity."""
    chosen: list[dict] = []
    chosen_ids: set[int] = set()
    for row in items:
        if len(chosen) >= 2:
            break
        if "疑似重复" in row["reason"]:
            chosen.append(row)
            chosen_ids.add(row["id"])
    used_feeds: set[int] = set()
    for row in items:
        if len(chosen) >= max_items:
            break
        if row["id"] in chosen_ids or row["feed_id"] in used_feeds:
            continue
        used_feeds.add(row["feed_id"])
        chosen.append(row)
        chosen_ids.add(row["id"])
    return chosen


def _linked_claim_info(conn: sqlite3.Connection, item_id: int) -> tuple[int | None, int, int]:
    """Return (document_id, claim_count, pending_prediction_count)."""
    from .ingest import linked_document_id

    document_id = linked_document_id(conn, item_id)
    if document_id is None:
        return None, 0, 0
    claim_ids = [row[0] for row in conn.execute(
        "SELECT DISTINCT claim_id FROM chains WHERE document_id=?", (document_id,))]
    if not claim_ids:
        return document_id, 0, 0
    placeholders = ",".join("?" for _ in claim_ids)
    pending = conn.execute(
        f"SELECT COUNT(*) FROM predictions WHERE status='pending' AND claim_id IN ({placeholders})",
        claim_ids,
    ).fetchone()[0]
    return document_id, len(claim_ids), pending


def render_brief(conn: sqlite3.Connection, days: int = 2, limit: int = 10,
                 per_source: int = 2, topics=None, collection: list[dict] | None = None,
                 no_fetch: bool = False, today: str | None = None,
                 personalization: dict | None = None) -> str:
    """Compact push: a few items worth attention, provenance/link state, ledger status, actions."""
    from . import dedup
    from . import personalize
    from .feeds import recommend
    from .profile import MODE_LABELS

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
        collect_line = "采集：未联网采集（仅使用本地已有条目）"
    else:
        collect_line = (f"采集：{success} 源成功 / {failures} 源失败；本次新增 {added} 条")

    due_rows = conn.execute(
        "SELECT id, due_date, text FROM predictions"
        " WHERE status='pending' AND due_date<=? ORDER BY due_date LIMIT 2", (today,)
    ).fetchall()
    pending_count = conn.execute("SELECT COUNT(*) FROM predictions WHERE status='pending'").fetchone()[0]
    due_count = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE status='pending' AND due_date<=?", (today,)
    ).fetchone()[0]
    new_claims = conn.execute(
        "SELECT COUNT(*) FROM claims WHERE created_at>=?", (since,)
    ).fetchone()[0]
    claim_total = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    ingested_total = conn.execute("SELECT COUNT(*) FROM feed_ingests").fetchone()[0]
    now = datetime.now(timezone.utc)
    unread_count = conn.execute(
        "SELECT COUNT(*) FROM feed_items i JOIN subscriptions s ON s.id=i.feed_id"
        " WHERE s.enabled=1 AND i.read_at IS NULL"
        " AND julianday(COALESCE(i.published_at,i.fetched_at)) BETWEEN julianday(?) AND julianday(?)",
        ((now - timedelta(days=days)).isoformat(), now.isoformat()),
    ).fetchone()[0]

    out = [f"因果簿简报  {today}（本地生成；条目与分数均未核验）",
           collect_line]
    if personalization:
        out.append(f"模式：{MODE_LABELS.get(mode, mode)}")
    out.extend([
           ("关注主题：" + "、".join(topics) + "（watch list，仅筛选标题/摘要）")
           if topics else "关注主题：未设置（使用全部订阅）",
           f"一、值得注意（{len(shown)}）"])
    unlinked_shown: list[dict] = []
    if not shown:
        out.append("  暂无符合条件的未读条目；可先添加订阅、采集或调整 --days/--topic。")
    for index, row in enumerate(shown, 1):
        when = (row["published_at"] or row["fetched_at"] or "")[:16]
        tags = []
        if "疑似重复" in row["reason"]:
            tags.append("跨源同题（仅提示）")
        if row["published_at"] is None:
            tags.append("发布时间未知")
        tags.append("未核验")
        target_url = dedup.normalize_url(row["url"])
        similar = sum(1 for other in pool
                      if (target_url is not None and dedup.normalize_url(other["url"]) == target_url)
                      or dedup.titles_similar(row["title"] or "", other["title"] or ""))
        same_story = "同题1源" if similar <= 1 else f"同题{similar}源"
        document_id, claim_count, pending = _linked_claim_info(conn, row["id"])
        if document_id is None:
            state = "未接账"
            unlinked_shown.append(row)
        else:
            state = f"已接账 D#{document_id}（主张{claim_count}/待结算{pending}）"
        link = dedup.display_url(row["url"]) or row["url"] or "无链接"
        out.append(f"  {index}. {row['title']} — {row['feed_name']}")
        out.append(f"     {when} · {same_story} · {state} · " + " · ".join(tags)
                   + f" · {link}")
        for line in personalize.lines_for(row, mode):
            out.append(f"     {line}")
    out.extend([
        "二、台账状态",
        f"  未读：{unread_count} 条；已接账条目：{ingested_total}；主张：{claim_total}；"
        f"到期/逾期预测：{due_count}；待结算：{pending_count}；今日新增主张：{new_claims}",
        "三、待你处理（人工）",
    ])
    if due_rows:
        parts = "；".join(f"P#{row['id']} 截止 {row['due_date']}（settle P#{row['id']} hit|miss --note 依据）"
                          for row in due_rows)
        out.append(f"  - 结算到期：{parts}")
    if unlinked_shown:
        out.append(f"  - 接账：ingest {unlinked_shown[0]['id']} --claim \"一句话主张\"（离线、不调用模型）")
    out.append("  - 登记预测：predict 主张ID --text 可观察承诺 --due YYYY-MM-DD --hint 核验方法 [--p 0-1]")
    out.extend([
        "四、边界",
        "  实验分未经校准，不是真实概率；订阅条目、推荐与检索结果均未核验；"
        "不自动外发、不安装定时任务。",
    ])
    return "\n".join(out)


def render_documents(rows) -> str:
    out = ["文档列表（导入内容，未核实）",
           "按导入时间倒序；标题、正文与来源均可能来自未核验内容，不代表真实性。"]
    if not rows:
        out.append("（暂无文档；可用 import-json / import / ingest 导入）")
    for row in rows:
        out.append(f"  D#{row['id']} {row['title'] or '（无标题）'}  — {row['reporter']}({row['reporter_type']})")
        out.append(f"      导入：{row['imported_at']}  关联主张：{row['claim_count']}  "
                   f"链接：{row['url'] or '无'}")
    return "\n".join(out)


def render_document(detail: dict) -> str:
    document = detail["document"]
    out = [f"文档 D#{document['id']}  {document['title'] or '（无标题）'}",
           f"  发布者来源：{document['reporter']}({document['reporter_type']})  导入时间：{document['imported_at']}",
           f"  原文链接：{document['url'] or '无'}"]
    if document["raw_length"]:
        out.append(f"  正文长度：{document['raw_length']} 字符（内容未核验）")
    else:
        out.append("  正文：未保存（手工 JSON 导入不存原文；不代表没有内容，内容未核验）")
    ingest = detail["ingest"]
    if ingest is not None:
        out.append(f"  由订阅条目 I#{ingest['feed_item_id']}（{ingest['feed_name']}）转换；"
                   "转换与阅读均不表示内容已核实")
    claims = detail["claims"]
    out.append(f"  关联主张（{len(claims)}）")
    for claim in claims:
        out.append(f"    #{claim['id']} [{claim['credibility']:.2f}] {claim['layer']:<13} "
                   f"链{claim['origins']} {claim['domain']:<10} {claim['text']}")
    if not claims:
        out.append("    （无；订阅/导入内容不会自动变成主张）")
    out.append("实验分未经校准，不是真实概率；出处与独立性待核实。")
    return "\n".join(out)


def render_search(results: list[dict], keywords) -> str:
    if isinstance(keywords, str):
        keywords = [keywords]
    if isinstance(keywords, (list, tuple)):
        keywords = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
    else:
        keywords = []
    out = ["检索结果（未核验）", "实验分未经校准，不是真实概率；匹配不等于相关，也不代表内容为真。"]
    if keywords:
        out.append("关键词：" + " ".join(keywords) + "（每个关键词都必须命中主张文本/键或文档标题）")
    out.append(f"结果数：{len(results)}（仅返回前 N 条，可用 --limit 调整）")
    if not results:
        out.append("（无匹配；可减少关键词、放宽领域/层级/日期/分数过滤）")
    for row in results:
        out.append(f"  #{row['id']} [{row['credibility']:.2f}] {row['layer']:<13} "
                   f"链{row['origins']} {row['domain']:<10} {row['text']}")
    out.append("提示：搜索只说明字符串命中；要判断真伪仍需人工核查与结算。")
    return "\n".join(out)


def render_digests(rows) -> str:
    out = ["日报历史（本地记录；不代表已投递或已发布）",
           "记录只说明生成过什么，不表示内容已核验，也不表示定时任务在运行。"]
    if not rows:
        out.append("（暂无记录；运行一次 daily 即可生成）")
    for row in rows:
        state = "成功" if row["ok"] else "部分失败"
        out.append(f"  D#{row['id']} {row['created_at']} [{row['kind']}] {state}")
        out.append(f"      文件：{row['path'] or '未写文件（仅标准输出）'}")
        summary = row["summary_json"] if len(row["summary_json"]) <= 200 else row["summary_json"][:197] + "..."
        out.append(f"      参数：{summary}")
    return "\n".join(out)


def render_report(conn: sqlite3.Connection, days: int = 1, today: str | None = None) -> str:
    if type(days) is not int or days < 1:
        raise ValueError("日报回看天数必须是正整数")
    today = today or date.today().isoformat()
    day = date.fromisoformat(today)
    since = datetime.combine(day - timedelta(days=days - 1), time.min).astimezone(timezone.utc).isoformat()
    until = datetime.combine(day + timedelta(days=1), time.min).astimezone(timezone.utc).isoformat()
    out: list[str] = [f"因果簿日报  {today}  （回看 {days} 个自然日）",
                      "实验分未经校准，不是真实概率；出处与独立性待核实；人工结算不证明因果。", ""]

    due = conn.execute(
        "SELECT p.*, c.text AS claim_text FROM predictions p JOIN claims c ON c.id=p.claim_id"
        " WHERE p.status='pending' AND p.due_date<=? ORDER BY p.due_date",
        (today,),
    ).fetchall()
    out.append(f"一、到期待结算（{len(due)}）")
    for p in due:
        flag = "逾期" if p["due_date"] < today else "今日"
        out.append(f"  P#{p['id']} {flag} {p['due_date']}  {format_probability(p['p'])}  {p['text']}")
        out.append(f"      ← 主张#{p['claim_id']} {p['claim_text']}")
        out.append(f"      核验：{p['check_hint']}")
    out.append("")

    settled = conn.execute(
        "SELECT p.*, c.text AS claim_text FROM predictions p JOIN claims c ON c.id=p.claim_id"
        " WHERE p.status IN ('hit','miss','void') AND p.settled_at>=? AND p.settled_at<? ORDER BY p.settled_at DESC",
        (since, until),
    ).fetchall()
    out.append(f"二、已结算（{len(settled)}）")
    for p in settled:
        brier = f"Brier={p['brier']:.3f}" if p["brier"] is not None else ("作废不计分" if p["status"] == "void" else "未登记概率，不计 Brier")
        out.append(f"  P#{p['id']} {p['status']:<4} {brier}  {p['text']}  ← 主张#{p['claim_id']}")
        out.append(f"      人工核验依据：{p['note']}")
    out.append("")

    new_claims = conn.execute(
        "SELECT * FROM claims WHERE created_at>=? AND created_at<? ORDER BY credibility DESC", (since, until)
    ).fetchall()
    out.append(f"三、新增主张（{len(new_claims)}）")
    for r in new_claims:
        out.append(_fmt_claim(conn, r))
    out.append("")

    movers = conn.execute(
        "SELECT l.claim_id, l.old, l.new, l.reason, c.text FROM credibility_log l JOIN claims c ON c.id=l.claim_id"
        " WHERE l.at>=? AND l.at<? ORDER BY ABS(l.new-l.old) DESC LIMIT 10",
        (since, until),
    ).fetchall()
    out.append(f"四、实验分变动记录（{len(movers)}）")
    for m in movers:
        out.append(f"  #{m['claim_id']} {m['old']:.2f} → {m['new']:.2f}  ({m['reason']})  {m['text']}")
    out.append("")

    out.append("五、来源档案（按领域；仅为类型先验，尚未校准）")
    rows = conn.execute(
        "SELECT s.name, s.type, d.domain, d.alpha, d.beta, d.n FROM source_domain_stats d JOIN sources s ON s.id=d.source_id"
        " ORDER BY d.domain, s.id"
    ).fetchall()
    if not rows:
        out.append("  （尚无来源记录）")
    for r in rows:
        out.append(f"  {r['domain']:<10} {r['alpha']/(r['alpha']+r['beta']):.3f} n={r['n']:<3} {r['name']} ({r['type']})")
    out.append("")

    total_claims = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM predictions WHERE status='pending'").fetchone()[0]
    done = conn.execute("SELECT COUNT(*), AVG(brier) FROM predictions WHERE brier IS NOT NULL").fetchone()
    avg = f"{done[1]:.3f}" if done[1] is not None else "-"
    settled_count = conn.execute("SELECT COUNT(*) FROM predictions WHERE status!='pending'").fetchone()[0]
    out.append(f"总计：主张 {total_claims}  待结算 {pending}  已结算 {settled_count}  可计 Brier {done[0]}  平均 Brier {avg}")
    return "\n".join(out)
