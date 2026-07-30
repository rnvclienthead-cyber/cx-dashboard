"""Wildberries Buyer Chat API — интеграция чата продавца с покупателем.

Безопасный режим:
  WB_CHAT_ENABLED=false  → воркер работает, события читает, в чат НЕ пишет (мониторинг)
  WB_CHAT_ENABLED=true   → авто-отвечает ссылкой на форму + шлёт уведомления об отправке

Триггер: менеджер вручную пишет WB_TRIGGER_KEYWORD (по умолч. #от) в чате продавца,
  или вставляет ссылку на форму с токеном. Система видит новое сообщение продавца →
  записывает chatID → (если enabled) отвечает ссылкой на форму.

Источник данных: этот модуль НЕ опрашивает WB /seller/events сам — события чатов
  опрашивает wb_chat_sync.py (каждые 5с) и сохраняет всё в wb_chat_messages; здесь
  мы только читаем оттуда новые сообщения продавца (см. _worker_once), чтобы не
  дублировать опрос одного и того же эндпоинта двумя независимыми процессами.
"""
import os
import re
import asyncio
from typing import Optional
from backend.database import db_session
from backend import wb_client

TOKEN_PATTERN  = re.compile(
    r'(?:https?://)?box\.vidovito\.com(?:/reshipment/form)?\?t=([\w\-]+)',
    re.IGNORECASE,
)
TOKEN_FORM_URL = "https://box.vidovito.com/reshipment/form"

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

FORM_BASE_URL = "https://cxvo.ru/reshipment/form"


def _key() -> str:
    return wb_client.api_key()


def is_enabled() -> bool:
    return os.getenv("WB_CHAT_ENABLED", "false").lower() == "true" and bool(_key())


def _trigger() -> str:
    return os.getenv("WB_TRIGGER_KEYWORD", "#от").lower()


# ── API вызовы ────────────────────────────────────────────────────────────────

def get_chats() -> list[dict]:
    """Список всех чатов продавца."""
    return wb_client.get_chats(key=_key())


def ensure_chat_order_rid_column(engine_or_conn) -> None:
    """Колонка order_rid: srid заказа из goodCard чата WB (не всегда есть в событиях сообщений)."""
    from sqlalchemy import text as sql_text
    conn = engine_or_conn
    close = False
    if hasattr(engine_or_conn, "connect"):
        conn = engine_or_conn.connect()
        close = True
    try:
        exists = conn.execute(sql_text("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'wb_chats' AND column_name = 'order_rid'
        """)).scalar()
        if not exists:
            conn.execute(sql_text("ALTER TABLE wb_chats ADD COLUMN order_rid TEXT"))
            conn.commit()
        # индекс — отдельно; IF NOT EXISTS не требует ACCESS EXCLUSIVE на всю таблицу так же жёстко
        conn.execute(sql_text("""
            CREATE INDEX IF NOT EXISTS idx_wb_chats_order_rid
            ON wb_chats (order_rid) WHERE order_rid IS NOT NULL AND order_rid <> ''
        """))
        conn.commit()
    finally:
        if close:
            conn.close()


def backfill_order_rids_from_wb(db=None) -> dict:
    """
    Дотягивает rid/nm/replySign из GET /seller/chats в wb_chats.
    Нужно потому что в events у сообщения attachments часто пустые,
    а в списке чатов goodCard.rid есть.
    """
    import json as _json
    from sqlalchemy import text as sql_text
    from sqlalchemy.orm import Session as _S
    ensure_chat_order_rid_column(db.bind if isinstance(db, _S) else db)

    try:
        chats = get_chats()
    except Exception as e:
        print(f"[WB Chat] backfill_order_rids get_chats error: {e}")
        return {"ok": False, "error": str(e), "updated": 0}

    updated = 0
    with_rid = 0
    for ch in chats or []:
        chat_id = str(ch.get("chatID") or "")
        if not chat_id:
            continue
        good = ch.get("goodCard") or {}
        rid = str(good.get("rid") or "").strip()
        nm = good.get("nmID")
        gname = good.get("name") or ""
        rs = (ch.get("replySign") or "").strip()
        cname = ch.get("clientName")
        if rid:
            with_rid += 1
        try:
            db.execute(sql_text("""
                UPDATE wb_chats SET
                    order_rid   = CASE
                        WHEN :rid <> '' THEN :rid
                        ELSE order_rid END,
                    nm_id       = COALESCE(:nm, nm_id),
                    good_name   = COALESCE(NULLIF(:gn, ''), good_name),
                    client_name = COALESCE(NULLIF(:cn, ''), client_name),
                    reply_sign  = COALESCE(NULLIF(:rs, ''), reply_sign),
                    updated_at  = now()
                WHERE chat_id = :cid
            """), {
                "cid": chat_id, "rid": rid, "nm": nm, "gn": gname,
                "cn": cname, "rs": rs,
            })
            # Чтобы старые запросы по attachments->goodCard->>rid тоже видели заказ:
            # дописываем goodCard в последнее сообщение без rid (если оно есть).
            if rid:
                db.execute(sql_text("""
                    UPDATE wb_chat_messages m SET
                        attachments = jsonb_set(
                            COALESCE(attachments, '{}'::jsonb),
                            '{goodCard}',
                            CAST(:gc AS jsonb),
                            true
                        )
                    WHERE m.chat_id = :cid
                      AND m.add_ts = (
                        SELECT max(m2.add_ts) FROM wb_chat_messages m2
                        WHERE m2.chat_id = :cid
                      )
                      AND (
                        m.attachments IS NULL
                        OR m.attachments->'goodCard'->>'rid' IS NULL
                        OR m.attachments->'goodCard'->>'rid' = ''
                      )
                """), {
                    "cid": chat_id,
                    "gc": _json.dumps({
                        "nmID": nm, "rid": rid, "name": gname,
                        "price": good.get("price") or 0,
                        "priceCurrency": good.get("priceCurrency") or "",
                        "size": good.get("size") or "",
                    }, ensure_ascii=False),
                })
            updated += 1
        except Exception as e:
            print(f"[WB Chat] backfill {chat_id}: {e}")
            try:
                db.rollback()
            except Exception:
                pass
    try:
        db.commit()
    except Exception:
        pass
    print(f"[WB Chat] backfill_order_rids: chats={len(chats or [])} touched={updated} with_rid={with_rid}")
    return {"ok": True, "chats": len(chats or []), "updated": updated, "with_rid": with_rid}


def get_chat_messages(chat_id: str, take: int = 30) -> list[dict]:
    """Получить последние сообщения конкретного чата (для поиска токена в истории)."""
    return wb_client.get_chat_messages(chat_id, take=take, key=_key(), timeout=15)


def get_events(next_ts: int = 0) -> dict:
    """Новые события чатов. next_ts = курсор пагинации (мс).

    Используется только вспомогательными скриптами (scripts/) для ручной
    отладки — сам wb_chat.py больше не опрашивает /seller/events напрямую
    (см. docstring модуля), это делает wb_chat_sync.py."""
    r = wb_client.get_chat_events_raw(next_ts, key=_key(), timeout=15)
    r.raise_for_status()
    return r.json() or {}


def send_message(reply_sign: str, text: str) -> dict:
    """Отправить сообщение в чат покупателю (multipart)."""
    r = wb_client.send_chat_message(reply_sign, text, key=_key(), timeout=15)
    return r.json() or {}


def resolve_reply_sign(chat_id: str) -> str:
    """
    Актуальный replySign для чата.
    WB требует свежую подпись из GET /seller/chats — без неё сообщение не уйдёт.
    """
    if not chat_id or not _key():
        return ""
    try:
        chats = get_chats()
    except Exception as e:
        print(f"[WB Chat] resolve_reply_sign get_chats error: {e}")
        return ""
    cid = str(chat_id)
    for ch in chats or []:
        if str(ch.get("chatID") or "") == cid:
            return (ch.get("replySign") or "").strip()
    return ""


# ── Бизнес-логика ─────────────────────────────────────────────────────────────

def generate_token_url(chat_id: str, get_db=None) -> str:
    """Создаёт одноразовый токен, привязывает к chat_id и возвращает ссылку на форму."""
    import secrets
    from sqlalchemy import text as sql_text
    from backend.database import db_session
    # Как кнопка generate-token в разделе доотправки: token_urlsafe(12)
    token = secrets.token_urlsafe(12)
    try:
        with db_session() as db:
            db.execute(sql_text("""
                INSERT INTO reshipment_chat_tokens (token, chat_id, created_by, expires_at)
                VALUES (:token, :chat_id, 'wb_chat_bot', NOW() + INTERVAL '7 days')
            """), {"token": token, "chat_id": chat_id})
            db.commit()
    except Exception as e:
        print(f"[WB Chat] token generation error: {e}")
    return f"{TOKEN_FORM_URL}?t={token}"


def send_tracking_notification(reply_sign: str, cdek_number: str, confirm_url: str = "") -> bool:
    """Уведомить покупателя в WB чате об отправке посылки."""
    if not reply_sign or not _key():
        return False
    msg = (
        f"✅ Ваша деталь отправлена!\n"
        f"Трек-номер СДЭК: {cdek_number}\n"
        f"Отследить: https://www.cdek.ru/track.html?order_id={cdek_number}"
    )
    try:
        send_message(reply_sign, msg)
        return True
    except Exception as e:
        print(f"[WB Chat] send_tracking error: {e}")
        return False


def send_rejection_notification(reply_sign: str, reason: str = "") -> bool:
    """Уведомить покупателя об отклонении заявки."""
    if not reply_sign or not _key():
        return False
    msg = (
        "К сожалению, мы не можем выполнить вашу заявку на отправку детали — "
        "запрашиваемая позиция отсутствует на нашем складе. "
        "Рекомендуем оформить возврат в личном кабинете Wildberries."
    )
    try:
        send_message(reply_sign, msg)
        return True
    except Exception as e:
        print(f"[WB Chat] send_rejection error: {e}")
        return False


# ── Воркер ────────────────────────────────────────────────────────────────────

def _ensure_trigger_cursor(db) -> None:
    from sqlalchemy import text as sql_text
    db.execute(sql_text("""
        CREATE TABLE IF NOT EXISTS wb_chat_trigger_cursor (
            id               INTEGER PRIMARY KEY DEFAULT 1,
            last_message_id  BIGINT NOT NULL DEFAULT 0,
            updated_at       TIMESTAMP DEFAULT now()
        )
    """))
    db.execute(sql_text(
        "INSERT INTO wb_chat_trigger_cursor (id, last_message_id) VALUES (1, 0) "
        "ON CONFLICT (id) DO NOTHING"
    ))


async def _worker_once(get_db) -> None:
    """Один проход: обработать триггеры в НОВЫХ сообщениях продавца.

    Раньше этот воркер сам опрашивал WB /seller/events своим отдельным курсором
    в памяти — тем же эндпоинтом, который уже опрашивает wb_chat_sync.py каждые
    5 секунд и сохраняет всё в wb_chat_messages. Чтобы не дублировать опрос
    одного и того же эндпоинта, здесь просто читаем то, что wb_chat_sync уже
    сохранил (курсор — wb_chat_trigger_cursor.last_message_id, по id новой
    таблицы wb_chat_messages). Бизнес-логика (токен/хэштег-триггер) не менялась.
    """
    if not _key():
        return

    from sqlalchemy import text as sql_text

    rows = []
    try:
        with db_session() as db:
            _ensure_trigger_cursor(db)
            last_id = db.execute(sql_text(
                "SELECT last_message_id FROM wb_chat_trigger_cursor WHERE id = 1"
            )).scalar() or 0
            rows = db.execute(sql_text("""
                SELECT id, chat_id, text
                FROM wb_chat_messages
                WHERE id > :last_id AND sender = 'seller'
                ORDER BY id ASC
                LIMIT 500
            """), {"last_id": last_id}).mappings().all()
            rows = list(rows)
            if rows:
                max_id = max(r["id"] for r in rows)
                db.execute(sql_text(
                    "UPDATE wb_chat_trigger_cursor SET last_message_id = :v, updated_at = now() WHERE id = 1"
                ), {"v": max_id})
                db.commit()
    except Exception as e:
        print(f"[WB Chat] read new seller messages error: {e}")
        return

    for row in rows:
        chat_id = str(row["chat_id"] or "")
        text_body = row["text"] or ""

        # Новая схема: токен в ссылке
        token_match = TOKEN_PATTERN.search(text_body)
        if token_match:
            token = token_match.group(1)
            print(f"[WB Chat] Token '{token}' found in message, chat {chat_id}")

            # chat_id из сообщения иногда не совпадает с реальным чатом (тот же
            # нюанс, что и раньше с event.chatID) — перепроверяем по lastMessage
            # в списке чатов.
            try:
                await asyncio.sleep(0.5)
                chats = await asyncio.to_thread(get_chats)
            except Exception as e:
                print(f"[WB Chat] get_chats error: {e}")
                continue

            real_chat_id = chat_id  # fallback на chat_id из сообщения
            chat_data = None
            for c in chats:
                last_msg = (c.get("lastMessage") or {}).get("text", "")
                if token in last_msg:
                    real_chat_id = str(c.get("chatID", ""))
                    chat_data = c
                    break

            if real_chat_id != chat_id:
                print(f"[WB Chat] Token chat corrected: {chat_id} → {real_chat_id}")

            if not chat_data:
                chat_data = next((c for c in chats if str(c.get("chatID", "")) == chat_id), None)

            try:
                with db_session() as db:
                    db.execute(sql_text("""
                        UPDATE reshipment_chat_tokens SET chat_id = :cid WHERE token = :token
                    """), {"cid": real_chat_id, "token": token})
                    db.execute(sql_text("""
                        UPDATE reshipment_requests
                        SET wb_chat_id = :cid
                        WHERE chat_token = :token AND wb_chat_id IS NULL
                    """), {"cid": real_chat_id, "token": token})
                    db.commit()
            except Exception as e:
                print(f"[WB Chat] Token DB update error: {e}")

            if chat_data:
                try:
                    reply_sign  = chat_data.get("replySign", "")
                    client_name = chat_data.get("clientName", "")
                    client_id   = str(chat_data.get("clientID", ""))
                    good_card   = chat_data.get("goodCard") or {}
                    nm_id       = good_card.get("nmID")
                    rid         = str(good_card.get("rid", ""))
                    with db_session() as db:
                        db.execute(sql_text("""
                            INSERT INTO wb_chat_pending
                                (chat_id, reply_sign, client_name, client_id, nm_id, rid, status)
                            VALUES (:cid, :rs, :cn, :ci, :nm, :rid, 'waiting_form')
                            ON CONFLICT (chat_id) DO UPDATE SET
                                reply_sign  = EXCLUDED.reply_sign,
                                client_name = EXCLUDED.client_name,
                                nm_id       = COALESCE(EXCLUDED.nm_id, wb_chat_pending.nm_id),
                                rid         = COALESCE(EXCLUDED.rid, wb_chat_pending.rid),
                                updated_at  = NOW()
                        """), {
                            "cid": real_chat_id, "rs": reply_sign, "cn": client_name,
                            "ci": client_id, "nm": nm_id, "rid": rid,
                        })
                        if nm_id:
                            db.execute(sql_text("""
                                UPDATE reshipment_requests
                                SET wb_nm_id = :nm
                                WHERE chat_token = :token AND wb_nm_id IS NULL
                            """), {"nm": nm_id, "token": token})
                        db.commit()
                    print(f"[WB Chat] Pending saved → chat={real_chat_id}, nm={nm_id}, rid={rid}")
                except Exception as e:
                    print(f"[WB Chat] Pending save error: {e}")

            continue

        # Старая схема: хэштег-триггер (оставляем для обратной совместимости)
        if _trigger() not in text_body.lower():
            continue

        print(f"[WB Chat] Trigger '{_trigger()}' in chat {chat_id}")

        # Получаем данные чата (reply_sign, clientName, nmId)
        try:
            await asyncio.sleep(1)  # соблюдаем rate limit WB (1 req/s)
            chats = await asyncio.to_thread(get_chats)
        except Exception as e:
            print(f"[WB Chat] get_chats error: {e}")
            continue

        chat = next((c for c in chats if str(c.get("chatID", "")) == chat_id), None)
        if not chat:
            print(f"[WB Chat] Chat {chat_id} not found in list")
            continue

        reply_sign  = chat.get("replySign", "")
        client_name = chat.get("clientName", "")
        client_id   = str(chat.get("clientID", ""))
        good_card   = chat.get("goodCard") or {}
        nm_id       = good_card.get("nmID")
        rid         = str(good_card.get("rid", ""))

        # Генерируем токен и отправляем ссылку на форму
        form_url = generate_token_url(chat_id, get_db)
        msg = (
            f"Здравствуйте! Для оформления заявки на отправку детали, пожалуйста, заполните форму — "
            f"это займёт пару минут: {form_url}\n\n"
            f"После проверки мы свяжемся с вами и организуем отправку."
        )
        if is_enabled():
            try:
                await asyncio.to_thread(send_message, reply_sign, msg)
                print(f"[WB Chat] Form link sent → chat={chat_id}, client={client_name}, nm={nm_id}")
            except Exception as e:
                print(f"[WB Chat] send error: {e}")
        else:
            print(f"[WB Chat] MONITOR MODE: would send form link to {chat_id} ({client_name}), nm={nm_id}")
            print(f"[WB Chat]   url: {form_url}")

        # Сохраняем pending запись в БД (в любом режиме — для связки когда форма придёт)
        try:
            with db_session() as db:
                db.execute(sql_text("""
                    INSERT INTO wb_chat_pending
                        (chat_id, reply_sign, client_name, client_id, nm_id, rid, status)
                    VALUES (:cid, :rs, :cn, :ci, :nm, :rid, 'waiting_form')
                    ON CONFLICT (chat_id) DO UPDATE SET
                        reply_sign  = EXCLUDED.reply_sign,
                        client_name = EXCLUDED.client_name,
                        updated_at  = NOW()
                """), {
                    "cid": chat_id,
                    "rs":  reply_sign,
                    "cn":  client_name,
                    "ci":  client_id,
                    "nm":  nm_id,
                    "rid": rid,
                })
                db.commit()
        except Exception as e:
            print(f"[WB Chat] DB pending write error: {e}")


_SCAN_CHATS_EVERY_N_CYCLES = 5  # полное сканирование /seller/chats — раз в ~5 мин, а не каждую минуту


async def start_worker(get_db) -> None:
    """Фоновый asyncio воркер. Запускается из lifespan FastAPI.

    _worker_once читает уже сохранённые сообщения из БД (дёшево, не ходит в API WB),
    поэтому крутится каждые 60с. Полное сканирование /seller/chats (scan_chats_for_tokens)
    само по себе — HTTP-запрос к WB, поэтому вызывается реже: раньше дёргало этот
    эндпоинт каждую минуту круглосуточно, теперь — раз в ~5 минут, той же частотой,
    что и аналогичное сканирование в wb_mini_sync.
    """
    print("[WB Chat] Worker started (messages every 60s, full chat scan every ~5 min)")
    cycle = 0
    while True:
        await asyncio.sleep(60)
        if not _key():
            continue
        await _worker_once(get_db)
        cycle += 1
        if cycle % _SCAN_CHATS_EVERY_N_CYCLES == 0:
            try:
                # Блокирующий вызов к API WB — в отдельный поток, чтобы не морозить event loop
                await asyncio.to_thread(scan_chats_for_tokens, get_db)
            except Exception as e:
                print(f"[WB Chat] Periodic scan error: {e}")


def scan_chats_for_tokens(get_db) -> int:
    """Сканирует все активные чаты WB и привязывает токены по lastMessage.
    Надёжнее events: не зависит от chatID в событии."""
    if not _key():
        return 0
    from sqlalchemy import text as sql_text
    try:
        chats = get_chats()
    except Exception as e:
        print(f"[WB Chat] scan_chats_for_tokens: get_chats error: {e}")
        return 0

    linked = 0
    for chat in chats:
        last_msg_text = (chat.get("lastMessage") or {}).get("text", "")
        token_match = TOKEN_PATTERN.search(last_msg_text)
        if not token_match:
            continue

        token    = token_match.group(1)
        chat_id  = str(chat.get("chatID", ""))
        good_card = chat.get("goodCard") or {}
        nm_id    = good_card.get("nmID")
        rid      = str(good_card.get("rid", ""))
        reply_sign  = chat.get("replySign", "")
        client_name = chat.get("clientName", "")
        client_id   = str(chat.get("clientID", ""))

        try:
            with db_session() as db:
                # Уже привязан к этому же чату — пропускаем (иначе воркер каждую минуту
                # заново перелинковывает одни и те же чаты бесконечно)
                already_linked = db.execute(sql_text("""
                    SELECT 1 FROM reshipment_chat_tokens WHERE token = :token AND chat_id = :cid
                """), {"token": token, "cid": chat_id}).first()
                if already_linked:
                    continue
                db.execute(sql_text("""
                    UPDATE reshipment_chat_tokens SET chat_id = :cid WHERE token = :token
                """), {"cid": chat_id, "token": token})
                db.execute(sql_text("""
                    UPDATE reshipment_requests SET wb_chat_id = :cid
                    WHERE chat_token = :token AND wb_chat_id IS NULL
                """), {"cid": chat_id, "token": token})
                db.execute(sql_text("""
                    INSERT INTO wb_chat_pending
                        (chat_id, reply_sign, client_name, client_id, nm_id, rid, status)
                    VALUES (:cid, :rs, :cn, :ci, :nm, :rid, 'waiting_form')
                    ON CONFLICT (chat_id) DO UPDATE SET
                        reply_sign  = EXCLUDED.reply_sign,
                        client_name = EXCLUDED.client_name,
                        nm_id       = COALESCE(EXCLUDED.nm_id, wb_chat_pending.nm_id),
                        rid         = COALESCE(EXCLUDED.rid, wb_chat_pending.rid),
                        updated_at  = NOW()
                """), {
                    "cid": chat_id, "rs": reply_sign, "cn": client_name,
                    "ci": client_id, "nm": nm_id, "rid": rid,
                })
                if nm_id:
                    db.execute(sql_text("""
                        UPDATE reshipment_requests SET wb_nm_id = :nm
                        WHERE chat_token = :token AND wb_nm_id IS NULL
                    """), {"nm": nm_id, "token": token})
                db.commit()
            linked += 1
            print(f"[WB Chat] Scan linked token={token} → chat={chat_id}, nm={nm_id}, rid={rid}")
        except Exception as e:
            print(f"[WB Chat] Scan link error: {e}")

    # Второй проход: чаты с known wb_chat_id но без matched_srid
    try:
        with db_session() as db:
            pending_chats = db.execute(sql_text("""
                SELECT wb_chat_id FROM reshipment_requests
                WHERE wb_chat_id IS NOT NULL AND matched_srid IS NULL
            """)).fetchall()
            pending_ids = {row[0] for row in pending_chats}
    except Exception:
        pending_ids = set()

    for chat in chats:
        chat_id = str(chat.get("chatID", ""))
        if chat_id not in pending_ids:
            continue
        good_card = chat.get("goodCard") or {}
        nm_id     = good_card.get("nmID")
        rid       = str(good_card.get("rid", ""))
        reply_sign  = chat.get("replySign", "")
        client_name = chat.get("clientName", "")
        if not rid:
            continue
        try:
            with db_session() as db:
                db.execute(sql_text("""
                    INSERT INTO wb_chat_pending (chat_id, reply_sign, client_name, nm_id, rid, status, request_id)
                    SELECT :cid, :rs, :cn, :nm, :rid, 'form_submitted', r.id
                    FROM reshipment_requests r
                    WHERE r.wb_chat_id = :cid AND r.matched_srid IS NULL
                    ON CONFLICT (chat_id) DO UPDATE SET
                        reply_sign  = EXCLUDED.reply_sign,
                        nm_id       = COALESCE(EXCLUDED.nm_id, wb_chat_pending.nm_id),
                        rid         = COALESCE(EXCLUDED.rid, wb_chat_pending.rid),
                        request_id  = COALESCE(EXCLUDED.request_id, wb_chat_pending.request_id),
                        updated_at  = NOW()
                """), {"cid": chat_id, "rs": reply_sign, "cn": client_name, "nm": nm_id, "rid": rid})
                db.execute(sql_text("""
                    UPDATE reshipment_requests
                    SET matched_srid = :rid,
                        wb_nm_id     = COALESCE(wb_nm_id, :nm),
                        wb_reply_sign = COALESCE(wb_reply_sign, :rs),
                        updated_at   = NOW()
                    WHERE wb_chat_id = :cid AND matched_srid IS NULL
                """), {"rid": rid, "nm": nm_id, "rs": reply_sign, "cid": chat_id})
                db.commit()
            linked += 1
            print(f"[WB Chat] Scan recovered goodCard: chat={chat_id}, nm={nm_id}, rid={rid}")
        except Exception as e:
            print(f"[WB Chat] Scan goodCard recovery error: {e}")

    # Третий проход: поиск токена в истории сообщений для waiting_form чатов
    try:
        with db_session() as db:
            waiting = db.execute(sql_text("""
                SELECT p.chat_id, p.rid, p.nm_id
                FROM wb_chat_pending p
                WHERE p.status = 'waiting_form' AND p.request_id IS NULL
            """)).mappings().fetchall()
            waiting = list(waiting)
    except Exception:
        waiting = []

    chats_map = {str(c.get("chatID", "")): c for c in chats}

    for row in waiting:
        chat_id = row["chat_id"]
        chat = chats_map.get(chat_id)
        if not chat:
            continue
        last_msg_text = (chat.get("lastMessage") or {}).get("text", "")
        if TOKEN_PATTERN.search(last_msg_text):
            continue
        try:
            messages = get_chat_messages(chat_id, take=50)
        except Exception:
            continue
        token_found = None
        for msg in messages:
            txt = (msg.get("text") or "")
            m = TOKEN_PATTERN.search(txt)
            if m:
                token_found = m.group(1)
                break
        if not token_found:
            continue
        good_card = chat.get("goodCard") or {}
        nm_id_new = good_card.get("nmID") or row["nm_id"]
        rid_new   = str(good_card.get("rid") or row["rid"] or "")
        reply_sign = chat.get("replySign", "")
        try:
            with db_session() as db:
                db.execute(sql_text("""
                    UPDATE reshipment_chat_tokens SET chat_id = :cid WHERE token = :token
                """), {"cid": chat_id, "token": token_found})
                db.execute(sql_text("""
                    UPDATE reshipment_requests SET wb_chat_id = :cid
                    WHERE chat_token = :token AND wb_chat_id IS NULL
                """), {"cid": chat_id, "token": token_found})
                db.execute(sql_text("""
                    UPDATE wb_chat_pending
                    SET nm_id = COALESCE(:nm, nm_id), rid = COALESCE(:rid, rid),
                        reply_sign = COALESCE(:rs, reply_sign), updated_at = NOW()
                    WHERE chat_id = :cid
                """), {"nm": nm_id_new, "rid": rid_new, "rs": reply_sign, "cid": chat_id})
                db.commit()
            linked += 1
            print(f"[WB Chat] Scan found token in history: token={token_found} chat={chat_id}")
        except Exception as e:
            print(f"[WB Chat] Scan history link error: {e}")

    return linked


def init_event_cursor(get_db=None) -> None:
    """Вызвать при старте: сканируем чаты на токены, ставим курсор триггеров на
    текущий момент (последний уже сохранённый id в wb_chat_messages), чтобы при
    каждом перезапуске backend не переобрабатывать всю накопленную историю
    сообщений продавца через логику триггеров."""
    from sqlalchemy import text as sql_text
    if not _key():
        return
    try:
        # Сначала проактивно сканируем все чаты на токены
        if get_db:
            linked = scan_chats_for_tokens(get_db)
            if linked:
                print(f"[WB Chat] Init scan: linked {linked} token(s) from active chats")

        with db_session() as db:
            _ensure_trigger_cursor(db)
            max_id = db.execute(sql_text("SELECT COALESCE(MAX(id), 0) FROM wb_chat_messages")).scalar() or 0
            db.execute(sql_text("""
                UPDATE wb_chat_trigger_cursor
                SET last_message_id = GREATEST(last_message_id, :v), updated_at = now()
                WHERE id = 1
            """), {"v": max_id})
            db.commit()
        print(f"[WB Chat] Trigger cursor initialized at message id={max_id}")
    except Exception as e:
        print(f"[WB Chat] Init cursor failed (no WB API key?): {e}")
