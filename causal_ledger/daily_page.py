"""Render the current "今日事报" causal-chain page for the zero-cost cloud path.

This replaces the older "因果簿简报" card layout. It does not invent facts:
- signals are the feed items themselves (title/source/time/link);
- effects are cautious, theme-level inferences marked as such;
- every chain carries "我的链路" and a "可能看错" caveat;
- the cloud route is the general line: no profile/vault is read.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from html import escape
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .bearing import render_bearing_section

LOCAL_TZ = ZoneInfo("Asia/Shanghai")

THEMES = (
    {
        "key": "tech",
        "label": "技术与工具",
        "keywords": ("ai", "人工智能", "模型", "芯片", "软件", "编程", "开源", "工具",
                     "产品", "科技", "技术", "硬件", "互联网", "平台", "算法", "机器人"),
        "reasoning": "这几条来自不同来源，但都在改变同一件事：谁能做、成本多少、多久能到产品。",
        "effects": (
            "工具成本下降会先改变小团队与个人的可做范围，再传导到岗位与产品节奏。",
            "下一轮争点可能不是“有没有新功能”，而是“谁能承接记忆、代理与工作流”。",
            "值得观察的不是发布会，而是随后的定价、开放接口与真实使用数据。",
        ),
        "doubt": "也可能只是同一季度的产品叙事；本条不证明这些动作之间存在传导。",
    },
    {
        "key": "trust",
        "label": "信息与信任",
        "keywords": ("媒体", "假", "伪", "谣言", "核验", "监管", "隐私", "安全", "平台",
                     "社交", "内容", "真相", "事实", "来源", "审查", "生成"),
        "reasoning": "它们题材不同，却都在削弱同一件事：可追溯的权威解释。",
        "effects": (
            "生成与传播成本下降时，辨认成本上升；平台标注与个人核验会成为下一层基础设施。",
            "“来源”会从一条链接变成一条可复核的传述链：谁先说、谁转述、哪里能验证。",
            "对个人来说，保留分歧与出处，比急着得出一个干净结论更有用。",
        ),
        "doubt": "三者的责任主体与时间尺度差别很大；把它们放进同一条链，可能是我对“信任结构”的偏爱。",
    },
    {
        "key": "economy",
        "label": "经济与生活",
        "keywords": ("经济", "价格", "消费", "房价", "就业", "工作", "收入", "市场", "公司",
                     "政策", "税", "工资", "裁员", "招聘", "成本", "通胀"),
        "reasoning": "它们都落在“成本如何传导到普通人的预算与选择”这条线上。",
        "effects": (
            "价格与就业信号会经由家庭预算与企业决策传导；先看可观察的下一步，而不是宏观口号。",
            "同一条政策对不同位置的人影响不同：先确认自己处在受影响、可行动还是旁观的位置。",
            "值得盯的是招聘、价格与政策执行的时间差，而不是单日情绪。",
        ),
        "doubt": "单条新闻可能只是季节性波动；没有连续数据时，不应把它写成趋势。",
    },
    {
        "key": "health",
        "label": "健康与身体",
        "keywords": ("健康", "医疗", "疾病", "睡眠", "运动", "疫苗", "疫情", "医院",
                     "身体", "心理", "药", "医生", "体检", "研究"),
        "reasoning": "它们都涉及“个体身体”与“公共资源”之间的传导，但证据层级不同。",
        "effects": (
            "单条健康研究不等于结论；先看样本、来源与可复现性，再看它是否改变个人安排。",
            "公共健康信号会先影响资源分配，再影响个人选择；两者不要混为一谈。",
            "值得观察的是后续指南、审批与真实世界数据，而不是标题里的“突破”。",
        ),
        "doubt": "健康类内容最容易被标题放大；本条只做存目，不构成医疗建议。",
    },
    {
        "key": "world",
        "label": "世界与长周期",
        "keywords": ("战争", "选举", "气候", "能源", "国际", "外交", "供应链", "灾害",
                     "疫情", "政策", "制裁", "谈判", "停火", "援助"),
        "reasoning": "它们与今天的技术主线未必直接相连，但会改变技术叙事的背景条件。",
        "effects": (
            "低频高影响事件会改变供应链、公共资源与跨境规则，进而影响技术落地的边界。",
            "这类信号更适合“存目”而不是“解释”：先记住位置，等它与主线真正相交时再连。",
            "值得观察的是后续谈判、执行与资源流向，而不是单日声量。",
        ),
        "doubt": "强行把远方事件与个人生活连起来，容易变成叙事；本条只记录，不强行归因。",
    },
)

DEFAULT_THEME = {
    "key": "other",
    "label": "其他信号",
    "reasoning": "它们暂时没有明显同构，但可能在某天与主线相交；先存目，不强行归因。",
    "effects": ("暂不判断方向；保留位置，等后续信号。",),
    "doubt": "这一组是“存目”，不是“因果链”；不要把它当作已证实的联系。",
}

# Strong terms carry the theme; broad terms alone are not enough. This prevents
# "平台/内容/公司" from dragging unrelated car or streaming stories into a chain.
STRONG = {
    "tech": ("ai", "人工智能", "模型", "芯片", "开源", "编程", "软件", "算法", "机器人",
             "半导体", "算力", "webassembly", "agent"),
    "trust": ("伪造", "谣言", "核验", "隐私", "审查", "监管", "误导", "事实核查",
              "版权", "dmca", "假"),
    "economy": ("价格", "房价", "就业", "裁员", "招聘", "通胀", "工资", "税",
                "关税", "利率", "成本"),
    "health": ("疾病", "疫苗", "疫情", "医院", "睡眠", "医疗", "健康", "病毒",
               "药物", "医生", "体检"),
    "world": ("战争", "选举", "制裁", "停火", "外交", "供应链", "灾害", "谈判",
              "援助", "气候", "能源"),
}


def _match_keyword(text: str, word: str) -> bool:
    word = word.casefold()
    if word.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None
    return word in text


def _safe_href(url: str | None) -> str:
    if not isinstance(url, str):
        return ""
    parts = urlsplit(url)
    return url if parts.scheme in ("http", "https") and parts.hostname else ""


def _text(value) -> str:
    return escape(str(value or ""), quote=True)


def _classify(item: dict) -> dict:
    text = ((item.get("title") or "") + " " + (item.get("summary") or "")).casefold()
    best, best_score = None, 0
    for theme in THEMES:
        strong = STRONG.get(theme["key"], ())
        strong_hits = sum(1 for word in strong if _match_keyword(text, word))
        if not strong_hits:
            continue
        weak_hits = sum(1 for word in theme["keywords"]
                        if word not in strong and _match_keyword(text, word))
        score = strong_hits * 2 + weak_hits * 0.5
        if score > best_score:
            best, best_score = theme, score
    return best or DEFAULT_THEME


def _strength(items: list[dict], theme: dict) -> tuple[str, str]:
    feeds = {item.get("feed_name") or item.get("feed_id") for item in items}
    if theme is DEFAULT_THEME:
        return "弱", "存目"
    if len(feeds) >= 3:
        return "强", "独立源同向"
    if len(feeds) >= 2:
        return "中", "跨源同构"
    return "弱", "单源待观察"


def _group_items(items: list[dict]) -> list[tuple[dict, list[dict]]]:
    groups: dict[str, tuple[dict, list[dict]]] = {}
    for item in items:
        theme = _classify(item)
        groups.setdefault(theme["key"], (theme, []))[1].append(item)
    real = []
    for theme, group in groups.values():
        if theme is DEFAULT_THEME:
            continue
        feeds = {item.get("feed_name") or item.get("feed_id") for item in group}
        if len(group) >= 2 and len(feeds) >= 2:
            real.append((theme, group))
    real.sort(key=lambda pair: (-len(pair[1]), pair[0]["label"]))
    chosen = real[:3]
    used = {item.get("id") for _, group in chosen for item in group}
    leftovers = [item for item in items if item.get("id") not in used]
    if len(chosen) < 3 and leftovers:
        chosen.append((DEFAULT_THEME, leftovers))
    return chosen


def _item_html(item: dict, text: str | None = None) -> str:
    title = _text(text or item.get("title") or "（无标题）")
    source = _text(item.get("feed_name") or "未知来源")
    when = (item.get("published_at") or item.get("fetched_at") or "")[:16]
    when = _text(when)
    href = _safe_href(item.get("url"))
    link = f'<a href="{_text(href)}" target="_blank" rel="noopener noreferrer">原文</a>' if href else ""
    meta = " · ".join(part for part in (source, when, link) if part)
    return f'<div class="signal"><p class="t">{title}</p><p class="src">{meta}</p></div>'


def _chain_section(index: int, title: str, strength: str, strength_note: str,
                   signals: str, effects: str, reasoning: str, doubt: str,
                   count: int) -> str:
    chain_no = ['一', '二', '三'][index - 1] if index <= 3 else str(index)
    return f"""
<section class="chain">
  <div class="chain-head">
    <span class="chain-no">链{chain_no}</span>
    <h2 class="chain-title">{_text(title)}</h2>
    <span class="strength">{_text(strength)} · {_text(strength_note)}</span>
  </div>
  <div class="spread">
    <div class="cause">
      <p class="col-head"><b>因</b>　今天出现的信号</p>
      {signals}
    </div>
    <div class="axis"><span class="dot"></span></div>
    <div class="effect">
      <p class="col-head"><b>果</b>　我由此推断</p>
      <ul>{effects}</ul>
    </div>
  </div>
  <div class="reasoning">
    <p class="label">我的链路</p>
    <p>{_text(reasoning)}</p>
    <p class="doubt">可能看错：{_text(doubt)} 本条基于 {count} 条信号、未核验。</p>
  </div>
</section>"""


def _source_list(items: list[dict]) -> str:
    seen, rows = set(), []
    for item in items:
        key = item.get("url") or item.get("title")
        if key in seen:
            continue
        seen.add(key)
        title = _text(item.get("title") or "（无标题）")
        source = _text(item.get("feed_name") or "")
        when = _text((item.get("published_at") or item.get("fetched_at") or "")[:16])
        href = _safe_href(item.get("url"))
        link = f'<a href="{_text(href)}" target="_blank" rel="noopener noreferrer">原文</a>' if href else ""
        meta = " · ".join(part for part in (source, when, link) if part)
        rows.append(f'<li><span class="src-title">{title}</span><span class="src-meta">{meta}</span></li>')
    return ('<section class="sources"><h2>本周信号清单（保留出处）</h2><ul>'
            + "".join(rows) + "</ul></section>") if rows else ""


def render_daily_page(items: list[dict], day: str | None = None, *, title: str = "今日事报",
                      period_start: str | None = None, period_end: str | None = None,
                      weekly: bool = False, synthesis: dict | None = None,
                      bearing_cards: list[dict] | None = None,
                      personal: bool = False) -> str:
    day = day or period_end or datetime.now(LOCAL_TZ).date().isoformat()
    date.fromisoformat(day)
    if weekly:
        if not period_start or not period_end:
            raise ValueError("weekly 页面需要 period_start 和 period_end")
        date.fromisoformat(period_start)
        date.fromisoformat(period_end)
    items = list(items or [])
    chains = _group_items(items)
    real = [pair for pair in chains if pair[0] is not DEFAULT_THEME]
    scope = "这一周" if weekly else "今天"
    labels = "、".join(f"「{theme['label']}」" for theme, _ in real) or "（无候选）"
    if real:
        thesis = (f"{scope}值得看的不是单条新闻，而是{labels}这几个方向的动作；"
                  "它们各自在改变成本、信任或背景条件。")
        if len(chains) > len(real):
            thesis += "另有一些只作存目的信号。"
    elif chains:
        thesis = f"{scope}没有形成清晰主题；以下只作存目，不强行造链。"
    else:
        thesis = f"{scope}没有足够的候选信号；只保留位置，不强行造链。"
    used_count = sum(len(group) for _, group in chains)
    scope_note = "本地个体线不读保险箱" if personal else "云端通线不读个人档案"
    algorithm = (
        (f"汇总 {len(items)} 条 / " if weekly else f"输入 {len(items)} 条 / ")
        + f"{len({item.get('feed_name') or item.get('feed_id') for item in items})} 个源；"
        f"入链 {used_count} 条、存目 {len(items) - used_count} 条；"
        f"按主题聚类取前三；{scope_note}；条目未核验；强度只是启发式提示。"
    )
    period_line = f"{period_start} ~ {period_end}" if weekly else day
    if weekly:
        kicker = f"{title} · 周报"
        lede = "本周由日报汇总；因与果摆在下面，链路完整写出。周报不读你的档案，这是通线版本。"
    elif personal:
        kicker = f"{title} · 个体线"
        lede = "这页在本地生成：读档案特征，不读保险箱；新闻只是因源之一，承因在前，因果链在后。"
    else:
        kicker = f"{title} · 云端通线"
        lede = ("因与果都摆在下面；中间的链路完整写出。云端不读你的档案，所以这是通线版本；个体线仍在本地。")
    section_title = "本周算法链" if weekly else "今日算法链"
    chain_html = []
    bearing_html = render_bearing_section(bearing_cards) if bearing_cards else ""
    if synthesis:
        item_by_id = {item.get("id"): item for item in items}
        used_count = 0
        for index, chain in enumerate(synthesis.get("chains") or [], 1):
            signals = []
            for cause in chain.get("cause") or []:
                item = item_by_id.get(cause.get("id"))
                if item is None:
                    signals.append(f'<div class="signal"><p class="t">{_text(cause.get("text"))}</p></div>')
                else:
                    signals.append(_item_html(item, text=cause.get("text")))
            effects = "".join(f"<li>{_text(line)}</li>" for line in chain.get("effect") or [])
            used_count += len(chain.get("cause") or [])
            chain_html.append(_chain_section(
                index, chain.get("title") or "未命名", chain.get("strength") or "中",
                "二次整理", "".join(signals), effects, chain.get("reasoning") or "",
                chain.get("doubt") or "", len(chain.get("cause") or [])))
        thesis = synthesis.get("thesis") or thesis
        algorithm = (
            (f"汇总 {len(items)} 条 / " if weekly else f"输入 {len(items)} 条 / ")
            + f"{len({item.get('feed_name') or item.get('feed_id') for item in items})} 个源；"
            f"DeepSeek 二次整理为 {len(chain_html)} 条链、引用 {used_count} 条信号；"
            f"本页由 DeepSeek 二次翻译/整理，不是原文摘抄；{scope_note}；条目未核验；"
            "强度只是启发式提示。"
        )
    else:
        for index, (theme, group) in enumerate(chains, 1):
            strong, strength_note = _strength(group, theme)
            effects = "".join(f"<li>{_text(line)}</li>" for line in theme["effects"])
            signals = "".join(_item_html(item) for item in group[:3])
            chain_html.append(_chain_section(
                index, theme["label"], strong, strength_note, signals, effects,
                theme["reasoning"], theme["doubt"], len(group)))
    rendered_chain_count = len(chain_html)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_text(title)} · {_text(day)}</title>
<style>
:root {{ --paper:#f6f2ea; --ink:#1b1a17; --soft:#4a463e; --muted:#8a8376;
  --line:#d8d0c0; --red:#9e3b2e; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:radial-gradient(1200px 600px at 10% -10%, #fbf8f2, transparent 60%), var(--paper);
  color:var(--ink); font:16px/1.8 "Songti SC","Noto Serif SC",Georgia,serif; }}
.page {{ max-width:1040px; margin:0 auto; padding:52px 30px 70px; }}
.masthead {{ display:flex; align-items:flex-end; justify-content:space-between; gap:20px;
  border-bottom:1px solid var(--ink); padding-bottom:16px; }}
.kicker {{ font-size:12px; letter-spacing:.26em; color:var(--red); margin-bottom:8px; }}
h1 {{ margin:0; font-size:42px; letter-spacing:.06em; }}
.date {{ color:var(--muted); font-size:13px; letter-spacing:.14em; margin-top:8px; }}
.seal {{ width:52px; height:52px; border:1.5px solid var(--red); color:var(--red);
  display:grid; place-items:center; font-size:14px; letter-spacing:.18em;
  writing-mode:vertical-rl; text-orientation:upright; }}
.thesis {{ margin:30px 0 6px; font-size:20px; line-height:1.8; }}
.lede {{ color:var(--soft); font-size:13.5px; margin:0 0 28px; }}
.bearing-block {{ margin:26px 0 30px; border-top:1px solid var(--line); border-bottom:1px solid var(--line); padding:16px 0 6px; }}
.bearing-block h2 {{ margin:0 0 10px; font-size:20px; letter-spacing:.08em; color:var(--red); }}
.bearing-block .bearing {{ padding:12px 0 14px; border-top:1px dotted var(--line); }}
.bearing-block .bearing-head {{ display:flex; justify-content:space-between; gap:12px; align-items:baseline; }}
.bearing-block .no {{ color:var(--red); font-size:12px; letter-spacing:.12em; }}
.bearing-block .tag, .bearing-block .path, .bearing-block .meta {{ color:var(--muted); font-size:12px; }}
.bearing-block .carrier {{ margin:6px 0 2px; font-size:18px; }}
.bearing-block .action {{ margin:8px 0; }}
.chain {{ margin:36px 0 0; }}
.chain-head {{ display:flex; align-items:baseline; gap:12px; border-top:1px solid var(--line); padding-top:16px; }}
.chain-no {{ font-size:12px; letter-spacing:.28em; color:var(--red); }}
.chain-title {{ margin:0; font-size:25px; letter-spacing:.05em; }}
.strength {{ margin-left:auto; font-size:12px; letter-spacing:.12em; color:var(--muted); }}
.spread {{ display:grid; grid-template-columns:minmax(0,1fr) 40px minmax(0,1fr); margin-top:18px; }}
.cause,.effect {{ padding:0 2px; }}
.axis {{ position:relative; display:flex; justify-content:center; }}
.axis::before {{ content:""; position:absolute; top:6px; bottom:6px; width:1px;
  background:linear-gradient(var(--line), var(--soft), var(--line)); opacity:.5; }}
.axis .dot {{ width:7px; height:7px; border-radius:50%; background:var(--red); margin-top:40px;
  position:relative; z-index:1; box-shadow:0 0 0 5px var(--paper); }}
.col-head {{ font-size:12.5px; letter-spacing:.2em; color:var(--muted); margin:0 0 10px; }}
.col-head b {{ color:var(--ink); letter-spacing:.08em; }}
.signal {{ margin:0 0 14px; }}
.signal .t {{ font-size:16px; line-height:1.65; margin:0; }}
.signal .src {{ margin:4px 0 0; font-size:12px; color:var(--muted); }}
.signal .src a {{ color:var(--muted); text-decoration:none; border-bottom:1px dotted var(--muted); }}
.signal .src a:hover {{ color:var(--red); border-color:var(--red); }}
.effect ul {{ list-style:none; margin:0; padding:0; }}
.effect li {{ position:relative; padding-left:1.1em; margin:0 0 10px; font-size:15.5px; }}
.effect li::before {{ content:"—"; position:absolute; left:0; color:#b8604f; }}
.reasoning {{ margin:16px 0 0; padding:14px 16px; border:1px solid var(--line); background:rgba(255,255,255,.3); }}
.reasoning .label {{ margin:0 0 6px; font-size:12px; letter-spacing:.22em; color:var(--red); }}
.reasoning p {{ margin:0 0 6px; font-size:14px; color:var(--soft); }}
.reasoning .doubt {{ color:var(--muted); font-size:12.5px; }}
.algorithm {{ margin-top:44px; border-top:1px solid var(--ink); padding-top:18px; }}
.algorithm h2 {{ font-size:20px; margin:0 0 8px; letter-spacing:.06em; }}
.algorithm p {{ margin:0 0 6px; color:var(--soft); font-size:13.5px; }}
.algorithm .muted {{ color:var(--muted); font-size:12.5px; }}
.sources {{ margin-top:30px; border-top:1px solid var(--line); padding-top:16px; }}
.sources h2 {{ font-size:19px; margin:0 0 10px; letter-spacing:.05em; }}
.sources ul {{ list-style:none; margin:0; padding:0; }}
.sources li {{ display:flex; justify-content:space-between; gap:14px; padding:7px 0;
  border-bottom:1px dashed var(--line); font-size:13px; }}
.sources .src-title {{ color:var(--ink); }}
.sources .src-meta {{ color:var(--muted); white-space:nowrap; font-size:12px; }}
.sources a {{ color:var(--muted); text-decoration:none; border-bottom:1px dotted var(--muted); }}
.foot {{ margin-top:34px; padding-top:12px; border-top:1px solid var(--line);
  color:var(--muted); font-size:12px; display:flex; justify-content:space-between; gap:16px; }}
@media (max-width:760px) {{
  .page {{ padding:32px 18px 54px; }}
  h1 {{ font-size:32px; }}
  .spread {{ grid-template-columns:1fr; }}
  .axis {{ display:none; }}
  .effect {{ margin-top:16px; }}
  .foot {{ flex-direction:column; }}
}}
</style></head><body><div class="page">
<header class="masthead">
  <div>
    <div class="kicker">{_text(kicker)}</div>
    <h1>{_text(title)}</h1>
    <div class="date">{_text(period_line)} · {len(items)} 条信号 · 取 {rendered_chain_count} 条链</div>
  </div>
  <div class="seal" aria-hidden="true">事报</div>
</header>
<p class="thesis">{_text(thesis)}</p>
<p class="lede">{_text(lede)}</p>
{bearing_html}
{"".join(chain_html) if chain_html else f'<p class="lede">{scope}没有可用候选；不强行造链。</p>'}
{_source_list(items) if weekly else ''}
<section class="algorithm">
  <h2>{_text(section_title)}</h2>
  <p>{_text(algorithm)}</p>
  <p class="muted">不按点击历史排序；不自动外发除 Server酱 以外的内容；所有条目与推断均未核验。</p>
</section>
<footer class="foot">
  <span>推断，不是结论。核验、结算、预测在本地账簿另做。</span>
  <span>{_text(period_line)} · {_text('个体线' if personal else '通线')}</span>
</footer>
</div></body></html>
"""
