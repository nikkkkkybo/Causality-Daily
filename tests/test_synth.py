"""DeepSeek synthesis: JSON contract, rewrite rendering and fallback."""
from __future__ import annotations

import json

import pytest

from causal_ledger import synth
from causal_ledger.daily_page import render_daily_page


ITEMS = [
    {"id": 1, "title": "AI model tool launch", "summary": "model and product",
     "feed_name": "源A", "published_at": "2026-11-13T01:00:00Z",
     "url": "https://example.org/a"},
    {"id": 2, "title": "平台被指伪造内容", "summary": "核验与隐私",
     "feed_name": "源B", "published_at": "2026-11-13T02:00:00Z",
     "url": "https://example.org/b"},
]

MODEL_JSON = {
    "thesis": "今天真正的信号不是单条发布，而是工具与信任同时在被重新定价。",
    "chains": [{
        "title": "工具与信任",
        "strength": "中",
        "cause": [
            {"id": 1, "text": "一家模型公司发布了新的开发工具，重点是降低使用门槛。"},
            {"id": 2, "text": "另一条消息则在讨论平台内容被伪造后的核验困难。"},
        ],
        "effect": ["工具越便宜，辨认内容真假的成本越突出。",
                   "下一轮争点可能从“能不能生成”转向“能不能追溯”。"],
        "reasoning": "两条来自不同领域，但都在改变“谁可以做、做完能不能被相信”。",
        "doubt": "也可能只是同一天的两条独立新闻，没有传导关系。",
    }],
}


class FakeResponse:
    status = 200
    def __init__(self, payload): self.payload = payload
    def read(self): return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")
    def __enter__(self): return self
    def __exit__(self, *args): return False


def opener_for(payload, seen=None):
    def opener(request, timeout=90):
        if seen is not None:
            seen["url"] = request.full_url
            seen["auth"] = request.headers.get("Authorization")
            seen["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(payload)
    return opener


def test_synthesize_valid_json_and_request_shape():
    seen = {}
    envelope = {"choices": [{"message": {"content": json.dumps(MODEL_JSON, ensure_ascii=False)}}]}
    result = synth.synthesize(ITEMS, day="2026-11-13", api_key="sk-test",
                             opener=opener_for(envelope, seen))
    assert result["thesis"].startswith("今天")
    assert result["chains"][0]["cause"][0]["id"] == 1
    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "deepseek-chat"


def test_synthesize_rejects_broken_json_and_invalid_ids():
    broken = {"choices": [{"message": {"content": "这不是 JSON"}}]}
    with pytest.raises(synth.SynthesisError):
        synth.synthesize(ITEMS, day="2026-11-13", api_key="sk-test", opener=opener_for(broken))
    invalid = {"thesis": "x", "chains": [{"title": "x", "strength": "中",
                                         "cause": [{"id": 999, "text": "不存在"}],
                                         "effect": ["x"], "reasoning": "x", "doubt": "x"}]}
    with pytest.raises(synth.SynthesisError):
        synth.validate_synthesis(invalid, ITEMS)
    single = {"thesis": "x", "chains": [{"title": "x", "strength": "中",
                                        "cause": [{"id": 1, "text": "单条"}],
                                        "effect": ["x"], "reasoning": "x", "doubt": "x"}]}
    with pytest.raises(synth.SynthesisError):
        synth.validate_synthesis(single, ITEMS)


def test_daily_page_uses_synthesis_rewrite():
    text = render_daily_page(ITEMS, "2026-11-13", synthesis=MODEL_JSON)
    assert "今天真正的信号" in text
    assert "一家模型公司发布了新的开发工具" in text
    assert "AI model tool launch" not in text  # raw English title is replaced by the rewrite
    assert "https://example.org/a" in text
    assert "DeepSeek 二次翻译/整理" in text
    assert "我的链路" in text and "可能看错" in text
