"""Generate the personal-bearing sample page.

Run from the repo root:

    python samples/make_bearing_sample.py

It uses real public items from reports/data/2026-11-13.json plus a fictional
profile. The causal chains are hand-written for the sample; production uses
DeepSeek synthesis or the deterministic fallback.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from causal_ledger.bearing import (
    DOMAIN_LABELS,
    POLARITY_LABELS,
    build_bearing,
    traditional_signals,
)
from causal_ledger.daily_page import render_daily_page

DAY = "2026-11-13"

FEATURES = {
    "domains": {"career": 0.9, "health": 0.5, "learning": 0.7},
    "constraints": ["时间", "现金流"],
    "load": 2.7,
    "goals": {"goal": "把工作从执行转向定义问题"},
}

SYNTHESIS = {
    "thesis": "今天值得注意的不是单条新闻，而是两件事：AI 工具继续往日常入口挤；平台和供应链的可靠性问题同时冒头。对你来说，先看这两件事会不会碰到你手上的项目。",
    "chains": [
        {
            "title": "AI 工具往入口挤",
            "strength": "中",
            "cause": [
                {"id": 91, "text": "早报把英伟达股权、豆包接入第三方模型、微信短剧入口放在一起，说明大厂在同时调资本叙事和分发入口。"},
                {"id": 92, "text": "Gemini 新模型主打“人人可用”，继续把模型能力往日常入口推。"},
                {"id": 1, "text": "英伟达 CEO 辞职的传闻和澄清，跟算力供给、公司治理缠在一起。"},
            ],
            "effect": [
                "模型竞争不只看谁更强，更看谁先进入日常使用场景。",
                "你做产品/内容时，选平台可能比选模型更影响分发成本。",
            ],
            "reasoning": "三条都在说入口和平台，不是同一件事；只是同一天出现，能互相参照。",
            "doubt": "也可能只是密集发布，没有统一节奏。",
        },
        {
            "title": "平台和供应链的可靠性",
            "strength": "弱",
            "cause": [
                {"id": 71, "text": "流媒体内容越来越多，用户反而更难找到想看的，平均找片时间上升。"},
                {"id": 72, "text": "印度苹果代工厂发生合同工死亡事故，供应链安全和产能集中度再被提起。"},
            ],
            "effect": [
                "规模变大后，发现机制和安全底线会变成瓶颈。",
                "对个人：检查你依赖的平台/供应链有没有单点。",
            ],
            "reasoning": "一条是内容发现，一条是制造安全；都指向“规模变大后可靠性变贵”，但跨行业，只能算弱连接。",
            "doubt": "跨行业类比容易过度概括，不能当同一因果链。",
        },
    ],
}


def add_traditional_lenses(cards: list[dict]) -> None:
    existing = {card["source"] for card in cards}
    for signal in traditional_signals(DAY, "1990-01-01"):
        if signal.family != "lens" or signal.source in existing:
            continue
        cards.append({
            "family": signal.family, "source": signal.source, "domain": signal.domain,
            "domain_label": DOMAIN_LABELS.get(signal.domain, "通用"),
            "polarity": signal.polarity,
            "polarity_label": POLARITY_LABELS.get(signal.polarity, "观察"),
            "strength": signal.strength, "window": signal.window,
            "carrier": signal.carrier, "path": signal.path, "action": signal.action,
            "effect": signal.effect, "uncertainty": signal.uncertainty,
            "evidence": signal.evidence, "hidden": signal.hidden,
        })


def main() -> None:
    items = json.loads((ROOT / "reports/data/2026-11-13.json").read_text(encoding="utf-8"))["items"]
    cards = build_bearing(FEATURES, items, DAY, birth="1990-01-01", max_cards=5)
    add_traditional_lenses(cards)
    html = render_daily_page(items, DAY, title="今日事报 · 样板",
                             synthesis=SYNTHESIS, bearing_cards=cards, personal=True)

    note = (
        '<div style="max-width:1040px;margin:18px auto 0;padding:10px 16px;'
        'border:1px solid #d8d0c0;background:#fff8e8;color:#6b5a3e;'
        'font:13px/1.7 sans-serif;">'
        '样板说明：以下使用真实公开信号 + 虚构档案（事业/健康/学习、负荷、约束），'
        '只演示“今日承因 → 因果链 → 今日之果”的结构，不是对当日的真实判断。'
        '</div>'
    )
    pipeline = f"""
<section style="max-width:1040px;margin:36px auto 0;padding:22px 24px;border:1px solid #d8d0c0;background:#fffdf8;font:13px/1.8 sans-serif;color:#3a352d;">
  <h2 style="margin:0 0 10px;font-size:18px;color:#9e3b2e;">这页是怎么产出的</h2>
  <ol style="margin:0;padding-left:20px;">
    <li><b>输入</b>：日期 2026-11-13；虚构档案（事业 0.9 / 健康 0.5 / 学习 0.7，
        约束“时间/现金流”，负荷 2.7）；示例生日 1990-01-01；14 条公开信号。</li>
    <li><b>档案 → 特征</b>：领域权重、约束、负荷、目标；<b>不读保险箱</b>。</li>
    <li><b>日期 → 时间因</b>：星期/季节/月初月末；<b>黄历透镜</b>只取建除十二神，
        节气按近似边界，不含神煞/宜忌全表。</li>
    <li><b>生日 → 命理透镜</b>：算当日日干与出生日主的五行关系；只用日干一层，
        不是完整命盘。</li>
    <li><b>信号 → 信息因</b>：条目文本按领域关键词归类；相关不等于因果，
        先给“核对”而不是结论。</li>
    <li><b>选择</b>：先风险/约束，再时间窗、个人支撑、位置/主线、世界信息、传统透镜；
        每类取一张，最多 4 张；样板为展示两种传统透镜，放到 7 张。</li>
    <li><b>因源地基</b>：已接时间/档案/信息/备注/建除/日主；
        六爻、六曜、占星、Panchang 是插件位；紫微/完整四柱是明确缺口，
        不用假数据填。查看：<code>bearing --coverage</code>。</li>
    <li><b>因果链</b>：样板链为手写示意；生产版由 DeepSeek 合成，
        或退回确定性聚类。</li>
    <li><b>渲染</b>：同一套 `daily_page`，承因卡在链之前，世界信号在后。</li>
  </ol>
  <p style="margin:12px 0 0;color:#8a8376;">
    说明：建除/日主只作时窗与关系提示，不是事件预测；本页不是你的真实档案。
  </p>
</section>
"""
    html = html.replace('<div class="page">', '<div class="page">' + note, 1)
    index = html.rfind("</div></body>")
    if index != -1:
        html = html[:index] + pipeline + html[index:]
    target = ROOT / "samples/个人承因日报样板.html"
    target.write_text(html, encoding="utf-8")
    print(f"written {target} ({len(html)} chars, {len(cards)} cards)")
    for card in cards:
        print(f"  {card['family']:8s} {card['carrier']} → {card['action'][:28]}")


if __name__ == "__main__":
    main()
