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
import re
import time
import asyncio
import logging
import requests
from sqlalchemy import text
from . import centrifugo, wb_client

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

SYNC_INTERVAL = 5            # было 60; для бота с меню 1–5 нужна быстрая реакция
BACKFILL_DAYS = 60           # глубина первичного бэкфилла
REQ_DELAY     = 0.8          # пауза между запросами (rate-limit)
MAX_PAGES     = 400          # предохранитель на один проход


def _key() -> str:
    return wb_client.api_key()


def _fetch_events(next_ts: int) -> dict:
    """Один запрос событий. Возвращает result-объект {events, next, ...}."""
    for attempt in range(4):
        try:
            r = wb_client.get_chat_events_raw(next_ts, key=_key(), timeout=25)
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
    # Полная лента переписки (накапливается из событий; WB историю не отдаёт)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS wb_chat_messages (
            id          BIGSERIAL PRIMARY KEY,
            chat_id     TEXT        NOT NULL,
            sender      VARCHAR(20),
            text        TEXT,
            attachments JSONB,
            add_ts      BIGINT,
            created_at  TIMESTAMP   DEFAULT now(),
            UNIQUE (chat_id, add_ts, sender)
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_wb_chat_messages_chat "
        "ON wb_chat_messages (chat_id, add_ts)"
    ))
    conn.commit()


def _store_message(conn, chat_id: str, sender: str, text_body: str, attachments, add_ts: int):
    """Сохранить сообщение в ленту переписки (идемпотентно).

    WB и наша отправка часто дают один текст с разным add_ts (±сек) —
    UNIQUE(chat_id, add_ts, sender) их не склеивает. Поэтому сначала
    ищем тот же текст у того же отправителя в окне 3 минуты.
    """
    import json as _json
    ts = int(add_ts or 0)
    body = text_body or ""
    # Пустые служебные — не дедупим по тексту (могут идти пачкой)
    if body.strip():
        exists = conn.execute(text("""
            SELECT id FROM wb_chat_messages
            WHERE chat_id = :cid AND sender = :snd AND text = :txt
              AND add_ts BETWEEN :t0 AND :t1
            LIMIT 1
        """), {
            "cid": chat_id, "snd": sender, "txt": body,
            "t0": ts - 180_000, "t1": ts + 180_000,
        }).first()
        if exists:
            return
    att = _json.dumps(attachments, ensure_ascii=False) if attachments else None
    conn.execute(text("""
        INSERT INTO wb_chat_messages (chat_id, sender, text, attachments, add_ts)
        VALUES (:cid, :snd, :txt, CAST(:att AS JSONB), :ts)
        ON CONFLICT (chat_id, add_ts, sender) DO NOTHING
    """), {"cid": chat_id, "snd": sender, "txt": body, "att": att, "ts": ts})


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
    attachments = msg.get("attachments") or {}
    good   = attachments.get("goodCard") or {}
    nm_id  = good.get("nmID")
    gname  = good.get("name")
    cname  = e.get("clientName")
    reply_sign = (e.get("replySign") or "").strip()

    # Сохраняем сообщение в полную ленту переписки (все стороны)
    _store_message(conn, cid, sender, msg.get("text") or "", attachments, ts)

    if sender == "client":
        closing = _is_closing(body)
        conn.execute(text("""
            INSERT INTO wb_chats
                (chat_id, client_name, nm_id, good_name, last_message, last_sender,
                 last_ts, needs_answer, is_closing, waiting_since, reply_sign, updated_at)
            VALUES
                (:cid, :cn, :nm, :gn, :msg, 'client',
                 :ts, true, :closing, to_timestamp(:ts/1000.0), NULLIF(:rs,''), now())
            ON CONFLICT (chat_id) DO UPDATE SET
                client_name  = COALESCE(EXCLUDED.client_name, wb_chats.client_name),
                nm_id        = COALESCE(EXCLUDED.nm_id,        wb_chats.nm_id),
                good_name    = COALESCE(EXCLUDED.good_name,    wb_chats.good_name),
                last_message = EXCLUDED.last_message,
                last_sender  = 'client',
                last_ts      = EXCLUDED.last_ts,
                needs_answer = true,
                is_closing   = :closing,
                reply_sign   = COALESCE(NULLIF(EXCLUDED.reply_sign,''), wb_chats.reply_sign),
                -- если чат уже ждал ответа — сохраняем самый ранний момент ожидания
                waiting_since = CASE
                    WHEN wb_chats.needs_answer AND wb_chats.waiting_since IS NOT NULL
                    THEN LEAST(wb_chats.waiting_since, EXCLUDED.waiting_since)
                    ELSE EXCLUDED.waiting_since END,
                updated_at   = now()
        """), {"cid": cid, "cn": cname, "nm": nm_id, "gn": gname, "msg": body,
               "ts": ts, "closing": closing, "rs": reply_sign})
        if closing:
            # Клиент закрыл вопрос («спасибо» и т.п.) — в архив
            conn.execute(text("""
                INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
                VALUES ('chat', :cid, 'wb', 'wb_chat_sync',
                        'Авто: клиент закрыл вопрос (благодарность)')
                ON CONFLICT (item_type, item_id, mp) DO NOTHING
            """), {"cid": cid})
            conn.execute(text("""
                UPDATE wb_chats SET needs_answer = false, waiting_since = NULL, updated_at = now()
                WHERE chat_id = :cid
            """), {"cid": cid})
        else:
            # Клиент написал с вопросом — вернуть чат из архива в работу
            conn.execute(text("""
                DELETE FROM cx_dismissed_items
                WHERE mp = 'wb' AND item_type = 'chat' AND item_id = :cid
            """), {"cid": cid})
    elif sender == "seller":
        conn.execute(text("""
            INSERT INTO wb_chats
                (chat_id, client_name, nm_id, good_name, last_message, last_sender,
                 last_ts, needs_answer, is_closing, waiting_since, reply_sign, updated_at)
            VALUES
                (:cid, :cn, :nm, :gn, :msg, 'seller',
                 :ts, false, false, NULL, NULLIF(:rs,''), now())
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
                reply_sign   = COALESCE(NULLIF(EXCLUDED.reply_sign,''), wb_chats.reply_sign),
                updated_at   = now()
        """), {"cid": cid, "cn": cname, "nm": nm_id, "gn": gname, "msg": body, "ts": ts, "rs": reply_sign})
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

            updated_ids = []
            for e in events:
                _upsert_event(conn, e)
                cid = e.get("chatID") or e.get("chat_id") or ""
                if cid:
                    updated_ids.append(cid)
                processed += 1
                ets = int(e.get("addTimestamp") or 0)
                if ets > last_ts:
                    last_ts = ets
            conn.commit()

            # Публикуем обновлённые чаты в Centrifugo
            if updated_ids:
                rows = conn.execute(text("""
                    SELECT chat_id, client_name, nm_id, good_name,
                           last_message, last_sender, last_ts, needs_answer
                    FROM wb_chats WHERE chat_id = ANY(:ids)
                """), {"ids": list(set(updated_ids))}).mappings().all()
                for row in rows:
                    centrifugo.publish("chats", {
                        "type": "chat_updated",
                        "chat": dict(row),
                    })

            nxt = res.get("next")
            if not nxt or int(nxt) <= cursor:
                break
            cursor = int(nxt)
            pages += 1
            time.sleep(REQ_DELAY)

        if last_ts > _get_cursor(conn):
            _set_cursor(conn, last_ts)

        # Автоархив: наше последнее сообщение, клиент молчит >2 дней
        archived = conn.execute(text("""
            WITH targets AS (
              SELECT c.chat_id
              FROM wb_chats c
              LEFT JOIN cx_dismissed_items d
                ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
              WHERE d.item_id IS NULL
                AND lower(COALESCE(c.last_sender, '')) = 'seller'
                AND to_timestamp(COALESCE(c.last_ts, 0) / 1000.0)
                    < now() - interval '2 days'
            ),
            ins AS (
              INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
              SELECT 'chat', chat_id, 'wb', 'wb_chat_sync',
                     'Авто: нет ответа клиента >2 дн после нашего'
              FROM targets
              ON CONFLICT (item_type, item_id, mp) DO NOTHING
              RETURNING item_id
            )
            SELECT count(*) FROM ins
        """)).scalar() or 0
        if archived:
            conn.execute(text("""
                UPDATE wb_chats c SET needs_answer = false, waiting_since = NULL, updated_at = now()
                WHERE EXISTS (
                  SELECT 1 FROM cx_dismissed_items d
                  WHERE d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
                    AND d.note LIKE 'Авто:%'
                )
                  AND c.needs_answer = true
            """))
            conn.commit()
            logger.info("[WBChatSync] auto-archived seller-silent>2d: %s", archived)

        # Уже помеченные is_closing, но ещё не в архиве
        thanks = conn.execute(text("""
            WITH targets AS (
              SELECT c.chat_id FROM wb_chats c
              LEFT JOIN cx_dismissed_items d
                ON d.item_id = c.chat_id AND d.mp = 'wb' AND d.item_type = 'chat'
              WHERE d.item_id IS NULL AND c.is_closing = true
            ),
            ins AS (
              INSERT INTO cx_dismissed_items (item_type, item_id, mp, dismissed_by, note)
              SELECT 'chat', chat_id, 'wb', 'wb_chat_sync',
                     'Авто: клиент закрыл вопрос (благодарность)'
              FROM targets
              ON CONFLICT (item_type, item_id, mp) DO NOTHING
              RETURNING item_id
            )
            SELECT count(*) FROM ins
        """)).scalar() or 0
        if thanks:
            conn.execute(text("""
                UPDATE wb_chats SET needs_answer = false, waiting_since = NULL, updated_at = now()
                WHERE is_closing = true AND needs_answer = true
            """))
            conn.commit()
            logger.info("[WBChatSync] auto-archived thanks/closing: %s", thanks)

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
                await asyncio.to_thread(sync_events, db, backfill_from)
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
                # Блокирующий синк (HTTP к API WB) — в отдельном потоке
                await asyncio.to_thread(sync_events, db)
            finally:
                db.close()
        except Exception as e:
            logger.error("[WBChatSync] cycle error: %s", e)
