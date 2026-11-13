"""Coverage registry: breadth is visible, gaps are explicit."""
from __future__ import annotations

from causal_ledger import cause_sources


def test_coverage_has_all_families_and_explicit_gaps():
    info = cause_sources.coverage()
    assert info["families"] == 9
    assert info["sources"] >= 18
    assert info["counts"].get("implemented", 0) >= 10
    assert info["counts"].get("simplified", 0) >= 2
    assert info["counts"].get("plugin", 0) >= 2
    assert info["counts"].get("gap", 0) >= 1
    assert {item["id"] for item in info["gaps"]} >= {"traditional.ziwei", "traditional.bazi_full"}


def test_coverage_text_shows_status_and_families():
    text = cause_sources.format_coverage()
    assert "时间/历法" in text
    assert "传统时间系统" in text
    assert "implemented" in text and "gap" in text
    assert "紫微斗数" in text
    assert "六爻/小六壬" in text
