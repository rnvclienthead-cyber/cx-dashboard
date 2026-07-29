"""
Публикация событий в Centrifugo.
Используется из воркеров для real-time обновлений на фронте.
"""
import os
import logging
import requests

logger = logging.getLogger("centrifugo")


def _api_url() -> str:
    return os.getenv("CENTRIFUGO_API_URL", "http://127.0.0.1:8100")


def _api_key() -> str:
    return os.getenv("CENTRIFUGO_API_KEY", "")


def publish(channel: str, data: dict) -> bool:
    """Публикует событие в канал Centrifugo. Возвращает True при успехе."""
    key = _api_key()
    if not key:
        return False
    try:
        r = requests.post(
            f"{_api_url()}/api/publish",
            json={"channel": channel, "data": data},
            headers={"X-API-Key": key, "Content-Type": "application/json"},
            timeout=3,
        )
        return r.ok
    except Exception as e:
        logger.warning("[Centrifugo] publish failed: %s", e)
        return False
