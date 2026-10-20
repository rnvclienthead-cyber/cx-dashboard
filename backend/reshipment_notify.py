"""
Антидубль клиентских уведомлений по статусам доотправки.

Одна заявка + один статус = одно сообщение клиенту (WB или Salebot),
даже если сработали и вебхук, и опросник, и два процесса uvicorn.

Галка «уже отправили» живёт только если сообщение реально ушло.
Иначе claim снимаем — следующий цикл попробует снова.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session
import logging
import re

logger = logging.getLogger("reshipment_notify")

_URL_RE = re.compile(r"https?://\S+", re.I)


def init_notify_table(engine):
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS reshipment_client_notifies (
                request_id  INTEGER NOT NULL,
                status      TEXT    NOT NULL,
                channel     TEXT    DEFAULT '',
                created_at  TIMESTAMP DEFAULT now(),
                PRIMARY KEY (request_id, status)
            )
        """))
        conn.commit()


def claim_notify(db: Session, request_id: int, status: str) -> bool:
    """
    Забирает право отправить уведомление по статусу.
    True  — ещё не слали, можно отправлять.
    False — уже слали (или ошибка) — молчать.
    """
    if not request_id or not status:
        return False
    try:
        row = db.execute(text("""
            INSERT INTO reshipment_client_notifies (request_id, status)
            VALUES (:id, :st)
            ON CONFLICT (request_id, status) DO NOTHING
            RETURNING request_id
        """), {"id": int(request_id), "st": status}).fetchone()
        db.commit()
        return row is not None
    except Exception as e:
        logger.warning("claim_notify #%s %s: %s", request_id, status, e)
        try:
            db.rollback()
        except Exception:
            pass
        return False


def release_notify(db: Session, request_id: int, status: str) -> None:
    """Снимает галку, если отправка не удалась — чтобы не молчать навсегда."""
    if not request_id or not status:
        return
    try:
        db.execute(text("""
            DELETE FROM reshipment_client_notifies
            WHERE request_id = :id AND status = :st
        """), {"id": int(request_id), "st": status})
        db.commit()
    except Exception as e:
        logger.warning("release_notify #%s %s: %s", request_id, status, e)
        try:
            db.rollback()
        except Exception:
            pass


def _without_urls(text_msg: str) -> str:
    cleaned = _URL_RE.sub("", text_msg or "")
    lines = []
    for raw in cleaned.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().rstrip(":") == "отследить":
            continue
        lines.append(raw.rstrip())
    return "\n".join(lines).strip()


def ozon_chat_writable(db: Session | None, chat_id: str) -> bool:
    """Ozon разрешает писать примерно 48 часов после последнего сообщения покупателя."""
    cid = str(chat_id or "").strip()
    if not cid or db is None:
        return True
    try:
        last = db.execute(text("""
            SELECT max(created_at) FROM ozon_chat_messages
            WHERE chat_id = :c
              AND lower(COALESCE(sender_type, '')) IN ('customer', 'buyer', 'client')
        """), {"c": cid}).scalar()
        if not last:
            return True
        from datetime import datetime, timedelta, timezone
        if getattr(last, "tzinfo", None) is None:
            last = last.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last < timedelta(hours=48)
    except Exception as e:
        logger.warning("ozon_chat_writable %s: %s", cid, e)
        return True


def send_marketplace_message(row: dict, msg: str, db: Session | None = None) -> bool:
    """Чат площадки: Ozon, ЯМ, иначе WB. Пустой текст — молчим."""
    text_msg = str(msg or "").strip()
    if not text_msg:
        return False
    oz = str(row.get("ozon_chat_id") or "").strip()
    mp = str(row.get("marketplace") or "").strip().lower()
    if oz or mp == "ozon":
        if not oz:
            return False
        if not ozon_chat_writable(db, oz):
            logger.warning("Ozon notify #%s: chat closed, wait for customer", row.get("id"))
            return False
        try:
            from . import ozon_chat_sync
            ozon_chat_sync.send_message(oz, text_msg)
            return True
        except Exception as e:
            logger.warning("Ozon notify #%s: %s", row.get("id"), e)
            return False
    ym = str(row.get("ym_chat_id") or "").strip()
    if ym or mp == "ym":
        if not ym:
            return False
        try:
            from . import ym_chat_sync
            ym_chat_sync.send_message(ym, text_msg)
            return True
        except Exception as e:
            logger.warning("YM notify #%s: %s", row.get("id"), e)
            stripped = _without_urls(text_msg)
            if stripped and stripped != text_msg:
                try:
                    ym_chat_sync.send_message(ym, stripped)
                    return True
                except Exception as e2:
                    logger.warning("YM notify retry #%s: %s", row.get("id"), e2)
            return False
    sign = str(row.get("wb_reply_sign") or "").strip()
    if not sign:
        chat_id = str(row.get("wb_chat_id") or "").strip()
        if chat_id:
            try:
                from . import wb_chat
                sign = (wb_chat.resolve_reply_sign(chat_id) or "").strip()
            except Exception as e:
                logger.warning("WB resolve sign #%s: %s", row.get("id"), e)
    if not sign:
        return False
    try:
        from . import wb_chat
        wb_chat.send_message(sign, text_msg)
        return True
    except Exception as e:
        logger.warning("WB notify #%s: %s", row.get("id"), e)
        return False


OZON_PICKUP_STEPS = (
    "1. На главном экране нажмите «Ozon Доставка».",
    "2. Внизу нажмите «Заказы».",
    "3. Откройте вкладку «Получения».",
    "4. Нажмите «В ожидании».",
    "5. Откройте заказ магазина Видовито.",
    "6. Здесь статус заказа. Штрихкод для получения появится, когда посылка будет в пункте выдачи.",
)


def format_ozon_pickup_guide() -> str:
    """Текст инструкции: как открыть заказ Ozon Доставки в приложении."""
    lines = [
        "Как забрать деталь в пункте Ozon:",
        "",
        "Откройте приложение Ozon на том телефоне, с которого оформляли доставку. "
        "Заказ будет не в обычных покупках, а в разделе Ozon Доставка.",
        "",
        *OZON_PICKUP_STEPS,
        "",
        "Посылку отдадут по штрихкоду из приложения, когда она придёт в пункт.",
    ]
    return "\n".join(lines)


def send_ozon_pickup_guide(row: dict, db: Session | None = None) -> bool:
    """После одобрения Ozon: текстовая инструкция в чат площадки и в Max."""
    method = str((row or {}).get("delivery_method") or "").strip().lower()
    if method != "ozon":
        return False
    msg = format_ozon_pickup_guide()
    chat_ok = send_marketplace_message(row, msg, db=db)
    sid = str((row or {}).get("salebot_client_id") or "").strip()
    salebot_ok = False
    if sid:
        try:
            from .salebot import is_enabled, send_message
            if is_enabled():
                salebot_ok = bool(send_message(sid, msg, req_id=row.get("id")))
        except Exception as e:
            logger.warning("salebot guide #%s: %s", row.get("id"), e)
    if chat_ok or salebot_ok:
        return True
    logger.warning("ozon pickup guide not delivered #%s", row.get("id"))
    return False


def _salebot_status(row: dict, status: str, **kwargs) -> bool:
    sid = str(row.get("salebot_client_id") or "").strip()
    if not sid:
        return False
    try:
        from .salebot import (
            is_enabled, notify_shipped, notify_in_transit,
            notify_ready_for_pickup, notify_delivered,
        )
        if not is_enabled():
            return False
        req_id = row.get("id")
        if status == "shipped":
            return bool(notify_shipped(sid, req_id, **kwargs))
        if status == "in_transit":
            return bool(notify_in_transit(sid, req_id, **kwargs))
        if status == "ready_for_pickup":
            return bool(notify_ready_for_pickup(sid, req_id, **kwargs))
        if status == "delivered":
            return bool(notify_delivered(sid, req_id))
    except Exception as e:
        logger.warning("salebot notify #%s %s: %s", row.get("id"), status, e)
    return False


def notify_delivery_status(
    db: Session,
    row: dict,
    status: str,
    *,
    delivery_method: str,
    track_number: str = "",
    sharing_url: str = "",
    pvz_address: str = "",
    pvz_name: str = "",
    items_to_send: str = "",
    eta_date: str = "",
) -> bool:
    """Salebot, иначе чат площадки. Не дошло — галку снимаем."""
    if not row or not status:
        return False
    from .salebot import format_delivery_status_message
    kwargs = dict(
        track_number=track_number or "",
        delivery_method=delivery_method or "cdek",
        sharing_url=sharing_url or "",
        pvz_address=pvz_address or "",
        pvz_name=pvz_name or "",
        items_to_send=items_to_send or "",
        eta_date=eta_date or "",
    )
    msg = format_delivery_status_message(status, **kwargs)
    if not claim_notify(db, row.get("id"), status):
        return False
    sent = _salebot_status(row, status, **kwargs)
    if not sent:
        sent = send_marketplace_message(row, msg, db=db)
    if sent:
        return True
    release_notify(db, row.get("id"), status)
    logger.warning("notify not delivered, will retry #%s %s", row.get("id"), status)
    return False
