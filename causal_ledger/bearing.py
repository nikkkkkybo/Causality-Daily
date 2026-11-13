"""Personal bearing: turn cause sources into "what lands on this person today".

This is intentionally not a fortune engine. Temporal / profile / news adapters
produce normalized cause signals; external lenses (almanac, astrology, ...) can
feed normalized signals through a JSON file or a registered callable. The
selector then produces at most three personal bearing cards.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

MAX_CARDS = 4

DOMAIN_LABELS = {
    "career": "事业", "wealth": "财务", "health": "健康",
    "relationship": "关系", "family": "家庭", "learning": "学习",
    "social": "社交", "inner": "内在",
}
POLARITY_LABELS = {"favor": "宜承", "avoid": "缓承", "watch": "观察"}

_WEEKDAY = {
    0: ("开局", "先排优先级，不急着开新战线。"),
    4: ("收束", "把本周未闭合的事收口，别把尾巴带进周末。"),
    5: ("恢复", "把时间还给恢复与关系，不安排高消耗硬仗。"),
    6: ("恢复", "留出无目的的时间，让负荷降下来。"),
}
_SEASON = {
    3: ("展开", "春季适合试错与小步展开，别一次押太大。"),
    4: ("展开", "春季适合试错与小步展开，别一次押太大。"),
    5: ("展开", "春季适合试错与小步展开，别一次押太大。"),
    6: ("扩张", "夏季适合推进，但要预留消耗后的恢复。"),
    7: ("扩张", "夏季适合推进，但要预留消耗后的恢复。"),
    8: ("扩张", "夏季适合推进，但要预留消耗后的恢复。"),
    9: ("收束", "秋季适合兑现与收口，把摊子收回到主线。"),
    10: ("收束", "秋季适合兑现与收口，把摊子收回到主线。"),
    11: ("收束", "秋季适合兑现与收口，把摊子收回到主线。"),
    12: ("蓄力", "冬季适合复盘与蓄力，不逼自己持续输出。"),
    1: ("蓄力", "冬季适合复盘与蓄力，不逼自己持续输出。"),
    2: ("蓄力", "冬季适合复盘与蓄力，不逼自己持续输出。"),
}

STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
STEM_ELEMENTS = {
    "甲": "木", "乙": "木", "丙": "火", "丁": "火", "戊": "土",
    "己": "土", "庚": "金", "辛": "金", "壬": "水", "癸": "水",
}
JIANCHU = [
    ("建", "favor", "定调/开局", "先定今天的主线，再开新动作。"),
    ("除", "favor", "清理/去除", "先清掉一个旧尾巴，再谈推进。"),
    ("满", "favor", "收满/完成", "把已经做到八成的事收满。"),
    ("平", "watch", "维持/平推", "今天适合平推，不急着定输赢。"),
    ("定", "favor", "定案/承诺", "适合把可逆的事定下来；不可逆的再等。"),
    ("执", "watch", "坚持/守成", "守住已有节奏，不临时改方向。"),
    ("破", "avoid", "破局/不可逆", "今天先不做不可逆的破局决定。"),
    ("危", "avoid", "冒险/高处", "今天不登高、不押大注，先降风险。"),
    ("成", "favor", "收成/定稿", "适合定稿、交付、收结果。"),
    ("收", "favor", "收束/回收", "把摊子收回来，别继续扩张。"),
    ("开", "favor", "开启/打开", "适合开一个可逆的小口子。"),
    ("闭", "avoid", "封闭/收尾", "今天适合收尾，不适合强行开新局。"),
]


def _jdn(d: date) -> int:
    a = (14 - d.month) // 12
    y = d.year + 4800 - a
    m = d.month + 12 * a - 3
    return d.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def day_pillar(day: str) -> tuple[str, str, int]:
    """Sexagenary day pillar; exact for the Gregorian calendar."""
    d = date.fromisoformat(day)
    index = (_jdn(d) + 49) % 60
    return STEMS[index % 10], BRANCHES[index % 12], index


def _month_branch(d: date) -> int:
    """Approximate month branch by solar-term boundaries (off by ≤1 day)."""
    md = (d.month, d.day)
    boundaries = [
        ((1, 6), 1), ((2, 4), 2), ((3, 6), 3), ((4, 5), 4),
        ((5, 6), 5), ((6, 6), 6), ((7, 7), 7), ((8, 8), 8),
        ((9, 8), 9), ((10, 8), 10), ((11, 7), 11), ((12, 7), 0),
    ]
    branch = 1  # 丑 before 小寒
    for (m, day), value in boundaries:
        if md >= (m, day):
            branch = value
    return branch


def traditional_signals(day: str, birth: str | None = None) -> list[CauseSignal]:
    """Simplified 黄历/命理 lens: 建除 + 日主关系. Heuristic, not event prediction."""
    d = date.fromisoformat(day)
    stem, branch, _ = day_pillar(day)
    month_branch = _month_branch(d)
    day_branch_index = BRANCHES.index(branch)
    jianchu_index = (day_branch_index - month_branch) % 12
    label, polarity, activity, action = JIANCHU[jianchu_index]
    signals = [_signal(
        f"黄历·建除（{stem}{branch}日）", "lens", "*", polarity, 0.45,
        f"建除十二神·{label}（{activity}）",
        f"日期 → 建除 → {activity}", action,
        "只取建除一层，节气按近似边界；不含神煞/宜忌全表，不是事件预测。",
        window="today", hidden=True, priority=60)]
    if birth:
        try:
            b = date.fromisoformat(birth)
        except ValueError:
            return signals
        bstem, _, _ = day_pillar(birth)
        b_element = STEM_ELEMENTS.get(bstem, "")
        t_element = STEM_ELEMENTS.get(stem, "")
        if not b_element or not t_element or b > d:
            return signals
        # relation of today's stem element to the birth day-master element
        generates = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
        controls = {"木": "土", "火": "金", "土": "水", "金": "木", "水": "火"}
        if t_element == b_element:
            relation, polarity, strength, action = (
                "同气", "favor", 0.5, "今天适合推进需要持续力的事，别把同气日耗在杂事上。")
        elif generates.get(b_element) == t_element:
            relation, polarity, strength, action = (
                "泄", "watch", 0.4, "今天适合输出与表达，但别透支；做完一段就收。")
        elif generates.get(t_element) == b_element:
            relation, polarity, strength, action = (
                "生我", "favor", 0.55, "今天适合补基础、找支持；把难事放在有人/有资源的地方。")
        elif controls.get(b_element) == t_element:
            relation, polarity, strength, action = (
                "我克", "watch", 0.45, "今天适合处理可控的硬任务，别把控制欲带到关系里。")
        else:
            relation, polarity, strength, action = (
                "克我", "avoid", 0.6, "今天先降负荷，避免不可逆决定；冲突延后处理。")
        signals.append(_signal(
            f"命理·日主（{bstem}日主）", "lens", "inner", polarity, strength,
            f"今日{stem}日 · {relation}", f"日干五行 → 日主关系（{relation}）",
            action, "只用日干五行，不含完整命盘/大运；启发式，不是命运判断。",
            window="today", hidden=True))
    return signals


@dataclass
class CauseSignal:
    source: str
    family: str
    domain: str
    polarity: str
    strength: float
    carrier: str
    path: str
    action: str
    effect: str
    uncertainty: str
    window: str = ""
    evidence: list[str] = field(default_factory=list)
    hidden: bool = False
    priority: int = 50


def _signal(source, family, domain, polarity, strength, carrier, path, action,
            uncertainty, window="", evidence=None, hidden=False, priority=50) -> CauseSignal:
    return CauseSignal(
        source=source, family=family, domain=domain, polarity=polarity,
        strength=max(0.0, min(1.0, float(strength))), carrier=carrier,
        path=path, action=action,
        effect=f"今日之果：{action}", uncertainty=uncertainty,
        window=window or "today", evidence=list(evidence or []), hidden=hidden,
        priority=int(priority),
    )


def temporal_signals(day: str, features: dict | None = None) -> list[CauseSignal]:
    d = date.fromisoformat(day)
    signals = []
    if d.weekday() in _WEEKDAY:
        label, action = _WEEKDAY[d.weekday()]
        signals.append(_signal(
            "历法·星期", "time", "*", "watch", 0.45,
            f"{label}日", "本周节奏 → 今天的安排", action,
            "只是节奏提示，不是事件预测。", window="today", priority=80))
    label, action = _SEASON[d.month]
    signals.append(_signal(
        "历法·季节", "time", "*", "watch", 0.4,
        f"{label}期", "季节 → 你的节奏", action,
        "季节划分是粗略启发式，不解释个人事件。", window="season", priority=75))
    if d.day >= 25:
        signals.append(_signal(
            "历法·月末", "time", "*", "watch", 0.5,
            "结算窗口", "月末 → 收尾/对账", "先做收尾与对账，别把未闭合项拖到下月。",
            "按自然月近似，不涉及个人命理。", window="month-end", priority=85))
    elif d.day <= 3:
        signals.append(_signal(
            "历法·月初", "time", "*", "favor", 0.45,
            "新周期", "月初 → 定调", "给本月定一个主目标，别同时开太多线。",
            "按自然月近似，不涉及个人命理。", window="month-start", priority=85))
    return signals


def profile_signals(features: dict) -> list[CauseSignal]:
    features = features or {}
    signals = []
    domains = features.get("domains") or {}
    ranked = sorted(domains.items(), key=lambda kv: float(kv[1]), reverse=True)
    for domain, weight in ranked[:2]:
        if float(weight) < 0.35:
            continue
        label = DOMAIN_LABELS.get(domain, domain)
        signals.append(_signal(
            "个人基线", "position", domain, "favor", float(weight),
            f"{label}权重 {float(weight):.2f}", f"长期权重 → {label}",
            f"给{label}留一个明确的时段，别被杂事挤掉。",
            "权重来自档案，不是你今天的实际状态。",
            window="today", evidence=["profile.domains"], priority=70))
    constraints = [c for c in (features.get("constraints") or []) if c]
    if constraints:
        text = "、".join(constraints[:2])
        signals.append(_signal(
            "个人约束", "structure", "*", "avoid", 0.55,
            text, f"约束 → 行动边界", f"先绕开硬约束：{text}；今天不硬碰。",
            "约束是否仍有效，需要你本人确认。", window="today",
            evidence=["profile.constraints"], priority=100))
    load = features.get("load")
    if load is not None:
        try:
            load = float(load)
        except (TypeError, ValueError):
            load = None
        if load is not None and load > 1.0:
            load = min(1.0, load / 3.0)  # questionnaire uses 0–3
    if load is not None and load >= 0.6:
        signals.append(_signal(
            "心身负荷", "psych", "inner", "avoid", min(1.0, load),
            "高负荷", "负荷 → 决策阈值", "降低不可逆承诺，先做可逆的小步。",
            "负荷来自自评，可能低估或高估。", window="today",
            evidence=["profile.load"], priority=90))
    elif load is not None and load <= 0.3:
        signals.append(_signal(
            "心身余量", "psych", "inner", "favor", 0.4,
            "有余量", "余量 → 可推进", "今天可以推进一件平时拖着的重要事。",
            "余量来自自评，不是医学判断。", window="today",
            evidence=["profile.load"], priority=65))
    goals = features.get("goals") or {}
    goal = str(goals.get("goal") or "").strip()
    if goal:
        signals.append(_signal(
            "个人目标", "position", "career", "favor", 0.5,
            goal[:40], f"目标 → 今天的取舍", "先问：今天这件事是否服务于你的主目标。",
            "目标可能已变化，需定期复评。", window="today",
            evidence=["profile.goal"], priority=75))
    phase = str(features.get("phase") or "").strip()
    role = str(features.get("role") or "").strip()
    if phase or role:
        signals.append(_signal(
            "位置/阶段", "structure", "*", "watch", 0.45,
            "、".join(x for x in (role, phase) if x)[:40],
            "角色/阶段 → 取舍", "先按当前角色和阶段安排，不按理想状态硬排。",
            "角色/阶段来自档案，可能已变化。", window="today",
            evidence=["profile.role", "profile.phase"], priority=70))
    relations = [str(x) for x in (features.get("relations") or []) if str(x).strip()]
    if relations:
        signals.append(_signal(
            "关键关系", "relation", "relationship", "watch", 0.4,
            "、".join(relations[:2])[:40], "关系 → 今天的优先级",
            "今天优先处理其中一段关键关系，别广撒网。",
            "关系名单来自档案，不代表今天必须行动。", window="today",
            evidence=["profile.relations"], priority=65))
    change_stage = str(features.get("change_stage") or "").strip()
    if change_stage:
        signals.append(_signal(
            "改变阶段", "psych", "inner", "watch", 0.4,
            change_stage[:30], "阶段 → 动作大小",
            "按当前改变阶段选动作：准备期先收集，行动期先小步。",
            "阶段来自自评，不是临床判断。", window="today",
            evidence=["profile.change_stage"], priority=60))
    return signals


def news_signals(items: list[dict], features: dict, limit: int = 6) -> list[CauseSignal]:
    from .personalize import classify_domains

    features = features or {}
    weights = features.get("domains") or {}
    signals = []
    seen_domains = set()
    for item in items:
        text = f"{item.get('title') or ''} {item.get('summary') or ''}".casefold()
        hits = classify_domains(text)
        for domain in hits:
            weight = float(weights.get(domain, 0.0))
            if weight <= 0:
                continue
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            label = DOMAIN_LABELS.get(domain, domain)
            title = (item.get("title") or "一条信号")[:60]
            source = item.get("feed_name") or "信息源"
            signals.append(_signal(
                f"信息·{source}", "world", domain, "watch",
                min(1.0, 0.35 + weight),
                title, f"{source} → {label}",
                "先核对：这条信号是否真的触及你的项目/决策；不急着下结论。",
                "条目未核验；相关不等于因果。", window="today",
                evidence=[f"item:{item.get('id')}"], hidden=False, priority=60))
            break
        if len(signals) >= limit:
            break
    return signals


def notes_signals(notes: list[dict], features: dict) -> list[CauseSignal]:
    """Turn the latest end-of-report note into an action-family cause."""
    from .personalize import classify_domains

    if not notes:
        return []
    latest = notes[0]
    text = str(latest.get("text") or "").strip()
    if not text:
        return []
    hits = classify_domains(text)
    domain = next(iter(hits), "inner")
    label = DOMAIN_LABELS.get(domain, domain)
    return [_signal(
        "末尾备注", "action", domain, "watch", 0.45,
        text[:50], f"备注 → {label} → 今天", 
        "把这条意向落到今天一个具体动作，别只停在想法。",
        "备注是自述，不是事实；可能只是一时情绪。", window="today",
        evidence=["note.latest"], priority=95)]


def load_lens_file(path) -> list[CauseSignal]:
    """Load normalized signals from an external lens (almanac/astrology/...)."""
    import json
    from pathlib import Path
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw.get("signals") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError("lens 文件必须是数组，或含 signals 数组")
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not row.get("domain") or not row.get("action"):
            continue
        out.append(_signal(
            str(row.get("source") or "外部透镜"), str(row.get("family") or "lens"),
            str(row["domain"]), str(row.get("polarity") or "watch"),
            float(row.get("strength") or 0.4), str(row.get("carrier") or "透镜"),
            str(row.get("path") or "透镜 → 个人"), str(row["action"]),
            str(row.get("uncertainty") or "外部透镜，未核验。"),
            window=str(row.get("window") or "today"),
            evidence=[str(x) for x in (row.get("evidence") or [])],
            hidden=True, priority=int(row.get("priority") or 55)))
    return out


def _score(signal: CauseSignal, features: dict) -> float:
    weights = features.get("domains") or {}
    domain_weight = 0.5 if signal.domain == "*" else float(weights.get(signal.domain, 0.15))
    return round(signal.strength * (0.4 + domain_weight) * (1.0 if signal.action else 0.3), 4)


def build_bearing(features: dict, items: list[dict], day: str,
                  lens_signals: list[CauseSignal] | None = None,
                  max_cards: int = MAX_CARDS, birth: str | None = None,
                  notes: list[dict] | None = None) -> list[dict]:
    """Build at most max_cards personal bearing cards."""
    features = features or {}
    candidates = []
    candidates.extend(temporal_signals(day, features))
    candidates.extend(traditional_signals(day, birth))
    candidates.extend(profile_signals(features))
    candidates.extend(notes_signals(notes or [], features))
    candidates.extend(news_signals(items or [], features))
    candidates.extend(lens_signals or [])
    # drop duplicates by (domain, family)
    seen = set()
    deduped = []
    for signal in sorted(candidates, key=lambda s: _score(s, features), reverse=True):
        key = (signal.domain, signal.family)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(signal)
    # deduped already keeps one (domain, family); do not collapse across families
    candidates = deduped
    # priority ladder: risk/constraint → time → position → world → lens → support
    groups = {
        "risk": [s for s in candidates if
                 (s.family == "structure" and s.polarity == "avoid") or
                 (s.family == "psych" and s.priority >= 90)],
        "time": [s for s in candidates if s.family == "time"],
        "position": [s for s in candidates if s.family == "position"],
        "world": [s for s in candidates if s.family == "world"],
        "lens": [s for s in candidates if s.family == "lens"],
        "support": [s for s in candidates if s.family in ("psych", "relation", "action")],
    }
    selected = []
    for name in ("risk", "time", "support", "position", "world", "lens"):
        group = groups[name]
        group = [s for s in group if s not in selected]
        if group:
            selected.append(max(group, key=lambda s: (s.priority, _score(s, features))))
    for signal in sorted(candidates, key=lambda s: _score(s, features), reverse=True):
        if len(selected) >= max(1, int(max_cards)):
            break
        if signal not in selected:
            selected.append(signal)
    selected = selected[:max(1, int(max_cards))]
    return [{
        "family": s.family, "source": s.source, "domain": s.domain,
        "domain_label": DOMAIN_LABELS.get(s.domain, "通用"),
        "polarity": s.polarity, "polarity_label": POLARITY_LABELS.get(s.polarity, "观察"),
        "strength": s.strength, "window": s.window,
        "carrier": s.carrier, "path": s.path,
        "action": s.action, "effect": s.effect,
        "uncertainty": s.uncertainty, "evidence": s.evidence,
        "hidden": s.hidden, "priority": s.priority,
    } for s in selected]


def render_bearing_section(cards: list[dict], title: str = "今日承因") -> str:
    from html import escape as _escape
    rows = []
    for index, card in enumerate(cards, 1):
        label = _escape(str(card.get("domain_label") or "通用"))
        rows.append(f"""
<section class="bearing">
  <div class="bearing-head">
    <span class="no">承因 {index}</span>
    <span class="tag">{label} · {_escape(str(card.get('polarity_label') or '观察'))} · 强度 {float(card.get('strength') or 0):.2f}</span>
  </div>
  <p class="carrier">{_escape(str(card.get('carrier') or ''))}</p>
  <p class="path">通道：{_escape(str(card.get('path') or ''))}</p>
  <p class="action">{_escape(str(card.get('action') or ''))}</p>
  <p class="meta">时窗：{_escape(str(card.get('window') or 'today'))}　·　可能错：{_escape(str(card.get('uncertainty') or '未核验'))}</p>
</section>""")
    body = "".join(rows) or '<p class="empty">今日无强承因；不硬造。</p>'
    return f'<section class="bearing-block"><h2>{_escape(title)}</h2>{body}</section>'


def render_bearing_html(day: str, cards: list[dict]) -> str:
    from html import escape as _escape
    body = render_bearing_section(cards)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>个人承因 · {_escape(day)}</title>
<style>
body {{ margin:0; background:#f6f2ea; color:#1b1a17; font:16px/1.8 "Songti SC","Noto Serif SC",Georgia,serif; }}
.page {{ max-width:760px; margin:0 auto; padding:48px 24px 64px; }}
h1 {{ font-size:34px; margin:0 0 6px; letter-spacing:.06em; }}
h2 {{ font-size:18px; margin:28px 0 12px; color:#9e3b2e; }}
.date {{ color:#8a8376; font-size:13px; margin-bottom:28px; }}
.bearing {{ border-top:1px solid #d8d0c0; padding:18px 0 20px; }}
.bearing-head {{ display:flex; justify-content:space-between; gap:12px; align-items:baseline; }}
.no {{ color:#9e3b2e; font-size:13px; letter-spacing:.12em; }}
.tag {{ color:#8a8376; font-size:12px; }}
.carrier {{ font-size:20px; margin:8px 0 4px; }}
.path, .meta {{ color:#8a8376; font-size:13px; margin:4px 0; }}
.action {{ margin:10px 0; }}
.empty {{ color:#8a8376; }}
</style></head><body><div class="page">
<h1>个人承因</h1>
<p class="date">{_escape(day)} · 因 → 承 → 果</p>
{body}
</div></body></html>"""
