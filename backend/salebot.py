import os
import logging
import requests
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

SALEBOT_API_TOKEN = os.getenv("SALEBOT_API_TOKEN", "")
SALEBOT_API_BASE  = "https://chatter.salebot.pro/api"


def is_enabled() -> bool:
    return bool(SALEBOT_API_TOKEN)


def get_client_messenger(client_id: str) -> str | None:
    """Возвращает тип мессенджера клиента: 'Telegram', 'Max' и т.д."""
    if not is_enabled():
        return None
    try:
        url = f"{SALEBOT_API_BASE}/{SALEBOT_API_TOKEN}/get_variables"
        resp = requests.get(url, params={"client_id": client_id}, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("messenger")
    except Exception as e:
        logger.error("Salebot get_client_messenger error: %s", e)
    return None


def send_message(client_id: str, message: str, db=None, req_id: int | None = None) -> bool:
    """Отправить сообщение подписчику Salebot. Если клиент заблокировал — ставим флаг."""
    if not is_enabled():
        logger.debug("Salebot не настроен (нет SALEBOT_API_TOKEN)")
        return False
    try:
        url = f"{SALEBOT_API_BASE}/{SALEBOT_API_TOKEN}/message"
        resp = requests.post(url, json={"client_id": client_id, "message": message}, timeout=10)
        if resp.status_code == 200:
            logger.info("Salebot: уведомление отправлено client_id=%s", client_id)
            if db and req_id:
                from sqlalchemy import text
                db.execute(text("UPDATE reshipment_requests SET salebot_blocked=FALSE WHERE id=:id"), {"id": req_id})
                db.commit()
            return True
        logger.warning("Salebot: HTTP %s — %s", resp.status_code, resp.text[:200])
        if resp.status_code in (400, 403, 404, 410) and db and req_id:
            from sqlalchemy import text
            db.execute(text("UPDATE reshipment_requests SET salebot_blocked=TRUE WHERE id=:id"), {"id": req_id})
            db.commit()
            logger.info("Salebot: client_id=%s отмечен как заблокированный", client_id)
        return False
    except Exception as e:
        logger.error("Salebot send_message error: %s", e)
        return False


def notify_approved(client_id: str, req_id: int) -> bool:
    msg = (
        "✅ Ваша заявка одобрена!\n"
        "Мы готовим отправку. Как только посылка уйдёт, вы получите трек-номер."
    )
    return send_message(client_id, msg)


def _short_items(items_to_send: str = "", limit: int = 120) -> str:
    """Первая строка состава заявки, укороченная для чата."""
    raw = (items_to_send or "").replace("\r", "\n").strip()
    if not raw:
        return ""
    line = raw.split("\n", 1)[0].strip()
    line = " ".join(line.split())
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def format_delivery_status_message(
    status: str,
    *,
    track_number: str = "",
    delivery_method: str = "cdek",
    sharing_url: str = "",
    pvz_address: str = "",
    pvz_name: str = "",
    items_to_send: str = "",
) -> str:
    """Текст статус-уведомления для Salebot и WB-чата.

    Для Яндекса приоритет — sharing_url (живая ссылка на маршрут/ПВЗ),
    плюс адрес пункта и краткий состав заявки.
    """
    carrier = "СДЭК" if delivery_method == "cdek" else "Яндекс Доставка"
    items = _short_items(items_to_send)
    items_line = f"\nЧто внутри: {items}" if items else ""

    if delivery_method == "yandex" and sharing_url:
        track_block = f"Отследить: {sharing_url}"
    elif delivery_method == "cdek" and track_number:
        track_block = (
            f"Трек-номер СДЭК: {track_number}\n"
            f"Отследить: https://www.cdek.ru/track.html?order_id={track_number}"
        )
    elif track_number:
        track_block = f"Трек-номер: {track_number}"
    else:
        track_block = ""

    pvz = (pvz_address or "").strip()
    name = (pvz_name or "").strip()
    if pvz and name and name.lower() not in pvz.lower():
        pvz_block = f"\nПункт выдачи: {name}\nАдрес: {pvz}"
    elif pvz:
        pvz_block = f"\nАдрес ПВЗ: {pvz}"
    elif name:
        pvz_block = f"\nПункт выдачи: {name}"
    else:
        pvz_block = ""

    if status == "shipped":
        body = (
            "📦 Ваша посылка отправлена!\n"
            f"Служба доставки: {carrier}"
            f"{items_line}"
        )
        if track_block:
            body += f"\n{track_block}"
        return body

    if status == "in_transit":
        body = (
            "🚚 Ваша посылка в пути!\n"
            f"Служба доставки: {carrier}"
            f"{items_line}"
        )
        if track_block:
            body += f"\n{track_block}"
        body += "\nСкоро прибудет в ваш город."
        return body

    if status == "ready_for_pickup":
        body = (
            "📬 Ваша посылка готова к выдаче!\n"
            f"Служба доставки: {carrier}"
            f"{items_line}"
            f"{pvz_block}"
        )
        if not pvz_block:
            body += f"\nЗаберите её в пункте выдачи {carrier}."
        if track_block:
            body += f"\n{track_block}"
        return body

    if status == "delivered":
        return (
            "✅ Посылка получена!\n"
            "Надеемся, деталь подошла. Если есть вопросы — мы всегда на связи."
        )

    return ""


def notify_shipped(client_id: str, req_id: int, track_number: str,
                   delivery_method: str = "cdek", confirm_url: str = "",
                   sharing_url: str = "", items_to_send: str = "",
                   pvz_address: str = "", pvz_name: str = "") -> bool:
    msg = format_delivery_status_message(
        "shipped",
        track_number=track_number,
        delivery_method=delivery_method,
        sharing_url=sharing_url,
        items_to_send=items_to_send,
        pvz_address=pvz_address,
        pvz_name=pvz_name,
    )
    return send_message(client_id, msg)


def notify_in_transit(client_id: str, req_id: int, track_number: str,
                      delivery_method: str = "cdek", sharing_url: str = "",
                      items_to_send: str = "",
                      pvz_address: str = "", pvz_name: str = "") -> bool:
    msg = format_delivery_status_message(
        "in_transit",
        track_number=track_number,
        delivery_method=delivery_method,
        sharing_url=sharing_url,
        items_to_send=items_to_send,
        pvz_address=pvz_address,
        pvz_name=pvz_name,
    )
    return send_message(client_id, msg)


def notify_ready_for_pickup(client_id: str, req_id: int, track_number: str,
                            delivery_method: str = "cdek", pvz_address: str = "",
                            sharing_url: str = "", items_to_send: str = "",
                            pvz_name: str = "") -> bool:
    msg = format_delivery_status_message(
        "ready_for_pickup",
        track_number=track_number,
        delivery_method=delivery_method,
        sharing_url=sharing_url,
        pvz_address=pvz_address,
        pvz_name=pvz_name,
        items_to_send=items_to_send,
    )
    return send_message(client_id, msg)


def notify_delivered(client_id: str, req_id: int) -> bool:
    msg = (
        "✅ Посылка получена!\n"
        "Надеемся, деталь подошла. Если есть вопросы — мы всегда на связи."
    )
    return send_message(client_id, msg)


def notify_rejected(client_id: str, req_id: int, reason: str = "") -> bool:
    msg = (
        "К сожалению, мы не можем выполнить вашу заявку на отправку детали — "
        "запрашиваемая позиция отсутствует на нашем складе. "
        "Рекомендуем оформить возврат в личном кабинете Wildberries."
    )
    return send_message(client_id, msg)
