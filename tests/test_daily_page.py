"""The current 今日事报 causal-chain page (not the deprecated card brief)."""
from __future__ import annotations

from causal_ledger.daily_page import render_daily_page


def items():
    return [
        {"id": 1, "title": "AI 模型工具发布", "summary": "模型与产品",
         "feed_name": "源A", "published_at": "2026-11-13T01:00:00+00:00",
         "url": "https://example.org/a"},
        {"id": 2, "title": "平台被指伪造内容", "summary": "信息与核验",
         "feed_name": "源B", "published_at": "2026-11-13T02:00:00+00:00",
         "url": "https://example.org/b"},
        {"id": 3, "title": "招聘与价格信号", "summary": "经济与就业",
         "feed_name": "源C", "published_at": "2026-11-13T03:00:00+00:00",
         "url": "https://example.org/c"},
        {"id": 4, "title": "海外谈判与供应链", "summary": "世界与长周期",
         "feed_name": "源D", "published_at": "2026-11-13T04:00:00+00:00",
         "url": "https://example.org/d"},
    ]


def test_daily_page_is_new_causal_format():
    text = render_daily_page(items(), "2026-11-13")
    assert "<title>今日事报 · 2026-11-13</title>" in text
    assert "今日事报" in text and "链一" in text
    assert "因" in text and "果" in text
    assert "我的链路" in text and "可能看错" in text
    assert "今日算法链" in text
    assert "台账状态" not in text and "待你处理" not in text
    assert "未接账" not in text and "接账" not in text
    assert "https://example.org/a" in text


def test_daily_page_escapes_and_rejects_unsafe_links():
    text = render_daily_page([
        {"id": 9, "title": "<script>alert(1)</script>", "summary": "AI 工具",
         "feed_name": "<b>源</b>", "published_at": "2026-11-13T00:00:00Z",
         "url": "javascript:alert(1)"},
    ], "2026-11-13")
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text
    assert 'href="javascript:' not in text
    assert "原文" not in text  # unsafe link is dropped


def test_daily_page_empty_is_honest():
    text = render_daily_page([], "2026-11-13")
    assert "今日事报" in text
    assert "没有可用候选" in text
    assert "不强行造链" in text
