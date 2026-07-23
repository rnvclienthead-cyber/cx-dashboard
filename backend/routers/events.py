"""SSE-эндпоинт для live-обновлений фронтенда."""
import asyncio
from fastapi import APIRouter, Query, HTTPException, status
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt

from .auth import SECRET_KEY, ALGORITHM
from .. import broadcaster

router = APIRouter(prefix="/api/v1", tags=["Events"])


def _verify_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("sub") is None:
            raise ValueError
        return payload
    except (JWTError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный токен")


@router.post("/events/test-broadcast")
async def test_broadcast(token: str = Query(...)):
    """Тестовый endpoint: вручную отправить broadcast всем клиентам."""
    _verify_token(token)
    await broadcaster.broadcast("voc")
    await broadcaster.broadcast("moderation")
    await broadcaster.broadcast("dashboard")
    await broadcaster.broadcast("sync")
    return {"sent_to": len(broadcaster._queues), "modules": ["voc", "moderation", "dashboard", "sync"]}


@router.get("/events")
async def sse_events(token: str = Query(...)):
    """Server-Sent Events: фронтенд подписывается и получает push при обновлении данных.
    Токен передаётся через query-параметр (EventSource не поддерживает заголовки)."""
    _verify_token(token)
    q = broadcaster.register()

    async def event_stream():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            broadcaster.unregister(q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
