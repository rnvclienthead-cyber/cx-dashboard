"""Мост live-обновлений: фон пишет в Postgres, процессы сайта слушают.

Нужен, потому что SSE-очередь живёт в памяти uvicorn, а фоновые задачи
больше не крутятся в том же процессе.
"""
from __future__ import annotations

import asyncio
import logging
import select
from typing import Iterable

from sqlalchemy import text

from . import broadcaster
from .database import engine

logger = logging.getLogger("cx.sse")

CHANNEL = "cx_frontend"
ALLOWED_MODULES = frozenset({
    "voc", "moderation", "dashboard", "sync", "chats", "reviews",
})


def publish(*modules: str) -> None:
    """Сигнал всем процессам сайта: данные модуля обновились."""
    names = [m.strip() for m in modules if str(m).strip()]
    if not names:
        return
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for name in names:
            conn.execute(text("SELECT pg_notify(:ch, :m)"), {"ch": CHANNEL, "m": name})


def publish_many(modules: Iterable[str]) -> None:
    publish(*list(modules))


async def listen_forever() -> None:
    """В каждом процессе API: LISTEN → broadcaster (вкладки сотрудников)."""
    import psycopg2.extensions

    while True:
        raw = None
        try:
            raw = engine.raw_connection()
            raw.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            cur = raw.cursor()
            cur.execute(f"LISTEN {CHANNEL}")
            logger.info("sse_bridge: listening on %s", CHANNEL)
            while True:
                ready, _, _ = await asyncio.to_thread(select.select, [raw], [], [], 15.0)
                if not ready:
                    continue
                raw.poll()
                while raw.notifies:
                    note = raw.notifies.pop(0)
                    module = (note.payload or "").strip()
                    if module:
                        await broadcaster.broadcast(module)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("sse_bridge listen: %s", e)
            await asyncio.sleep(3)
        finally:
            if raw is not None:
                try:
                    raw.close()
                except Exception:
                    pass
