"""Наблюдение за «зависанием» сайта: лаг event loop и медленные HTTP-запросы.

Если фоновая задача на секунды блокирует цикл uvicorn, страницы перестают
отвечать, хотя сервер «живой». Этот модуль измеряет задержку и пишет в лог.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("cx.loop")

_stats: dict[str, Any] = {
    "loop_lag_ms": 0.0,
    "loop_lag_max_ms": 0.0,
    "slow_requests": 0,
}

SKIP_SLOW_PREFIXES = (
    "/api/v1/events",
    "/api/v1/health",
    "/connection/",
)

SLOW_REQUEST_SEC = 2.0
LAG_WARN_MS = 500.0


def snapshot() -> dict[str, Any]:
    return dict(_stats)


def note_lag_ms(lag_ms: float) -> None:
    lag = max(0.0, float(lag_ms))
    _stats["loop_lag_ms"] = round(lag, 1)
    prev = float(_stats.get("loop_lag_max_ms") or 0)
    if lag > prev:
        _stats["loop_lag_max_ms"] = round(lag, 1)


def note_slow_request() -> None:
    _stats["slow_requests"] = int(_stats.get("slow_requests") or 0) + 1


def classify_lag(lag_ms: float, warn_ms: float = LAG_WARN_MS) -> str:
    if lag_ms >= 2000:
        return "critical"
    if lag_ms >= warn_ms:
        return "warn"
    return "ok"


def classify_http_probe(ok: bool, elapsed_ms: float, slow_ms: float = 2500) -> str:
    """down / slow / ok — «сайт лёг» vs «сайт тормозит»."""
    if not ok:
        return "down"
    if elapsed_ms >= slow_ms:
        return "slow"
    return "ok"


def classify_ram(percent: float, threshold: float = 90) -> bool:
    return float(percent) >= threshold


def classify_disk(free_gb: float, min_gb: float = 2.0) -> bool:
    return float(free_gb) < min_gb


class SlowRequestMiddleware:
    """Пишет в лог HTTP дольше SLOW_REQUEST_SEC. Долгие SSE не считает."""

    def __init__(self, app, threshold_s: float = SLOW_REQUEST_SEC):
        self.app = app
        self.threshold_s = threshold_s

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        if path.startswith(SKIP_SLOW_PREFIXES):
            await self.app(scope, receive, send)
            return
        t0 = time.perf_counter()
        try:
            await self.app(scope, receive, send)
        finally:
            dt = time.perf_counter() - t0
            if dt >= self.threshold_s:
                note_slow_request()
                logger.warning(
                    "slow request %.2fs %s %s",
                    dt,
                    scope.get("method"),
                    path,
                )


async def start_watchdog(interval_s: float = 1.0) -> None:
    """Каждую секунду смотрит, насколько цикл опоздал с пробуждением."""
    loop = asyncio.get_running_loop()
    while True:
        t0 = loop.time()
        await asyncio.sleep(interval_s)
        lag_ms = (loop.time() - t0 - interval_s) * 1000.0
        note_lag_ms(lag_ms)
        level = classify_lag(lag_ms)
        if level == "critical":
            logger.error("event loop lag %.0f ms — сайт в эти секунды не отвечает", lag_ms)
        elif level == "warn":
            logger.warning("event loop lag %.0f ms", lag_ms)
