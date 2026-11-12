"""Optional push channels: ntfy / Bark / Telegram / ServerChan, auto-selection."""
from __future__ import annotations

import json

from causal_ledger import push


class FakeResponse:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): return False


def opener_for(seen):
    def opener(request, timeout=15):
        seen["url"] = request.full_url
        seen["data"] = json.loads(request.data.decode("utf-8")) if request.data else None
        return FakeResponse()
    return opener


def test_ntfy_posts_json():
    seen = {}
    result = push.send_ntfy("my-topic_1", "标题", "正文", opener=opener_for(seen))
    assert result["sent"] is True and result["channel"] == "ntfy"
    assert seen["url"] == "https://ntfy.sh/"
    assert seen["data"]["topic"] == "my-topic_1"
    assert seen["data"]["title"] == "标题"


def test_ntfy_rejects_bad_topic():
    assert push.send_ntfy("bad topic!", "t", "b", opener=opener_for({}))["sent"] is False


def test_bark_posts_to_key_url():
    seen = {}
    result = push.send_bark("ABC123", "标题", "正文", opener=opener_for(seen))
    assert result["sent"] is True and result["channel"] == "bark"
    assert seen["url"] == "https://api.day.app/ABC123"
    assert seen["data"]["body"] == "正文"


def test_telegram_requires_valid_token_and_chat():
    seen = {}
    result = push.send_telegram("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ", "42", "正文",
                                opener=opener_for(seen))
    assert result["sent"] is True and result["channel"] == "telegram"
    assert seen["url"].endswith("/sendMessage")
    assert seen["data"]["chat_id"] == "42"
    assert push.send_telegram("bad", "42", "x", opener=opener_for({}))["sent"] is False


def test_send_push_auto_selects_configured_channel():
    seen = {}
    result = push.send_push("auto", "t", "b", environ={"NTFY_TOPIC": "abc"},
                            opener=opener_for(seen))
    assert result["sent"] is True and result["channel"] == "ntfy"
    skipped = push.send_push("auto", "t", "b", environ={}, opener=opener_for({}))
    assert skipped["sent"] is False and "没有配置" in skipped["reason"]
    assert push.send_push("none", "t", "b", environ={})["sent"] is False


def test_send_push_explicit_channel():
    seen = {}
    result = push.send_push("bark", "t", "b", environ={"BARK_KEY": "KEY"},
                            opener=opener_for(seen))
    assert result["channel"] == "bark" and seen["url"].endswith("/KEY")
