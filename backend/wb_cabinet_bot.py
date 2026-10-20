"""Разбор ссылки чата WB из кабинета и запуск бота на сервере."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import text

_CHAT_ID_RE = re.compile(r"[?&]chatId=([0-9a-f-]{36})", re.I)
logger = logging.getLogger("wb_cabinet_bot")

_SCRIPT_CANDIDATES = (
    Path("/root/cx-dashboard/scripts/wb_bot_open_chat.py"),
    Path(__file__).resolve().parents[1] / "scripts" / "wb_bot_open_chat.py",
)

_CHROME_DOWN = ("не запущен", "нет окна", "не установлен")
_fail_until: dict[str, float] = {}
_fail_tries: dict[str, int] = {}


def chat_id_from_cabinet_url(url: str) -> str:
    """https://seller.wildberries.ru/chat-with-clients?chatId=uuid → 1:uuid."""
    m = _CHAT_ID_RE.search(url or "")
    if not m:
        return ""
    return "1:" + m.group(1).lower()


def _script_path() -> Path:
    for p in _SCRIPT_CANDIDATES:
        if p.is_file():
            return p
    raise FileNotFoundError("не найден scripts/wb_bot_open_chat.py")


def _busy_payload(payload: dict, err: str) -> bool:
    if payload.get("busy"):
        return True
    text_err = str(err or payload.get("error") or "")
    return "уже открывает другой чат" in text_err


def run_open_claim_chat(
    claim_id: str,
    *,
    timeout: int | None = None,
    archive: bool = False,
    wait_lock: bool = False,
) -> dict:
    """Открыть чат по заявке через Chrome кабинета WB. Возвращает {ok, chat_id}."""
    cid = str(claim_id or "").strip()
    if not cid:
        return {"ok": False, "error": "нет номера заявки"}
    script = _script_path()
    env = os.environ.copy()
    env.setdefault("WB_BOT_CDP", "http://127.0.0.1:9224")
    cmd = [sys.executable, str(script), cid, "--archive" if archive else "--active"]
    if wait_lock:
        cmd.append("--wait")
    run_timeout = timeout if timeout is not None else (130 if wait_lock else 55)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=run_timeout,
            env=env,
            cwd=str(script.parent.parent),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "кабинет WB не ответил за минуту — попробуйте ещё раз"}
    except FileNotFoundError:
        return {"ok": False, "error": "бот кабинета WB не установлен"}
    raw = (proc.stdout or "").strip().splitlines()
    payload = {}
    for line in reversed(raw):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    err = payload.get("error") or ""
    if proc.returncode != 0 and not payload.get("ok"):
        if not err:
            tail = (proc.stderr or "").strip().splitlines()[-1:]
            err = tail[0] if tail else f"бот WB код {proc.returncode}"
        out = {"ok": False, "error": str(err)[:400]}
        if _busy_payload(payload, err):
            out["busy"] = True
        return out
    chat_id = str(payload.get("chat_id") or "").strip()
    if not chat_id:
        chat_id = chat_id_from_cabinet_url(str(payload.get("url") or ""))
    if payload.get("ok") and chat_id:
        return {"ok": True, "chat_id": chat_id, "created": bool(payload.get("created"))}
    out = {"ok": False, "error": payload.get("error") or "не удалось открыть чат в кабинете WB"}
    if _busy_payload(payload, out["error"]):
        out["busy"] = True
    return out


def _link_chat(engine, chat_id: str, srid: str | None) -> None:
    from . import wb_chat as wb_chat_mod

    wb_chat_mod.ensure_chat_order_rid_column(engine)
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO wb_chats (chat_id, order_rid, needs_answer, updated_at)
            VALUES (:cid, :srid, false, now())
            ON CONFLICT (chat_id) DO UPDATE SET
                order_rid = COALESCE(NULLIF(wb_chats.order_rid, ''), EXCLUDED.order_rid),
                updated_at = now()
        """), {"cid": chat_id, "srid": srid or None})
        conn.commit()


def ensure_wb_claim_chat(engine, claim_id: str, *, wait_lock: bool = False) -> dict:
    """Найти или открыть чат WB по заявке. Живой клик: wait_lock=True."""
    from .routers.cx_responses import _find_wb_chat_for_claim

    cid = str(claim_id or "").strip()
    if not cid:
        return {"ok": False, "error": "нет номера заявки"}
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT c.claim_id::text, c.srid, c.is_archive
            FROM wb_claims c
            WHERE c.claim_id::text = :cid
            LIMIT 1
        """), {"cid": cid}).mappings().first()
        if not row:
            return {"ok": False, "error": "Претензия не найдена", "missing": True}
        srid = str(row.get("srid") or "").strip()
        is_archive = bool(row.get("is_archive"))
        chat = _find_wb_chat_for_claim(conn, srid) if srid else None
    existing = str((chat or {}).get("chat_id") or "").strip()
    if existing:
        return {"ok": True, "chat_id": existing, "created": False}
    bot = run_open_claim_chat(cid, archive=is_archive, wait_lock=wait_lock)
    chat_id = str((bot or {}).get("chat_id") or "").strip()
    if not chat_id:
        return bot
    try:
        _link_chat(engine, chat_id, srid or None)
    except Exception:
        logger.exception("[WB] link chat to claim failed claim=%s chat=%s", cid, chat_id)
    return {"ok": True, "chat_id": chat_id, "created": bool((bot or {}).get("created"))}


def _in_backoff(claim_id: str) -> bool:
    until = _fail_until.get(claim_id) or 0
    return time.time() < until


def _mark_fail(claim_id: str) -> None:
    n = _fail_tries.get(claim_id, 0) + 1
    _fail_tries[claim_id] = n
    delay = min(6 * 3600, 8 * 60 * (2 ** min(n, 6)))
    _fail_until[claim_id] = time.time() + delay


def _mark_ok(claim_id: str) -> None:
    _fail_until.pop(claim_id, None)
    _fail_tries.pop(claim_id, None)


def next_live_claim_without_chat(engine) -> dict | None:
    """Следующая живая заявка WB, по которой чата ещё нет."""
    from .routers.cx_responses import _find_wb_chat_for_claim

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT c.claim_id::text AS claim_id, c.srid, c.is_archive
            FROM wb_claims c
            WHERE NOT COALESCE(c.is_archive, false)
              AND NULLIF(TRIM(c.claim_id::text), '') IS NOT NULL
              AND NULLIF(TRIM(COALESCE(c.srid, '')), '') IS NOT NULL
              AND COALESCE(c.status, '') NOT IN ('Одобрено', 'Отказ', 'Отменена', 'Закрыта')
              AND NOT EXISTS (
                SELECT 1 FROM wb_chats ch
                WHERE NULLIF(TRIM(COALESCE(ch.order_rid, '')), '') IS NOT NULL
                  AND ch.order_rid = c.srid
              )
            ORDER BY c.claim_date ASC NULLS LAST, c.created_dt ASC NULLS LAST
            LIMIT 80
        """)).mappings().all()
        for row in rows:
            claim_id = str(row.get("claim_id") or "").strip()
            srid = str(row.get("srid") or "").strip()
            if not claim_id or _in_backoff(claim_id):
                continue
            if _find_wb_chat_for_claim(conn, srid):
                continue
            return {
                "claim_id": claim_id,
                "srid": srid,
                "is_archive": bool(row.get("is_archive")),
            }
    return None


def prefetch_one_claim_chat(engine) -> dict:
    """Открыть один чат, если Chrome свободен. Не конкурирует с кликом менеджера."""
    row = next_live_claim_without_chat(engine)
    if not row:
        return {"ok": True, "empty": True}
    claim_id = row["claim_id"]
    result = ensure_wb_claim_chat(engine, claim_id, wait_lock=False)
    if result.get("busy"):
        return result
    err = str(result.get("error") or "")
    if any(part in err for part in _CHROME_DOWN):
        return result
    if result.get("ok"):
        _mark_ok(claim_id)
        logger.info("[WB] prefetch chat claim=%s chat=%s", claim_id, result.get("chat_id"))
        return result
    _mark_fail(claim_id)
    logger.warning("[WB] prefetch chat fail claim=%s: %s", claim_id, err[:200])
    return result


def _prefetch_isolated() -> dict:
    from .database import engine

    return prefetch_one_claim_chat(engine)


async def start_wb_claim_chat_worker(get_db_func) -> None:
    """По живым возвратам WB сразу открываем чат в кабинете, не дожидаясь клика."""
    logger.info("[WB] claim-chat prefetch worker starting")
    await asyncio.sleep(40)
    while True:
        delay = 15
        try:
            result = await asyncio.to_thread(_prefetch_isolated)
            if result.get("busy"):
                delay = 5
            elif result.get("empty"):
                delay = 20
            elif result.get("ok"):
                delay = 8
            elif any(part in str(result.get("error") or "") for part in _CHROME_DOWN):
                delay = 60
            else:
                delay = 10
        except Exception:
            logger.exception("[WB] claim-chat prefetch cycle")
            delay = 20
        await asyncio.sleep(delay)
