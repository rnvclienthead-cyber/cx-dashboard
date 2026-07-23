"""
Глобальный SSE-broadcaster.
Каждый подключённый клиент получает свою asyncio.Queue.
Воркеры вызывают broadcast(module) после завершения синка.
"""
import asyncio
import json
from typing import Set

_queues: Set[asyncio.Queue] = set()


def register() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _queues.add(q)
    return q


def unregister(q: asyncio.Queue) -> None:
    _queues.discard(q)


async def broadcast(module: str) -> None:
    """Отправить событие data_updated всем подключённым клиентам."""
    if not _queues:
        return
    payload = json.dumps({"type": "data_updated", "module": module})
    dead: list[asyncio.Queue] = []
    for q in list(_queues):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _queues.discard(q)
