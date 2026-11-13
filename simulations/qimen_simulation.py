"""Self-audit simulation: a fictional person + an opaque "AI Qimen" lens.

This is a *toy* simulation, not evidence that the system predicts outcomes.
It exists to expose structural problems: card order, repetition, coverage,
overload, and whether the engine can consume an external Qimen-style provider.

It does NOT read or modify D:\\ai奇门. The Qimen provider here is a local
simulator with normalized signals, explicitly labeled as simulated.
"""
from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from causal_ledger.bearing import CauseSignal, build_bearing

OUT = ROOT / "simulations"
DAYS = 30
START = date(2026, 11, 1)

PERSON = {
    "name": "林岚（虚构）",
    "domains": {"career": 0.85, "health": 0.55, "learning": 0.6, "relationship": 0.45},
    "constraints": ["时间", "现金流"],
    "load": 0.65,
    "goals": {"goal": "把工作从执行转向定义问题"},
    "role": "产品负责人",
    "phase": "转型期",
    "relations": ["直属上级", "核心客户"],
    "change_stage": "准备",
}

DOORS = {
    "开": ("favor", "开一个可逆的小口子，不押大注。"),
    "休": ("watch", "今天适合休整，不硬推。"),
    "生": ("favor", "补基础、找支持，把难事放到有资源的地方。"),
    "伤": ("avoid", "避免硬碰和正面冲突。"),
    "杜": ("avoid", "先收口，不扩张。"),
    "景": ("watch", "先观察，不急着定案。"),
    "死": ("avoid", "暂缓不可逆决定。"),
    "惊": ("watch", "先核对信息，别被消息带跑。"),
}
DOMAINS = ["career", "wealth", "health", "relationship", "learning", "inner"]

EVENT_TEMPLATES = [
    ("行业预算收紧，项目审批变慢", "career", "模拟市场"),
    ("客户临时变更交付范围", "relationship", "模拟客户"),
    ("回款比预期晚两周", "wealth", "模拟财务"),
    ("连续两晚睡眠不足", "health", "模拟生活"),
    ("新工具发布，团队想试", "learning", "模拟技术"),
    ("上级要求本周给方向判断", "career", "模拟公司"),
    ("家里临时需要处理一件事", "family", "模拟家庭"),
]


def qimen_lens(day: date) -> list[CauseSignal]:
    """A deterministic stand-in for an external AI Qimen provider."""
    seed = int(day.strftime("%Y%m%d"))
    rng = random.Random(seed)
    door = rng.choice(list(DOORS))
    polarity, action = DOORS[door]
    domain = rng.choice(DOMAINS)
    return [CauseSignal(
        source="AI奇门模拟", family="lens", domain=domain, polarity=polarity,
        strength=0.55, carrier=f"{door}门（模拟）", path=f"时盘 → {door}门 → {domain}",
        action=action, effect=f"今日之果：{action}",
        uncertainty="本地模拟器，不是真实奇门排盘；只用于测试承因链路。",
        window="today", evidence=["qimen.sim"], hidden=True, priority=60,
    )]


def daily_items(day: date) -> list[dict]:
    rng = random.Random(int(day.strftime("%Y%m%d")) + 7)
    picked = rng.sample(EVENT_TEMPLATES, k=3)
    return [{
        "id": int(day.strftime("%Y%m%d")) * 10 + i,
        "title": title, "summary": title,
        "feed_name": source, "published_at": day.isoformat() + "T08:00:00+08:00",
        "url": f"https://example.invalid/{day.isoformat()}/{i}",
    } for i, (title, _domain, source) in enumerate(picked)]


@dataclass
class State:
    load: float
    progress: float = 0.0
    risk_events: int = 0
    cards: list[list[dict]] = field(default_factory=list)


def _apply(cards: list[dict], state: State, compliance: float = 0.8, rng=None) -> None:
    rng = rng or random.Random(0)
    for card in cards:
        if rng.random() > compliance:
            continue
        action = card.get("action") or ""
        if "不可逆" in action or "暂缓" in action:
            state.risk_events = max(0, state.risk_events - 1)
            state.load = max(0.15, state.load - 0.015)
        if ("留一个明确的时段" in action or "今天能交付" in action or
                "第一段专注" in action or "只推进" in action):
            state.progress += 1.5
        if "收口" in action or "收束" in action or "先交付" in action:
            state.progress += 0.8
            state.load = max(0.1, state.load - 0.02)
        if ("降负荷" in action or "先做可逆" in action or "砍" in action or
                "不接新承诺" in action or "休息" in action or "目标砍半" in action):
            state.load = max(0.1, state.load - 0.03)


def run(mode: str, seed: int = 42) -> State:
    rng = random.Random(seed)
    state = State(load=float(PERSON["load"]))
    recent_actions: list[str] = []
    for offset in range(DAYS):
        day = START + timedelta(days=offset)
        features = dict(PERSON)
        features["load"] = state.load
        items = daily_items(day)
        notes = ([{"day": (day - timedelta(days=1)).isoformat(), "text": "这周想多留意思路"}]
                 if offset % 7 == 6 else [])
        if mode == "bearing":
            cards = build_bearing(features, items, day.isoformat(),
                                  lens_signals=qimen_lens(day), notes=notes,
                                  birth="1990-01-01", max_cards=4,
                                  recent_actions=recent_actions[-6:])
            state.cards.append(cards)
            recent_actions.extend(card["action"] for card in cards)
            _apply(cards, state, rng=rng)
        else:
            state.cards.append([])
        # ordinary day drift
        state.load = min(1.0, state.load + rng.uniform(0.005, 0.03))
        state.progress += rng.uniform(0.4, 1.0)
        risk = 0.12 + state.load * 0.25
        if rng.random() < risk:
            state.risk_events += 1
    return state


def audit(state: State) -> dict:
    flat = [card for cards in state.cards for card in cards]
    counts = {}
    for card in flat:
        counts[card["family"]] = counts.get(card["family"], 0) + 1
    family_days = {}
    for cards in state.cards:
        for family in {card["family"] for card in cards}:
            family_days[family] = family_days.get(family, 0) + 1
    actions = [card["action"] for card in flat]
    top_carriers = [cards[0]["carrier"] for cards in state.cards if cards]
    return {
        "days": DAYS,
        "cards": len(flat),
        "avg_cards": round(len(flat) / DAYS, 2),
        "families": counts,
        "family_days": family_days,
        "empty_days": sum(1 for cards in state.cards if not cards),
        "repeat_action_rate": round(1 - len(set(actions)) / len(actions), 3) if actions else 0,
        "top_carrier_changes": len(set(top_carriers)),
        "risk_first_days": sum(1 for cards in state.cards
                               if cards and cards[0]["family"] in ("structure", "psych")
                               and cards[0]["polarity"] == "avoid"),
        "load": round(state.load, 2),
        "progress": round(state.progress, 1),
        "risk_events": state.risk_events,
    }


def main() -> None:
    OUT.mkdir(exist_ok=True)
    baseline = run("baseline")
    bearing = run("bearing")
    report = {
        "person": PERSON,
        "note": "虚构人物 + 本地模拟透镜；不读取/修改 D:\\ai奇门，不代表真实效果。",
        "baseline": audit(baseline),
        "bearing": audit(bearing),
    }
    (OUT / "qimen_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    lines = [
        "# AI 奇门模拟：虚构人物 30 天自审查",
        "",
        f"虚构人物：{PERSON['name']}，产品负责人，转型期；时间/现金流约束；负荷 0.65。",
        "",
        "> 这是玩具模拟：AI 奇门透镜是本地模拟器，不读 `D:\\ai奇门`，不证明真实效果。",
        "",
        "## 结果",
        "",
        "| 指标 | 无承因 | 有承因 |",
        "|---|---:|---:|",
    ]
    b, w = report["baseline"], report["bearing"]
    for key in ("avg_cards", "empty_days", "repeat_action_rate", "top_carrier_changes",
                "risk_first_days", "load", "progress", "risk_events"):
        lines.append(f"| {key} | {b[key]} | {w[key]} |")
    lines += [
        "",
        "## 覆盖",
        "",
        "| 家族 | 出现天数 |",
        "|---|---:|",
    ]
    for family, count in sorted(w.get("family_days", {}).items(), key=lambda kv: -kv[1]):
        lines.append(f"| {family} | {count} |")
    lines += [
        "",
        "## 发现",
        "",
        f"- 每天固定 4 卡，没有空窗；但 4 卡是硬上限，容易把世界信息/传统透镜挤掉。",
        f"- 风险优先日 {w['risk_first_days']}/30：高负荷 + 奇门模拟的 avoid 信号让风险卡长期占据首位。",
        f"- 动作重复率 {w['repeat_action_rate']*100:.1f}%：模板仍偏少，连续几天会出现同一句。",
        f"- 顶部载体只换 {w['top_carrier_changes']} 次：承因排序对日变化的敏感度还不够。",
        f"- 玩具效果：负荷 {b['load']}→{w['load']}，进度 {b['progress']}→{w['progress']}，风险事件 {b['risk_events']}→{w['risk_events']}；这只说明模拟器内部逻辑，不证明真实效果。",
        "",
        "## 结论",
        "",
        "- 地基（因源覆盖）已经显式；短板在**轮换、变体、上限分配**，不在“缺一个玄学公式”。",
        "- 下一步应优先做：按天轮换首位、扩大动作变体、把世界/透镜设为隔天必选槽，而不是继续加卡。",
    ]
    (OUT / "qimen_report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
