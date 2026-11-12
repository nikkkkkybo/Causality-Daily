"""Weekly page and daily retention (zero-cost cloud)."""
from __future__ import annotations

from causal_ledger.weekly_page import render_weekly_page


def items(start: str = "2026-11-02", end: str = "2026-11-08"):
    rows = []
    for index, day in enumerate(("2026-11-02", "2026-11-03", "2026-11-04",
                                 "2026-11-05", "2026-11-06", "2026-11-07", "2026-11-08")):
        rows.append({"id": index + 1, "title": f"AI 模型工具进展 {day}", "summary": "模型与软件",
                     "feed_name": "源A", "published_at": day + "T01:00:00+00:00",
                     "url": f"https://example.org/{index}"})
        rows.append({"id": index + 20, "title": f"价格与就业信号 {day}", "summary": "价格与招聘",
                     "feed_name": "源B", "published_at": day + "T02:00:00+00:00",
                     "url": f"https://example.org/b{index}"})
    return rows


def test_weekly_page_has_week_scope_and_sources():
    text = render_weekly_page(items(), "2026-11-02", "2026-11-08")
    assert "本周事报" in text
    assert "本周算法链" in text
    assert "2026-11-02 ~ 2026-11-08" in text
    assert "本周信号清单（保留出处）" in text
    assert "这一周" in text
    assert "https://example.org/0" in text
