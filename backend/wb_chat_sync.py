"""
Синхронизация чатов с покупателями WB для мониторинга в «Контроль отдела».

Логика:
  - WB отдаёт события чатов (/api/v1/seller/events) в хронологическом порядке.
  - У каждого события есть sender: client / seller / wb.
  - Чат «ждёт ответа», если последнее сообщение — от client.
  - Храним состояние в таблице wb_chats; дашборд читает из неё (без вызовов API).

Воркер:
  - При старте: бэкфилл за N дней (BACKFILL_DAYS), если таблица пустая или курсора нет.
  - Далее: инкрементальная синхронизация от сохранённого курсора каждые SYNC_INTERVAL сек.

Rate-limit: WB ~1 req/s, при 429 — пауза и повтор.
"""
import os
import re
import time
import asyncio
import logging
import requests
from sqlalchemy import text

logger = logging.getLogger("wb_chat_sync")

# ── Определение «закрывающих» сообщений (благодарность/подтверждение) ──────────
# Такой чат НЕ требует ответа, но и не удаляется — выносится в отдельный счётчик.
_CLOSING_RE = re.compile(
    r'(спасибо|благодар|пожалуйста|👍|🙏|❤|отлично|супер|здорово|'
    r'^ок\b|^окей|хорошо|понятно|поняла|понял|принял|принято|ясно|'
    r'договорились|всё ясно|все ясно|всё понятно|все понятно|буду знать|'
    r'учту|учла|попробую|попробуем|оформил|оформила|получил|получила|'
    r'всё пришло|все пришло|всё собрал|все собрал)',
    re.IGNORECASE)
# Признаки реального запроса — перевешивают «спасибо» в том же сообщении
_QUESTION_RE = re.compile(
    r'[?]|когда|почему|как |каким|где |сколь|можно ли|можете ли|дослать|дошлите|'
    r'верн|замен|обмен|брак|дефект|не приш|не полн|не работ|не хватает|отсутств|'
    r'проблем|претенз|жалоб|сломан|разбит|помогите|подскажите|уточн|вопрос',
    re.IGNORECASE)


def _is_closing(text_body: str) -> bool:
    """True, если сообщение — короткая благодарность/подтверждение без запроса."""
    s = (text_body or "").strip().lower()
    if not s:
        return False
    if len(s) > 40:          # длинные сообщения не считаем закрывающими
        return False
    if _QUESTION_RE.search(s):
        return False
    core = re.sub(r'[^а-яёa-z0-9]', '', s)
    if not core:             # только эмодзи/пунктуация (напр. «🙏») — закрывающее
        return True
    return bool(_CLOSING_RE.search(s))

WB_CHAT_BASE  = "https://buyer-chat-api.wildberries.ru"
SYNC_INTERVAL = 300          # инкрементальная синхронизация раз в 5 минут
BACKFILL_DAYS = 60           # глубина первичного бэкфилла
REQ_DELAY     = 0.8          # пауза между запросами (rate-limit)
MAX_PAGES     = 400          # предохранитель на один проход


def _key() -> str:
    return os.getenv("WB_API_KEY", "") or os.getenv("WB_FEEDBACKS_TOKEN", "")


def _h() -> dict:
    return {"Authorization": _key()}


def _fetch_events(next_ts: int) -> dict:
    """Один запрос событий. Возвращает result-объект {events, next, ...}."""
    params = {"next": next_ts} if next_ts else {}
    for attempt in range(4):
        try:
            r = requests.get(f"{WB_CHAT_BASE}/api/v1/seller/events",
                             params=params, headers=_h(), timeout=25)
            if r.status_code == 429:
                time.sleep(3)
                continue
            r.raise_for_status()
            return (r.json() or {}).get("result") or {}
        except requests.HTTPError as e:
            if getattr(e.response, "status_code", 0) == 429:
                time.sleep(3)
                continue
            raise
    return {}


def _ensure_cursor_row(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS wb_chat_sync_state (
            id       INTEGER PRIMARY KEY DEFAULT 1,
            last_ts  BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT now()
        )
    """))
    conn.execute(text("INSERT INTO wb_chat_sync_state (id, last_ts) VALUES (1, 0) ON CONFLICT (id) DO NOTHING"))
    conn.commit()


def _get_cursor(conn) -> int:
    row = conn.execute(text("SELECT last_ts FROM wb_chat_sync_state WHERE id = 1")).fetchone()
    return int(row[0]) if row and row[0] else 0


def _set_cursor(conn, ts: int):
    conn.execute(text("UPDATE wb_chat_sync_state SET last_ts = :ts, updated_at = now() WHERE id = 1"),
                 {"ts": int(ts)})
    conn.commit()


def _upsert_event(conn, e: dict):
    """Применяет одно событие-сообщение к таблице wb_chats."""
    if e.get("eventType") != "message":
        return
    cid = e.get("chatID")
    if not cid:
        return
    sender = e.get("sender") or ""
    ts     = int(e.get("addTimestamp") or 0)
    msg    = e.get("message") or {}
    body   = (msg.get("text") or "")[:1000]
    good   = ((msg.get("attachments") or {}).get("goodCard")) or {}
    nm_id  = good.get("nmID")
    gname  = good.get("name")
    cname  = e.get("clientName")

    if sender == "client":
        closing = _is_closing(body)
        conn.execute(text("""
            INSERT INTO wb_chats
                (chat_id, client_name, nm_id, good_name, last_message, last_sender,
                 last_ts, needs_answer, is_closing, waiting_since, updated_at)
            VALUES
                (:cid, :cn, :nm, :gn, :msg, 'client',
                 :ts, true, :closing, to_timestamp(:ts/1000.0), now())
            ON CONFLICT (chat_id) DO UPDATE SET
                client_name  = COALESCE(EXCLUDED.client_name, wb_chats.client_name),
                nm_id        = COALESCE(EXCLUDED.nm_id,        wb_chats.nm_id),
                good_name    = COALESCE(EXCLUDED.good_name,    wb_chats.good_name),
                last_message = EXCLUDED.last_message,
                last_sender  = 'client',
                last_ts      = EXCLUDED.last_ts,
                needs_answer = true,
                is_closing   = :closing,
                -- если чат уже ждал ответа — сохраняем самый ранний момент ожидания
                waiting_since = CASE
                    WHEN wb_chats.needs_answer AND wb_chats.waiting_since IS NOT NULL
                    THEN LEAST(wb_chats.waiting_since, EXCLUDED.waiting_since)
                    ELSE EXCLUDED.waiting_since END,
                updated_at   = now()
        """), {"cid": cid, "cn": cname, "nm": nm_id, "gn": gname, "msg": body,
               "ts": ts, "closing": closing})
    elif sender == "seller":
        conn.execute(text("""
            INSERT INTO wb_chats
                (chat_id, client_name, nm_id, good_name, last_message, last_sender,
                 last_ts, needs_answer, is_closing, waiting_since, updated_at)
            VALUES
                (:cid, :cn, :nm, :gn, :msg, 'seller',
                 :ts, false, false, NULL, now())
            ON CONFLICT (chat_id) DO UPDATE SET
                client_name  = COALESCE(EXCLUDED.client_name, wb_chats.client_name),
                nm_id        = COALESCE(EXCLUDED.nm_id,        wb_chats.nm_id),
                good_name    = COALESCE(EXCLUDED.good_name,    wb_chats.good_name),
                last_message = EXCLUDED.last_message,
                last_sender  = 'seller',
                last_ts      = EXCLUDED.last_ts,
                needs_answer = false,
                is_closing   = false,
                waiting_since = NULL,
                updated_at   = now()
        """), {"cid": cid, "cn": cname, "nm": nm_id, "gn": gname, "msg": body, "ts": ts})
    # sender == 'wb' (системные) — не меняют статус ответа


def sync_events(db, since_ts: int = None, max_pages: int = MAX_PAGES) -> tuple[int, int]:
    """
    Проходит события от курсора вперёд, обновляет wb_chats.
    Возвращает (обработано_событий, новый_курсор).
    """
    if not _key():
        logger.warning("[WBChatSync] no WB_API_KEY")
        return 0, since_ts or 0

    with db.bind.connect() as conn:
        _ensure_cursor_row(conn)
        cursor = since_ts if since_ts is not None else _get_cursor(conn)
        processed = 0
        pages = 0
        last_ts = cursor

        while pages < max_pages:
            try:
                res = _fetch_events(cursor)
            except Exception as ex:
                logger.error("[WBChatSync] fetch error: %s", ex)
                break

            events = res.get("events") or []
            if not events:
                break

            for e in events:
                _upsert_event(conn, e)
                processed += 1
                ets = int(e.get("addTimestamp") or 0)
                if ets > last_ts:
                    last_ts = ets
            conn.commit()

            nxt = res.get("next")
            if not nxt or int(nxt) <= cursor:
                break
            cursor = int(nxt)
            pages += 1
            time.sleep(REQ_DELAY)

        if last_ts > _get_cursor(conn):
            _set_cursor(conn, last_ts)

        logger.info("[WBChatSync] processed %d events, %d pages, cursor=%d",
                    processed, pages, last_ts)
        return processed, last_ts


async def start_chat_sync_worker(get_db_func) -> None:
    """Фоновая задача: бэкфилл при старте, затем инкрементальная синхронизация."""
    logger.info("[WBChatSync] worker starting")
    await asyncio.sleep(20)  # даём приложению подняться

    if not _key():
        logger.warning("[WBChatSync] no WB_API_KEY — worker idle")
        return

    # Бэкфилл, если курсор пустой
    try:
        db = next(get_db_func())
        try:
            with db.bind.connect() as conn:
                _ensure_cursor_row(conn)
                cursor = _get_cursor(conn)
            if cursor == 0:
                backfill_from = int((time.time() - BACKFILL_DAYS * 86400) * 1000)
                logger.info("[WBChatSync] backfill from %d days", BACKFILL_DAYS)
                sync_events(db, since_ts=backfill_from)
        finally:
            db.close()
    except Exception as e:
        logger.error("[WBChatSync] backfill error: %s", e)

    # Инкрементальный цикл
    while True:
        await asyncio.sleep(SYNC_INTERVAL)
        try:
            db = next(get_db_func())
            try:
                sync_events(db)
            finally:
                db.close()
        except Exception as e:
            logger.error("[WBChatSync] cycle error: %s", e)
