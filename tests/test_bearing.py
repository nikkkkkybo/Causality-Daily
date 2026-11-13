"""Personal bearing: normalized cause signals and personal cards."""
from __future__ import annotations

import json

from causal_ledger import bearing


FEATURES = {
    "domains": {"career": 0.9, "health": 0.4, "learning": 0.3},
    "constraints": ["时间"],
    "load": 3,
    "goals": {"goal": "把工作从执行转向定义问题"},
}


def test_temporal_signals_are_time_only():
    signals = bearing.temporal_signals("2026-11-13")
    assert signals
    assert all(s.family == "time" for s in signals)
    assert any("收束" in s.carrier for s in signals)  # Friday
    assert any("秋" in s.uncertainty or "季节" in s.source for s in signals)


def test_profile_signals_include_domain_constraint_and_load():
    signals = bearing.profile_signals(FEATURES)
    families = {s.family for s in signals}
    assert {"position", "structure", "psych"} <= families
    assert any(s.domain == "career" for s in signals)
    assert any("时间" in s.carrier for s in signals)


def test_build_bearing_caps_and_dedupes():
    items = [
        {"id": 1, "title": "AI 产品与工作效率", "summary": "职业与工具",
         "feed_name": "源A"},
        {"id": 2, "title": "健康与睡眠", "summary": "恢复", "feed_name": "源B"},
    ]
    cards = bearing.build_bearing(FEATURES, items, "2026-11-13")
    assert 1 <= len(cards) <= 3
    domains = [card["domain"] for card in cards]
    assert len(domains) == len(set(domains))
    assert any(card["family"] == "time" for card in cards)
    assert any(card["family"] in ("position", "structure", "psych") for card in cards)


def test_day_pillar_and_traditional_lens():
    assert bearing.day_pillar("2000-01-01")[:2] == ("戊", "午")
    signals = bearing.traditional_signals("2026-11-13", "1990-01-01")
    assert signals and all(s.hidden for s in signals)
    assert any("建除" in s.source for s in signals)
    assert any("命理" in s.source for s in signals)
    assert all("不是事件预测" in s.uncertainty or "不是命运" in s.uncertainty for s in signals)


def test_lens_file_and_html(tmp_path):
    lens = tmp_path / "lens.json"
    lens.write_text(json.dumps({"signals": [{
        "source": "示例透镜", "family": "lens", "domain": "career",
        "polarity": "favor", "strength": 0.8, "carrier": "项目节点",
        "path": "透镜 → 项目", "action": "先做可逆的小验证。",
        "uncertainty": "透镜是半黑盒。",
    }]}, ensure_ascii=False), encoding="utf-8")
    signals = bearing.load_lens_file(lens)
    assert signals and signals[0].hidden is True
    cards = bearing.build_bearing(FEATURES, [], "2026-11-13", signals)
    html = bearing.render_bearing_html("2026-11-13", cards)
    assert "个人承因" in html
    assert "今日之果" in html or "action" in html
