"""
Ускорение раздела чатов без урезания функций:
  - короткий TTL-кэш списка чатов;
  - публикация точечных обновлений в Centrifugo;
  - фоновый предрасчёт подсказки бота при новом сообщении клиента.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from . import centrifugo

logger = logging.getLogger("chat_realtime")

CHANNEL = "chats"
_CACHE_TTL_SEC = 8.0
_cache_lock = threading.Lock()
_queue_cache: dict[str, tuple[float, Any]] = {}
_suggest_inflight: set[str] = set()
_suggest_lock = threading.Lock()


def invalidate_chats_queue_cache(mp: Optional[str] = None) -> None:
    """Сброс кэша списка. mp=None — все площадки."""
    with _cache_lock:
        if mp is None or mp == "all":
            _queue_cache.clear()
            return
        _queue_cache.pop(mp, None)
        _queue_cache.pop("all", None)


def get_cached_chats_queue(mp: str):
    key = (mp or "all").lower()
    with _cache_lock:
        hit = _queue_cache.get(key)
        if not hit:
            return None
        ts, payload = hit
        if time.monotonic() - ts > _CACHE_TTL_SEC:
            _queue_cache.pop(key, None)
            return None
        return payload


def set_cached_chats_queue(mp: str, payload) -> None:
    key = (mp or "all").lower()
    with _cache_lock:
        _queue_cache[key] = (time.monotonic(), payload)


def publish_chat_updated(chat: dict) -> bool:
    """Точечное обновление строки чата в UI."""
    if not chat or not chat.get("chat_id"):
        return False
    invalidate_chats_queue_cache(chat.get("mp"))
    payload = {
        "type": "chat_updated",
        "chat": {
            "chat_id": str(chat.get("chat_id")),
            "mp": (chat.get("mp") or "wb").lower(),
            "contact": chat.get("contact") or chat.get("client_name") or "",
            "sku": chat.get("sku") or chat.get("good_name") or "",
            "nm_id": chat.get("nm_id"),
            "body": chat.get("body") or chat.get("last_message") or "",
            "last_message": chat.get("last_message") or chat.get("body") or "",
            "last_ts": chat.get("last_ts"),
            "needs_answer": bool(chat.get("needs_answer")),
            "last_sender": chat.get("last_sender") or "",
            "product_photo": chat.get("product_photo"),
            "cabinet_url": chat.get("cabinet_url"),
            "item_type": "chat",
        },
    }
    return centrifugo.publish(CHANNEL, payload)


def publish_chat_removed(chat_id: str, mp: str = "wb") -> bool:
    """Чат ушёл в архив / закрыт — убрать из списка без полной перезагрузки."""
    if not chat_id:
        return False
    invalidate_chats_queue_cache(mp)
    return centrifugo.publish(CHANNEL, {
        "type": "chat_removed",
        "chat_id": str(chat_id),
        "mp": (mp or "wb").lower(),
    })


def publish_chat_seen(chat_id: str, mp: str, seen_ts: int) -> bool:
    """Кто-то открыл чат — для всей команды он больше не «новый»."""
    if not chat_id:
        return False
    invalidate_chats_queue_cache(mp)
    return centrifugo.publish(CHANNEL, {
        "type": "chat_seen",
        "chat_id": str(chat_id),
        "mp": (mp or "wb").lower(),
        "seen_ts": int(seen_ts or 0),
    })


def publish_chats_seen_all(mp: str = "all") -> bool:
    """«Все прочитано» — сбросить «новые» у всей команды."""
    invalidate_chats_queue_cache(mp)
    return centrifugo.publish(CHANNEL, {
        "type": "chats_seen_all",
        "mp": (mp or "all").lower(),
    })


def schedule_suggest_precompute(mp: str, chat_id: str) -> None:
    """Фоном считает подсказку и кладёт в cx_chat_bot_suggest_cache."""
    mp = (mp or "wb").lower()
    cid = str(chat_id or "").strip()
    if not cid or mp not in ("wb", "ozon", "ym"):
        return
    key = f"{mp}:{cid}"
    with _suggest_lock:
        if key in _suggest_inflight:
            return
        _suggest_inflight.add(key)

    def _run():
        try:
            from .database import db_session
            from . import chat_bot
            with db_session() as db:
                chat_bot.suggest_for_chat(db, cid, force_ai=False, use_cache=False, mp=mp)
        except Exception as e:
            logger.warning("[ChatRealtime] suggest precompute %s/%s: %s", mp, cid, e)
        finally:
            with _suggest_lock:
                _suggest_inflight.discard(key)

    threading.Thread(target=_run, daemon=True, name=f"suggest-{key[:40]}").start()
