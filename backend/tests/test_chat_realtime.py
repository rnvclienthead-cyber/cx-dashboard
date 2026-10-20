"""Кэш очереди чатов и публикация realtime-событий."""
from unittest.mock import patch

from backend import chat_realtime


def setup_function():
    chat_realtime.invalidate_chats_queue_cache()


def test_queue_cache_roundtrip():
    payload = {"items": [{"id": "c1"}], "counts": {"total": 1}}
    assert chat_realtime.get_cached_chats_queue("wb") is None
    chat_realtime.set_cached_chats_queue("wb", payload)
    hit = chat_realtime.get_cached_chats_queue("wb")
    assert hit is not None
    assert hit["counts"]["total"] == 1
    chat_realtime.invalidate_chats_queue_cache("wb")
    assert chat_realtime.get_cached_chats_queue("wb") is None


def test_publish_chat_updated_invalidates_cache():
    chat_realtime.set_cached_chats_queue("ozon", {"items": [], "counts": {}})
    with patch.object(chat_realtime.centrifugo, "publish", return_value=True) as pub:
        ok = chat_realtime.publish_chat_updated({
            "chat_id": "oz-1",
            "mp": "ozon",
            "last_message": "привет",
            "needs_answer": True,
        })
    assert ok is True
    assert chat_realtime.get_cached_chats_queue("ozon") is None
    assert pub.call_args[0][0] == "chats"
    body = pub.call_args[0][1]
    assert body["type"] == "chat_updated"
    assert body["chat"]["chat_id"] == "oz-1"
    assert body["chat"]["mp"] == "ozon"


def test_publish_chat_removed():
    with patch.object(chat_realtime.centrifugo, "publish", return_value=True) as pub:
        ok = chat_realtime.publish_chat_removed("ym-9", "ym")
    assert ok is True
    assert pub.call_args[0][1]["type"] == "chat_removed"
    assert pub.call_args[0][1]["chat_id"] == "ym-9"
    assert pub.call_args[0][1]["mp"] == "ym"


def test_publish_chat_seen():
    chat_realtime.set_cached_chats_queue("wb", {"items": [], "counts": {}})
    with patch.object(chat_realtime.centrifugo, "publish", return_value=True) as pub:
        ok = chat_realtime.publish_chat_seen("1:abc", "wb", 123)
    assert ok is True
    assert chat_realtime.get_cached_chats_queue("wb") is None
    body = pub.call_args[0][1]
    assert body["type"] == "chat_seen"
    assert body["chat_id"] == "1:abc"
    assert body["seen_ts"] == 123


def test_publish_chats_seen_all():
    with patch.object(chat_realtime.centrifugo, "publish", return_value=True) as pub:
        ok = chat_realtime.publish_chats_seen_all("ozon")
    assert ok is True
    assert pub.call_args[0][1]["type"] == "chats_seen_all"
    assert pub.call_args[0][1]["mp"] == "ozon"
