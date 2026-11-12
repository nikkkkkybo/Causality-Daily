"""Render the current "今日事报" causal-chain page for the zero-cost cloud path.

This replaces the older "因果簿简报" card layout. It does not invent facts:
- signals are the feed items themselves (title/source/time/link);
- effects are cautious, theme-level inferences marked as such;
- every chain carries "我的链路" and a "可能看错" caveat;
- the cloud route is the general line: no profile/vault is read.
"""
from __future__ import annotations

from datetime import date, datetime
from html import escape
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

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
        score = sum(1 for word in theme["keywords"] if word.casefold() in text)
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
    ordered = sorted(groups.values(), key=lambda pair: (-len(pair[1]), pair[0]["label"]))
    chosen = ordered[:3]
    if not chosen:
        return []
    used = {item["id"] for _, group in chosen for item in group}
    leftovers = [item for item in items if item.get("id") not in used]
    if leftovers and len(chosen) < 3:
        chosen.append((DEFAULT_THEME, leftovers))
    return chosen


def _item_html(item: dict) -> str:
    title = _text(item.get("title") or "（无标题）")
    source = _text(item.get("feed_name") or "未知来源")
    when = (item.get("published_at") or item.get("fetched_at") or "")[:16]
    when = _text(when)
    href = _safe_href(item.get("url"))
    link = f'<a href="{_text(href)}" target="_blank" rel="noopener noreferrer">原文</a>' if href else ""
    meta = " · ".join(part for part in (source, when, link) if part)
    return f'<div class="signal"><p class="t">{title}</p><p class="src">{meta}</p></div>'


def render_daily_page(items: list[dict], day: str | None = None, *, title: str = "今日事报") -> str:
    day = day or datetime.now(LOCAL_TZ).date().isoformat()
    date.fromisoformat(day)
    items = list(items or [])
    chains = _group_items(items)
    labels = "、".join(f"「{theme['label']}」" for theme, _ in chains) or "（今日无候选）"
    if chains:
        thesis = (f"今天值得看的不是单条新闻，而是{labels}这几个方向的动作；"
                  "它们各自在改变成本、信任或背景条件。")
    else:
        thesis = "今天没有足够的候选信号；这份事报只保留位置，不强行造链。"
    algorithm = (
        f"输入 {len(items)} 条 / {len({item.get('feed_name') or item.get('feed_id') for item in items})} 个源；"
        "按主题聚类取前三；云端通线不读个人档案；条目未核验；强度只是启发式提示。"
    )
    chain_html = []
    for index, (theme, group) in enumerate(chains, 1):
        strong, strength_note = _strength(group, theme)
        effects = "".join(f"<li>{_text(line)}</li>" for line in theme["effects"])
        signals = "".join(_item_html(item) for item in group[:3])
        chain_html.append(f"""
<section class="chain">
  <div class="chain-head">
    <span class="chain-no">链{['一','二','三'][index - 1] if index <= 3 else index}</span>
    <h2 class="chain-title">{_text(theme['label'])}</h2>
    <span class="strength">{_text(strong)} · {_text(strength_note)}</span>
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
    <p>{_text(theme['reasoning'])}</p>
    <p class="doubt">可能看错：{_text(theme['doubt'])} 本条基于 {len(group)} 条信号、未核验。</p>
  </div>
</section>""")

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
    <div class="kicker">{_text(title)} · 云端通线</div>
    <h1>{_text(title)}</h1>
    <div class="date">{_text(day)} · {len(items)} 条信号 · 取 {len(chains)} 条链</div>
  </div>
  <div class="seal" aria-hidden="true">事报</div>
</header>
<p class="thesis">{_text(thesis)}</p>
<p class="lede">因与果都摆在下面；中间的链路完整写出。云端不读你的档案，所以这是通线版本；个体线仍在本地。</p>
{"".join(chain_html) if chain_html else '<p class="lede">今天没有可用候选；不强行造链。</p>'}
<section class="algorithm">
  <h2>今日算法链</h2>
  <p>{_text(algorithm)}</p>
  <p class="muted">不按点击历史排序；不自动外发除 Server酱 以外的内容；所有条目与推断均未核验。</p>
</section>
<footer class="foot">
  <span>推断，不是结论。核验、结算、预测在本地账簿另做。</span>
  <span>{_text(day)} · 通线</span>
</footer>
</div></body></html>
"""
