from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

from .db import atomic, connect
from .digest import list_digests, record_digest
from .extract import extract_claims, fetch_url
from .html_report import render_brief_html
from .html_site import render_brief_site
from .ingest import get_item, ingest_item, link_document, linked_document_id, prepare_content
from .provenance import add_prediction, import_extraction, independent_origins, validate_extraction
from .query import get_document, list_documents, search_claims
from .report import (
    format_probability,
    render_brief,
    render_digests,
    render_document,
    render_documents,
    render_reading_queue,
    render_report,
    render_search,
)
from .feeds import add_subscription, collect_feeds, mark_read, set_subscription_enabled, store_feed
from .settle import settle
from .watchlist import add_topic, enabled_topics, list_topics, remove_topic, set_topic_enabled

DEFAULT_DB = "causal_ledger.db"


def _print_import_summary(conn, summary: dict) -> None:
    print("实验分未经校准，不是真实概率；出处和独立性尚未核实。")
    print(f"文档 #{summary['document_id']}  主张 {len(summary['claims'])} 条")
    for c in summary["claims"]:
        row = conn.execute("SELECT * FROM claims WHERE id=?", (c["claim_id"],)).fetchone()
        tag = "新" if c["created"] else "并"
        print(f"  {tag} #{row['id']} [{row['credibility']:.2f}] {row['layer']:<13} 链{independent_origins(conn, row['id'])} {row['text']}")
    for s in summary["skipped_predictions"]:
        print(f"  跳过预测（主张#{s['claim_id']}）：{s['text']} —— {s['why']}")


def cmd_import(args, conn):
    if args.text:
        content, url = args.text, None
    elif args.file:
        content, url = Path(args.file).read_text(encoding="utf-8-sig"), None
    elif args.url:
        content, url = fetch_url(args.url), args.url
    else:
        sys.exit("需要 --text / --file / --url 之一")
    reporter = {"name": args.reporter, "type": args.reporter_type} if args.reporter else None
    extraction = extract_claims(content, reporter=reporter)
    validate_extraction(extraction)
    for item in extraction["claims"]:
        for prediction in item.get("predictions", []):
            prediction.pop("p", None)
    if args.dump:
        print(json.dumps(extraction, ensure_ascii=False, indent=2))
    summary = import_extraction(conn, extraction, title=args.title, url=url, raw=content)
    _print_import_summary(conn, summary)


def cmd_import_json(args, conn):
    if (args.file is None) == (args.dir is None):
        raise ValueError("需要 FILE 或 --dir 目录之一（二者只能给一个）")
    if args.feed_item is not None and args.dir is not None:
        raise ValueError("--feed-item 只能与单文件导入一起使用")
    if args.dir is not None:
        directory = Path(args.dir)
        if args.title is not None:
            raise ValueError("目录导入不支持 --title；每个文件的标题默认取文件名")
        if not directory.is_dir():
            raise ValueError(f"目录不存在：{directory}")
        files = sorted(directory.glob("*.json"))
        if not files:
            raise ValueError("目录中没有 .json 文件")
        failures = 0
        print("实验分未经校准，不是真实概率；出处和独立性尚未核实。")
        for path in files:
            try:
                extraction = json.loads(path.read_text(encoding="utf-8-sig"))
                summary = import_extraction(conn, extraction, title=path.name)
            except (ValueError, OSError, sqlite3.Error) as error:
                failures += 1
                print(f"  失败 {path.name}：{error}")
                continue
            created = sum(1 for c in summary["claims"] if c["created"])
            print(f"  完成 {path.name}：文档 #{summary['document_id']}，"
                  f"主张 {len(summary['claims'])}（新 {created}），"
                  f"跳过预测 {len(summary['skipped_predictions'])}")
        print(f"目录导入：成功 {len(files) - failures}，失败 {failures}（{directory}）")
        if failures:
            sys.exit("部分 JSON 导入失败；成功文件已保留，失败文件未写入业务表")
        return
    extraction = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
    if args.feed_item is not None:
        item = get_item(conn, args.feed_item)
        if linked_document_id(conn, args.feed_item) is not None:
            raise ValueError(f"条目 I#{args.feed_item} 已转换文档；不会重复导入")
        title = args.title or item["title"]
        with atomic(conn):
            summary = import_extraction(conn, extraction, title=title, url=item["url"])
            link_document(conn, args.feed_item, summary["document_id"])
        print(f"已关联订阅条目 I#{args.feed_item}（人工 JSON 接账；内容仍未核验）")
    else:
        summary = import_extraction(conn, extraction, title=args.title or Path(args.file).name)
    _print_import_summary(conn, summary)


def cmd_claims(args, conn):
    print("实验分未经校准，不是真实概率；出处和独立性尚未核实。")
    sql, params = "SELECT * FROM claims WHERE credibility>=?", [args.min]
    if args.domain:
        sql += " AND domain=?"
        params.append(args.domain)
    sql += " ORDER BY credibility DESC, id"
    for r in conn.execute(sql, params).fetchall():
        print(f"#{r['id']} [{r['credibility']:.2f}] {r['layer']:<13} 链{independent_origins(conn, r['id'])} {r['domain']:<10} {r['text']}")


def cmd_explain(args, conn):
    c = conn.execute("SELECT * FROM claims WHERE id=?", (args.id,)).fetchone()
    if not c:
        sys.exit(f"主张 #{args.id} 不存在")
    print(f"主张 #{c['id']}  实验分 {c['credibility']:.3f}")
    print(f"  文本：{c['text']}")
    print(f"  键：{c['canonical_key']}   领域：{c['domain']}   层级：{c['layer']}")
    print(f"  待核查线索（未验证，不加分）：{c['hard_signal'] or '无'}")
    print("  评分轨迹：")
    for t in json.loads(c["trace"] or "[]"):
        print(f"    - {t}")
    print("  出处链（isnad）：")
    rows = conn.execute(
        "SELECT ch.*, r.name AS reporter, r.type AS rtype, o.name AS origin, o.type AS otype, d.title"
        " FROM chains ch JOIN sources r ON r.id=ch.reporter_source_id JOIN sources o ON o.id=ch.origin_source_id"
        " JOIN documents d ON d.id=ch.document_id WHERE ch.claim_id=?",
        (c["id"],),
    ).fetchall()
    for r in rows:
        arrow = "" if r["reporter_source_id"] == r["origin_source_id"] else f" ← 引自 {r['origin']}({r['otype']})"
        print(f"    - 文档#{r['document_id']} {r['title'] or ''}  读自 {r['reporter']}({r['rtype']}){arrow}")
        if r["quote"]:
            print(f"      “{r['quote']}”")
    print("  预测：")
    for p in conn.execute("SELECT * FROM predictions WHERE claim_id=? ORDER BY due_date", (c["id"],)).fetchall():
        brier = f" Brier={p['brier']:.3f}" if p["brier"] is not None else ""
        print(f"    - P#{p['id']} [{p['status']}] 截止 {p['due_date']} {format_probability(p['p'])}{brier}  {p['text']}  核验：{p['check_hint']}")
    print("  实验分日志：")
    for l in conn.execute("SELECT * FROM credibility_log WHERE claim_id=? ORDER BY id", (c["id"],)).fetchall():
        print(f"    - {l['at']}  {l['old']:.3f} → {l['new']:.3f}  {l['reason']}")


def cmd_predict(args, conn):
    pid = add_prediction(conn, args.id, args.text, args.due, args.hint, p=args.p)
    stored = conn.execute("SELECT p FROM predictions WHERE id=?", (pid,)).fetchone()[0]
    print(f"预测 P#{pid} 已挂到主张 #{args.id}，{format_probability(stored)}（重复登记保留原记录）")


def cmd_settle(args, conn):
    r = settle(conn, args.id, args.outcome, note=args.note)
    if args.outcome == "void":
        print(f"P#{args.id} 作废")
    else:
        score = f"Brier={r['brier']:.3f}" if r["brier"] is not None else "未登记概率，不计 Brier"
        print(f"P#{args.id} {args.outcome}  {score}；仅记录预测结果，不推断主张真伪或因果")


def cmd_due(args, conn):
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT p.*, c.text AS claim_text FROM predictions p JOIN claims c ON c.id=p.claim_id"
        " WHERE p.status='pending' ORDER BY p.due_date"
    ).fetchall()
    for p in rows:
        flag = "逾期" if p["due_date"] < today else ("今日" if p["due_date"] == today else "    ")
        print(f"P#{p['id']} {flag} {p['due_date']} {format_probability(p['p'])}  {p['text']}  ← #{p['claim_id']} {p['claim_text']}")
        print(f"      核验：{p['check_hint']}")


def cmd_sources(args, conn):
    sql = (
        "SELECT s.id, s.name, s.type, d.domain, d.alpha, d.beta, d.n FROM sources s"
        " LEFT JOIN source_domain_stats d ON d.source_id=s.id"
    )
    params: list = []
    if args.domain:
        sql += " WHERE d.domain=?"
        params.append(args.domain)
    sql += " ORDER BY s.id, d.domain"
    for r in conn.execute(sql, params).fetchall():
        if r["domain"] is None:
            print(f"S#{r['id']} {r['name']} ({r['type']})  —— 尚无领域记录")
        else:
            print(f"S#{r['id']} {r['name']} ({r['type']})  {r['domain']:<10} 先验权重（未校准）{r['alpha']/(r['alpha']+r['beta']):.3f}  α={r['alpha']:.1f} β={r['beta']:.1f} n={r['n']}")


def cmd_report(args, conn):
    print(render_report(conn, days=args.days))


def cmd_feed(args, conn):
    if args.action == "add":
        feed_id = add_subscription(conn, args.name, args.url)
        print(f"订阅 F#{feed_id} 已登记；尚未联网采集，不代表来源已经核实")
    elif args.action == "list":
        rows = conn.execute("SELECT * FROM subscriptions ORDER BY id").fetchall()
        if not rows:
            print("暂无订阅，请用 feed add --name 名称 --url 订阅地址 添加")
        for row in rows:
            state = "启用" if row["enabled"] else "停用"
            print(f"F#{row['id']} [{state}] {row['name']}  {row['url']}")
            print(f"    最近采集：{row['checked_at'] or '无'}；错误：{row['last_error'] or '无'}")
    elif args.action in ("enable", "disable"):
        enabled = args.action == "enable"
        set_subscription_enabled(conn, args.id, enabled)
        print(f"F#{args.id} {'启用' if enabled else '停用'}")
    elif args.action == "import-file":
        with Path(args.file).open("rb") as source:
            raw = source.read(2 * 1024 * 1024 + 1)
        result = store_feed(conn, args.id, raw)
        print(f"F#{args.id} 离线订阅文件：新增 {result['added']}，重复 {result['duplicates']}")


def _collection_lines(results) -> list[str]:
    lines = ["订阅采集结果（不调用模型）"]
    if not results:
        lines.append("暂无启用订阅，本次没有联网采集。")
    for result in results:
        if result.get("error"):
            lines.append(f"  F#{result['feed_id']} 采集失败：{result['error']}")
        else:
            lines.append(f"  F#{result['feed_id']} 新增 {result['added']}，重复 {result['duplicates']}")
    return lines


def cmd_collect(args, conn):
    results = collect_feeds(conn, feed_ids=args.feed)
    print("\n".join(_collection_lines(results)))
    if any(result.get("error") for result in results):
        sys.exit("部分订阅采集失败，其余结果已保留；请查看 feed list")


def _resolved_topics(conn, topics):
    """Explicit --topic wins; otherwise use the enabled watchlist."""
    if topics:
        return list(topics)
    watched = enabled_topics(conn)
    return watched or None


def cmd_inbox(args, conn):
    print(render_reading_queue(conn, days=args.days, limit=args.limit,
                               topics=_resolved_topics(conn, args.topic),
                               per_source=args.per_source))


def cmd_item(args, conn):
    item = conn.execute(
        "SELECT i.*, s.name AS feed_name FROM feed_items i JOIN subscriptions s ON s.id=i.feed_id WHERE i.id=?",
        (args.id,),
    ).fetchone()
    if item is None:
        raise ValueError(f"条目 I#{args.id} 不存在")
    print(f"I#{item['id']} {item['title']} — {item['feed_name']}（未核验）")
    print(f"源称发布：{item['published_at'] or '发布时间未知'}；首次采集：{item['fetched_at']}")
    print(f"原文链接：{item['url'] or '无可用链接'}")
    print(f"已读时间：{item['read_at'] or '未读'}")
    print(item["summary"])
    linked = linked_document_id(conn, args.id)
    if linked is not None:
        print(f"已转换文档：D#{linked}（转换不表示内容已核实）")
    else:
        print(f"尚未转换为文档：可用 ingest {args.id} 转为文档（默认不抽取、不联网）。")


def cmd_read(args, conn):
    mark_read(conn, args.id)
    print(f"条目 I#{args.id} 已读；不表示内容已核实")


def cmd_daily(args, conn):
    from .profile import choose_mode, load_profile

    if args.html_dir:
        if args.html or args.brief:
            raise ValueError("--html-dir 与 --html/--brief 只能选一个")
        if args.output:
            raise ValueError("--html-dir 不接受 --output；站点直接写入该目录")
    if args.fetch_details and not args.html_dir:
        raise ValueError("--fetch-details 只用于 --html-dir（多页简报站）")
    if args.html and args.output is None:
        raise ValueError("--html 需要 --output 指定 .html 文件（不覆盖已有文件）")
    if args.html and args.brief:
        raise ValueError("--html 与 --brief 只能选一个：HTML 简报已包含简报内容")
    requested_mode = getattr(args, "mode", "auto")
    stored = load_profile(conn)
    effective_mode = choose_mode(stored, requested_mode)
    personalization = None
    calibration_note = ""
    if stored is not None:
        from .calibrate import effective_features
        features, calibration_note = effective_features(conn, stored["features"])
        personalization = {"mode": effective_mode, "features": features,
                           "completeness": stored["completeness"]}
        if calibration_note:
            print(calibration_note)
    elif requested_mode != "auto":
        print(f"未设置档案，已降级为通线（请求 {requested_mode}）。")
    topics = _resolved_topics(conn, args.topic)
    queue = render_reading_queue(conn, days=args.days, limit=args.limit, topics=topics,
                                 per_source=args.per_source, personalization=personalization)
    output = Path(args.output) if args.output else None
    if output is not None:
        if output.exists():
            raise FileExistsError(f"日报输出已存在，不覆盖：{output}")
        if not output.parent.is_dir():
            raise ValueError("日报输出目录不存在，请先创建目录")
    results = []
    if args.no_fetch:
        lines = ["未联网采集：只使用本地已有条目。"]
    else:
        results = collect_feeds(conn, feed_ids=args.feed)
        lines = _collection_lines(results)
        queue = render_reading_queue(conn, days=args.days, limit=args.limit, topics=topics,
                                     per_source=args.per_source, personalization=personalization)
    if args.html_dir:
        directory = Path(args.html_dir)
        index_path = directory / "index.html"
        details_dir = directory / "detail"
        if index_path.exists() or details_dir.exists():
            raise FileExistsError(f"简报站点已存在，不覆盖：{index_path}")
        site = render_brief_site(
            conn, days=args.days, limit=args.limit, per_source=args.per_source,
            topics=topics, collection=results, no_fetch=args.no_fetch,
            fetch_details=args.fetch_details,
            fetch_text=(lambda url: fetch_url(url, timeout=10)) if args.fetch_details else None,
            personalization=personalization)
        directory.mkdir(parents=True, exist_ok=True)
        details_dir.mkdir(exist_ok=True)
        index_path.write_text(site["index"], encoding="utf-8")
        for relative, page in site["details"].items():
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(page, encoding="utf-8")
        print(f"简报站点已保存：{index_path}（详情 {len(site['details'])} 页）")
        failures = [result for result in results if result.get("error")]
        record_digest(conn, kind="daily", path=str(index_path), ok=not failures,
                      summary={"days": args.days, "limit": args.limit,
                               "per_source": args.per_source, "topics": list(topics or []),
                               "no_fetch": bool(args.no_fetch), "format": "html-site",
                               "fetch_details": bool(args.fetch_details),
                               "fetch_ok": site["stats"]["fetched_ok"],
                               "fetch_fail": site["stats"]["fetched_fail"],
                               "feed_failures": len(failures),
                               "mode": effective_mode,
                               "calibration": calibration_note or None,
                               "collection": results})
        if failures:
            sys.exit("简报站点已生成，但部分订阅采集失败；详情见站点，不能视为完整采集")
        return
    if args.html:
        text = render_brief_html(conn, days=args.days, limit=args.limit, per_source=args.per_source,
                                 topics=topics, collection=results, no_fetch=args.no_fetch,
                                 personalization=personalization)
    elif args.brief:
        text = render_brief(conn, days=args.days, limit=args.limit, per_source=args.per_source,
                            topics=topics, collection=results, no_fetch=args.no_fetch,
                            personalization=personalization)
    else:
        text = "\n".join(lines) + "\n\n" + queue + "\n\n" + render_report(conn)
    if output is not None:
        with output.open("x", encoding="utf-8") as destination:
            destination.write(text + "\n")
        print(f"日报已保存：{output}")
    else:
        print(text)
    failures = [result for result in results if result.get("error")]
    record_digest(conn, kind="daily", path=str(output) if output is not None else None,
                  ok=not failures,
                  summary={"days": args.days, "limit": args.limit,
                           "per_source": args.per_source, "topics": list(topics or []),
                           "no_fetch": bool(args.no_fetch), "brief": bool(args.brief),
                           "format": "html" if args.html else ("brief" if args.brief else "text"),
                           "feed_failures": len(failures),
                           "mode": effective_mode,
                           "calibration": calibration_note or None,
                           "collection": results})
    if failures:
        sys.exit("日报已生成，但部分订阅采集失败；详情见报告，不能视为完整采集")


def cmd_docs(args, conn):
    print(render_documents(list_documents(conn, since=args.since, source=args.source, limit=args.limit)))


def cmd_doc(args, conn):
    print(render_document(get_document(conn, args.id)))


def cmd_search(args, conn):
    results = search_claims(conn, args.keyword, domain=args.domain, layer=args.layer,
                            since=args.since, min_credibility=args.min,
                            source=args.source, limit=args.limit)
    print(render_search(results, args.keyword))


def cmd_digests(args, conn):
    print(render_digests(list_digests(conn, limit=args.limit)))


def cmd_watch(args, conn):
    if args.action == "add":
        topic_id = add_topic(conn, args.topic)
        print(f"关注主题 W#{topic_id} 已添加：「{args.topic.strip()}」"
              "（inbox/daily 默认按启用主题筛选；--topic 可覆盖）")
    elif args.action == "list":
        rows = list_topics(conn)
        if not rows:
            print("暂无关注主题；可用 watch add 主题 添加")
        for row in rows:
            state = "启用" if row["enabled"] else "停用"
            print(f"W#{row['id']} [{state}] {row['topic']}")
    elif args.action in ("enable", "disable"):
        enabled = args.action == "enable"
        set_topic_enabled(conn, args.topic, enabled)
        print(f"关注主题「{args.topic.strip()}」已{'启用' if enabled else '停用'}")
    elif args.action == "remove":
        remove_topic(conn, args.topic)
        print(f"关注主题「{args.topic.strip()}」已删除")


def cmd_calibrate(args, conn):
    from .calibrate import apply, latest, propose
    from .profile import DOMAIN_LABELS, load_profile

    if load_profile(conn) is None:
        raise ValueError("请先导入个体档案（profile import FILE），再跑 7 天校准")
    proposal = propose(conn, days=args.days)
    print(f"7 天校准（近 {args.days} 天；备注 {proposal['notes_count']} 条，"
          f"忽略 {proposal['skipped']} 条）")
    if not proposal["deltas"]:
        print("没有可应用的调整：备注需要同时出现领域词和明确的正/负向词。")
        return
    for domain, delta in sorted(proposal["deltas"].items(), key=lambda item: -abs(item[1])):
        evidence = proposal["evidence"][domain]
        ids = "、".join(f"N#{item['note_id']}" for item in evidence[:5])
        print(f"  {DOMAIN_LABELS.get(domain, domain)}：{delta:+.3f}（证据备注 {ids}）")
    stored = latest(conn)
    if stored:
        print(f"当前生效：C#{stored['id']}（{stored['created_at']}，{stored['days']} 天）")
    if not args.apply:
        print("预览；确认后加 --apply 写入。写入只改权重，不改档案原文。")
        return
    calibration_id = apply(conn, proposal)
    print(f"已写入校准 C#{calibration_id}；下一次 daily 自动应用。")


def cmd_eval(args, conn):
    from pathlib import Path

    from .evaluation import ablate, blind_new, blind_stats, blind_vote

    if args.action == "blind":
        topics = _resolved_topics(conn, args.topic)
        result = blind_new(conn, Path(args.dir), days=args.days, limit=args.limit,
                           per_source=args.per_source, topics=topics)
        print(f"盲评已生成：{result['a']} 与 {result['b']}（A/B 已隐藏归属）")
        print("先读两份，再运行：eval vote A|B --dir " + args.dir)
        print("不要先看 blind.json；投票后才会揭晓。")
    elif args.action == "vote":
        result = blind_vote(conn, Path(args.dir), args.choice)
        print(f"你选了 {result['choice']}；揭晓：A={result['meta']['a_kind']}，"
              f"B={result['meta']['b_kind']}；本次胜者：{result['winner']}")
    elif args.action == "stats":
        stats = blind_stats(conn)
        if stats["total"] == 0:
            print("暂无盲评投票；先运行 eval blind 生成 A/B。")
            return
        print(f"盲评投票：共 {stats['total']} 次；个体线胜 {stats['individual']} 次，"
              f"通线胜 {stats['general']} 次；个体线占比 {stats['individual_share']:.0%}")
        print("样本很小时只作方向参考，不作准确率宣称。")
    elif args.action == "ablate":
        topics = _resolved_topics(conn, args.topic)
        results = ablate(conn, days=args.days, limit=args.limit,
                         per_source=args.per_source, topics=topics)
        print("消融：与完整个体线相比，各变体选中的前 4 条")
        for item in results:
            ids = "、".join(f"I#{value}" for value in item["ids"]) or "（无）"
            print(f"  {item['name']}：重叠 {item['overlap']:.0%}，同序 {item['same_order']}；"
                  f"{item['note']}；{ids}")
        print("重叠高说明该组件对选择影响小；重叠低说明它在改变你看到的东西。样本小，仅作方向参考。")


def cmd_cloud(args, conn):
    import os

    from .cloud import build_report, push_body, send_serverchan

    result = build_report(args.config, args.reports, days=args.days, limit=args.limit,
                          per_source=args.per_source, no_fetch=args.no_fetch,
                          today=args.date)
    print(f"云端报告已生成：{result['report']}（归档 {result['index']}；"
          f"订阅 {result['feeds']} 个，失败 {result['failures']} 个）")
    if args.no_push:
        print("推送：已跳过（--no-push）。")
        return
    key = os.environ.get("SERVERCHAN_KEY", "")
    send_title, body = push_body(result["day"], args.base_url, title=args.title)
    pushed = send_serverchan(key, send_title, body)
    if pushed["sent"]:
        print("推送：Server酱已发送。")
    else:
        print(f"推送：未发送（{pushed.get('reason', '未知原因')}）；报告仍已生成。")


def cmd_profile(args, conn):
    from .profile import (DOMAIN_LABELS, clear_profile, derive_features,
                          list_vault_audit, load_profile, save_profile,
                          unlock_vault, vault_status)

    if args.action == "import":
        with open(args.file, encoding="utf-8-sig") as source:
            data = json.load(source)
        saved = save_profile(conn, data)
        features = derive_features({"data": saved["data"]})
        domains = "、".join(f"{DOMAIN_LABELS.get(key, key)}:{value:.1f}"
                            for key, value in features["domains"].items())
        print(f"档案已导入：完整度 {saved['completeness']:.2f}；mode={saved['mode']}（auto）")
        print(f"派生领域：{domains or '（无）'}；公开部分只用于排序。")
        if saved["has_vault"]:
            print(f"保险箱：{len(saved['vault_fields'])} 个字段已加密（Windows DPAPI，绑定本机用户）："
                  + "、".join(saved["vault_fields"]))
        else:
            print("保险箱：无（未提供出生/事件等敏感字段）")
    elif args.action == "show":
        stored = load_profile(conn)
        if stored is None:
            print("暂无档案：当前走通线（通用读者版）。可运行 profile import FILE 导入基线。")
            return
        features = stored["features"]
        print(f"档案：完整度 {stored['completeness']:.2f}；mode={stored['mode']}；更新于 {stored['updated_at']}")
        domains = "、".join(f"{DOMAIN_LABELS.get(key, key)}:{value:.1f}"
                            for key, value in features["domains"].items())
        print(f"领域权重：{domains or '（无）'}")
        print(f"当前问题：{features['goals'].get('problem') or '（未填）'}")
        print(f"目标：{features['goals'].get('goal') or '（未填）'}")
        print(f"位置：{features['role'] or '（未填）'}；约束：{'、'.join(features['constraints']) or '（未填）'}")
        status = vault_status(conn)
        if status:
            print(f"保险箱：已封存 {len(status['fields'])} 个字段（未解锁）："
                  + "、".join(status["fields"]))
        else:
            print("保险箱：无")
        print("说明：show 不解锁保险箱；出生数据等字段默认不参与日常排序。")
    elif args.action == "unlock":
        vault = unlock_vault(conn, args.reason)
        print(f"保险箱已解锁（本次）：{len(vault)} 个字段；字段名："
              + "、".join(sorted(vault)))
        print("说明：本次解锁已写入 vault_audit；不打印字段原文，避免终端/日志泄露。")
    elif args.action == "audit":
        rows = list_vault_audit(conn, limit=args.limit)
        if not rows:
            print("暂无保险箱审计记录。")
            return
        for row in rows:
            print(f"A#{row['id']} {row['created_at']} {row['action']}：{row['reason']}")
    elif args.action == "clear":
        removed = clear_profile(conn)
        print("档案与保险箱已清除；之后自动走通线。" if removed else "暂无档案，无需清除。")


def cmd_serve(args, conn):
    from .app import serve as run_app

    def ready(server):
        print(f"今日事报应用：{server.url}")
        if args.token:
            print("已启用访问口令：链接后加 ?token=…，或请求头 X-Token。")
        print(f"事报目录：{args.reports}；备注写入数据库：{args.db}")
        print("Ctrl+C 停止。局域网访问请显式使用 --host 0.0.0.0 并设置 --token。")

    run_app(args.reports, host=args.host, port=args.port, token=args.token or "",
            db_path=args.db, on_ready=ready)


def cmd_ingest(args, conn):
    item = get_item(conn, args.id)
    existing = linked_document_id(conn, args.id)
    if existing is not None:
        print(f"条目 I#{args.id} 已转换文档 D#{existing}（不会重复导入；转换不表示内容已核实）")
        return
    title = args.title or item["title"]
    reporter_name = args.reporter or item["feed_name"]
    if args.reporter_type:
        print(f"注意：来源类型 {args.reporter_type} 会写入来源档案；若该来源原为 unknown，"
              "会按新类型重算其相关主张并留痕。")
    if args.claim is not None:
        content, url = prepare_content(conn, args.id, fetch=args.fetch)
        item_claim = {
            "text": args.claim,
            "canonical_key": args.claim_key or args.claim,
            "domain": args.domain,
            "layer": args.layer,
            "hard_signal": args.hard_signal,
            "quote": args.quote,
            "origin": ({"name": args.origin, "type": args.origin_type}
                       if args.origin else None),
            "predictions": [],
        }
        manual = {"reporter": {"name": reporter_name, "type": args.reporter_type or "unknown"},
                  "claims": [item_claim]}
        validate_extraction(manual)
        with atomic(conn):
            summary = import_extraction(conn, manual, title=title, url=url, raw=content)
            link_document(conn, args.id, summary["document_id"])
        print("人工接账（离线；未调用模型；来源链由你确认，内容仍未核验）。")
        print(f"条目 I#{args.id} → 文档 D#{summary['document_id']}；主张 {len(summary['claims'])} 条")
        for c in summary["claims"]:
            row = conn.execute("SELECT * FROM claims WHERE id=?", (c["claim_id"],)).fetchone()
            tag = "新" if c["created"] else "并"
            print(f"  {tag} #{row['id']} [{row['credibility']:.2f}] {row['layer']:<13} "
                  f"链{independent_origins(conn, row['id'])} {row['text']}")
        print("下一步：predict 主张ID --text 可观察承诺 --due YYYY-MM-DD --hint 核验方法 [--p 0-1]")
        return
    if args.extract:
        content, url = prepare_content(conn, args.id, fetch=args.fetch)
        kind = "原文全文（联网抓取）" if args.fetch else "订阅摘要（未抓取全文）"
        extraction = extract_claims(content, reporter={
            "name": reporter_name, "type": args.reporter_type or "unknown"})
        validate_extraction(extraction)
        for claim in extraction["claims"]:
            for prediction in claim.get("predictions", []):
                prediction.pop("p", None)
        # Document/claims/chains and the feed-item link must commit together.
        with atomic(conn):
            summary = import_extraction(conn, extraction, title=title, url=url, raw=content)
            link_document(conn, args.id, summary["document_id"])
        print("实验分未经校准，不是真实概率；出处和独立性尚未核实。")
        print(f"条目 I#{args.id} → 文档 D#{summary['document_id']}；主张 {len(summary['claims'])} 条"
              f"（内容来源：{kind}；模型只抽取，不裁判）")
        for c in summary["claims"]:
            row = conn.execute("SELECT * FROM claims WHERE id=?", (c["claim_id"],)).fetchone()
            tag = "新" if c["created"] else "并"
            print(f"  {tag} #{row['id']} [{row['credibility']:.2f}] {row['layer']:<13} "
                  f"链{independent_origins(conn, row['id'])} {row['text']}")
        for skipped in summary["skipped_predictions"]:
            print(f"  跳过预测（主张#{skipped['claim_id']}）：{skipped['text']} —— {skipped['why']}")
        print("订阅条目与阅读状态未改变；内容仍需人工核验，订阅条目不等于已核实主张。")
    else:
        result = ingest_item(conn, args.id, title=args.title, reporter=args.reporter,
                             reporter_type=args.reporter_type, fetch=args.fetch)
        kind = "原文全文（联网抓取）" if result["content_kind"] == "fetched" else "订阅摘要（未抓取全文）"
        print(f"条目 I#{args.id} → 文档 D#{result['document_id']}（"
              f"{'新建' if result['created'] else '已有'}；内容来源：{kind}；"
              "未抽取、未调用模型；内容未核验）")
        print("转换不表示内容已核实；如需盲读抽取请加 --extract --allow-remote（可能产生费用）。")


def _queue_arguments(parser):
    parser.add_argument("--days", type=int, default=7, help="待读条目回看天数（1到365）")
    parser.add_argument("--limit", type=int, default=10, help="最多推荐条数（1到100）")
    parser.add_argument("--topic", action="append",
                        help="主题关键词，可重复；省略时使用 watch 关注主题（仅筛选标题/摘要）")
    parser.add_argument("--per-source", type=int, default=2, help="每个订阅最多推荐条数")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="causal_ledger",
        description="因果簿：规则生预测，世界生裁决，裁决生规则",
        epilog=("所有分数均为未校准的实验分，不是真伪概率；导入/订阅/检索结果均未经核验；"
                "数据默认只存本地，不自动外发；daily 不安装定时任务。"))
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import", help="导入内容并盲读抽取")
    inputs = p.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--text")
    inputs.add_argument("--file")
    inputs.add_argument("--url")
    p.add_argument("--allow-remote", action="store_true", help="同意把内容发送给配置的外部模型服务，可能产生费用")
    p.add_argument("--title")
    p.add_argument("--reporter", help="发布者名称（已知时）")
    p.add_argument("--reporter-type", default=None)
    p.add_argument("--dump", action="store_true", help="打印 LLM 抽取结果")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("import-json", help="导入手工/离线抽取结果 JSON（单文件或整个目录）")
    p.add_argument("file", nargs="?", help="单个 JSON 文件")
    p.add_argument("--dir", help="目录：导入其中全部 .json 文件（逐文件独立、失败不中断）")
    p.add_argument("--feed-item", type=int, help="把本次文档关联到订阅条目 I#ID（离线接账）")
    p.add_argument("--title", help="单文件导入时的文档标题（默认取文件名或条目标题）")
    p.set_defaults(fn=cmd_import_json)

    p = sub.add_parser("claims")
    p.add_argument("--domain")
    p.add_argument("--min", type=float, default=0.0)
    p.set_defaults(fn=cmd_claims)

    p = sub.add_parser("explain")
    p.add_argument("id", type=int)
    p.set_defaults(fn=cmd_explain)

    p = sub.add_parser("predict")
    p.add_argument("id", type=int)
    p.add_argument("--text", required=True)
    p.add_argument("--due", required=True)
    p.add_argument("--hint", required=True)
    p.add_argument("--p", type=float, help="人工登记的预测概率（0到1）；省略则不计算 Brier")
    p.set_defaults(fn=cmd_predict)

    p = sub.add_parser("settle")
    p.add_argument("id", type=int)
    p.add_argument("outcome", choices=["hit", "miss", "void"])
    p.add_argument("--note", required=True, help="观察证据与核验时间，或作废原因")
    p.set_defaults(fn=cmd_settle)

    p = sub.add_parser("due")
    p.set_defaults(fn=cmd_due)

    p = sub.add_parser("sources")
    p.add_argument("--domain")
    p.set_defaults(fn=cmd_sources)

    p = sub.add_parser("report")
    p.add_argument("--days", type=int, default=1)
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("feed", help="管理 RSS/Atom 订阅，不调用模型")
    feeds = p.add_subparsers(dest="action", required=True)
    add = feeds.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--url", required=True)
    add.set_defaults(fn=cmd_feed)
    feeds.add_parser("list").set_defaults(fn=cmd_feed)
    for action in ("enable", "disable"):
        switch = feeds.add_parser(action)
        switch.add_argument("id", type=int)
        switch.set_defaults(fn=cmd_feed)
    offline = feeds.add_parser("import-file")
    offline.add_argument("id", type=int)
    offline.add_argument("file")
    offline.set_defaults(fn=cmd_feed)

    p = sub.add_parser("collect", help="采集已启用订阅，失败逐源记录")
    p.add_argument("--feed", type=int, action="append", help="只采集指定订阅ID，可重复")
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("inbox", help="本地未核验待读推荐")
    _queue_arguments(p)
    p.set_defaults(fn=cmd_inbox)
    p = sub.add_parser("item", help="查看完整订阅摘要及出处")
    p.add_argument("id", type=int)
    p.set_defaults(fn=cmd_item)
    p = sub.add_parser("read", help="标记订阅条目已读")
    p.add_argument("id", type=int)
    p.set_defaults(fn=cmd_read)

    p = sub.add_parser("daily", help="单次采集并生成本地日报；不安装定时任务、不调用模型")
    _queue_arguments(p)
    p.add_argument("--no-fetch", action="store_true", help="纯离线生成，不请求订阅网站")
    p.add_argument("--brief", action="store_true", help="紧凑简报：值得注意的少量条目 + 台账状态 + 待办（不含长篇推荐依据）")
    p.add_argument("--html", action="store_true", help="生成自包含 HTML 可视化简报（必须配合 --output；不覆盖已有文件）")
    p.add_argument("--html-dir", metavar="DIR", help="生成多页 HTML 简报站：DIR/index.html + DIR/detail/（不覆盖已有站点）")
    p.add_argument("--fetch-details", action="store_true", help="为选中条目抓取原文正文做摘录（联网、不调用模型；仅 --html-dir）")
    p.add_argument("--feed", type=int, action="append")
    p.add_argument("--mode", choices=["auto", "general", "light", "individual"], default="auto",
                   help="自动/通线/轻个体线/个体线；没有档案时自动降为通线")
    p.add_argument("--output", help="保存为新的 UTF-8 文本文件，不覆盖已有文件")
    p.set_defaults(fn=cmd_daily)

    p = sub.add_parser("docs", help="列出已导入文档（未核实）")
    p.add_argument("--since", help="导入日期下限 YYYY-MM-DD（本机自然日）")
    p.add_argument("--source", help="按发布者来源名称精确过滤")
    p.add_argument("--limit", type=int, default=20, help="最多条数（1到100）")
    p.set_defaults(fn=cmd_docs)

    p = sub.add_parser("doc", help="查看文档详情及关联主张")
    p.add_argument("id", type=int)
    p.set_defaults(fn=cmd_doc)

    p = sub.add_parser("search", help="检索文档标题/主张文本与键（未核验）")
    p.add_argument("keyword", nargs="+", help="关键词，可多个；每个都必须命中")
    p.add_argument("--domain", help="按领域精确过滤（小写英文词）")
    p.add_argument("--layer", choices=["intent", "plan", "formation", "manifestation"],
                   help="按陈述层级过滤")
    p.add_argument("--since", help="主张创建日期下限 YYYY-MM-DD（本机自然日）")
    p.add_argument("--min", type=float, default=0.0, help="实验分下限（0到1）")
    p.add_argument("--source", help="按 reporter/origin 来源名称精确过滤")
    p.add_argument("--limit", type=int, default=20, help="最多条数（1到100）")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("ingest", help="把订阅条目转为文档（默认不抽取；--extract 需 --allow-remote；--claim 为离线人工接账）")
    p.add_argument("id", type=int, help="订阅条目 I#ID")
    p.add_argument("--fetch", action="store_true", help="抓取原文全文（联网，不调用模型）")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--extract", action="store_true", help="盲读抽取并入库（需 --allow-remote，可能产生费用）")
    mode.add_argument("--claim", metavar="TEXT", help="人工确认的原子主张文本（离线，不调用模型）")
    p.add_argument("--allow-remote", action="store_true", help="同意把内容发送给配置的外部模型服务")
    p.add_argument("--title", help="文档标题（默认用条目标题）")
    p.add_argument("--reporter", help="发布者名称（默认用订阅名称）")
    p.add_argument("--reporter-type", default=None)
    p.add_argument("--claim-key", help="人工主张的 canonical_key（默认用主张文本规范化）")
    p.add_argument("--domain", help="人工主张领域（默认 general）")
    p.add_argument("--layer", choices=["intent", "plan", "formation", "manifestation"],
                   help="人工主张的陈述层级（默认 intent，最保守）")
    p.add_argument("--hard-signal", help="人工主张的待核验线索（PENDING，仅保存不加分）")
    p.add_argument("--quote", help="人工主张对应的原文片段")
    p.add_argument("--origin", help="人工确认的原始出处名称（默认同发布者）")
    p.add_argument("--origin-type", default=None)
    p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("digests", help="列出本地日报历史（不代表已投递或定时运行）")
    p.add_argument("--limit", type=int, default=20, help="最多条数（1到100）")
    p.set_defaults(fn=cmd_digests)

    p = sub.add_parser("watch", help="关注主题表：inbox/daily 默认按启用主题筛选（仅标题/摘要）")
    watch = p.add_subparsers(dest="action", required=True)
    add = watch.add_parser("add")
    add.add_argument("topic")
    add.set_defaults(fn=cmd_watch)
    watch.add_parser("list").set_defaults(fn=cmd_watch)
    remove = watch.add_parser("remove")
    remove.add_argument("topic")
    remove.set_defaults(fn=cmd_watch)
    for action in ("enable", "disable"):
        switch = watch.add_parser(action)
        switch.add_argument("topic")
        switch.set_defaults(fn=cmd_watch)

    p = sub.add_parser("profile", help="个体因果基线：导入/查看/清除；没有基线时自动走通线")
    profile_actions = p.add_subparsers(dest="action", required=True)
    imp = profile_actions.add_parser("import")
    imp.add_argument("file", help="问卷导出的 JSON 文件（本地读取，不上传）")
    imp.set_defaults(fn=cmd_profile)
    profile_actions.add_parser("show").set_defaults(fn=cmd_profile)
    unlock = profile_actions.add_parser("unlock")
    unlock.add_argument("--reason", required=True, help="解锁原因（写入审计）")
    unlock.set_defaults(fn=cmd_profile)
    audit = profile_actions.add_parser("audit")
    audit.add_argument("--limit", type=int, default=20, help="最多条数（1到100）")
    audit.set_defaults(fn=cmd_profile)
    profile_actions.add_parser("clear").set_defaults(fn=cmd_profile)

    p = sub.add_parser("calibrate", help="7 天校准：把末尾备注的领域正/负向转成小幅权重调整（先预览，--apply 写入）")
    p.add_argument("--days", type=int, default=7, help="回看天数（1到90）")
    p.add_argument("--apply", action="store_true", help="确认写入；不加则只预览")
    p.set_defaults(fn=cmd_calibrate)

    p = sub.add_parser("cloud", help="零月费云发布：生成静态日报/归档并可推 Server酱（不读档案/保险箱）")
    p.add_argument("--config", default="config/feeds.json", help="订阅配置 JSON")
    p.add_argument("--reports", default="reports", help="静态报告输出目录")
    p.add_argument("--days", type=int, default=2, help="回看天数（1到365）")
    p.add_argument("--limit", type=int, default=30, help="最多条数（1到100）")
    p.add_argument("--per-source", type=int, default=2, help="每来源最多条数（1到100）")
    p.add_argument("--no-fetch", action="store_true", help="不联网，只用空库生成（调试用）")
    p.add_argument("--no-push", action="store_true", help="不发送 Server酱")
    p.add_argument("--base-url", default=os.environ.get("PAGES_BASE_URL", ""),
                   help="GitHub Pages 基础 URL（默认读 PAGES_BASE_URL）")
    p.add_argument("--date", help="覆盖日期 YYYY-MM-DD（调试用）")
    p.add_argument("--title", default="今日事报")
    p.set_defaults(fn=cmd_cloud)

    p = sub.add_parser("eval", help="盲评与消融：比较通线/个体线，不宣称准确率")
    eval_actions = p.add_subparsers(dest="action", required=True)
    blind = eval_actions.add_parser("blind")
    _queue_arguments(blind)
    blind.add_argument("--dir", required=True, help="存放 A.txt/B.txt/blind.json 的目录（须为空/不存在）")
    blind.set_defaults(fn=cmd_eval)
    vote = eval_actions.add_parser("vote")
    vote.add_argument("choice", choices=["A", "B"])
    vote.add_argument("--dir", required=True, help="eval blind 生成的目录")
    vote.set_defaults(fn=cmd_eval)
    eval_actions.add_parser("stats").set_defaults(fn=cmd_eval)
    ablation = eval_actions.add_parser("ablate")
    _queue_arguments(ablation)
    ablation.set_defaults(fn=cmd_eval)

    p = sub.add_parser("serve", help="本地单应用：阅读今日事报/往期、提交末尾备注（默认仅本机）")
    p.add_argument("--reports", default="app/reports", help="存放 YYYY-MM-DD.html 的事报目录")
    p.add_argument("--host", default="127.0.0.1", help="绑定地址；局域网用 0.0.0.0（需配 --token）")
    p.add_argument("--port", type=int, default=8765, help="端口（1到65535）")
    p.add_argument("--token", help="可选访问口令；局域网访问建议设置")
    p.set_defaults(fn=cmd_serve)
    return ap


def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "import" and not args.allow_remote:
        parser.error("内容会发送到配置的外部模型服务，可能产生费用；确认后加 --allow-remote，或用离线 import-json")
    if args.cmd == "ingest" and args.extract and not args.allow_remote:
        parser.error("ingest --extract 会把内容发送给配置的外部模型服务，可能产生费用；确认后加 --allow-remote，或先只用 ingest 建文档")
    conn = None
    try:
        conn = connect(args.db)
        args.fn(args, conn)
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
        sys.exit(f"错误：{error}")
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()
