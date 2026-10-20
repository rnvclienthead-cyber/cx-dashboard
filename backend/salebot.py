import os
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

from . import http_client
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
        resp = http_client.request(
            "GET", url, params={"client_id": client_id}, timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("messenger")
    except Exception as e:
        logger.error("Salebot get_client_messenger error: %s", e)
    return None


def send_message(client_id: str, message: str, db=None, req_id: int | None = None) -> bool:
    """Отправить сообщение подписчику Salebot. Если клиент заблокировал — ставим флаг.

    Повторы — через общий http_client. idempotent=True: уведомление важнее
    не потерять, чем изредка задвоить. 400/403/404/410 (клиент заблокировал)
    помощник не повторяет — отдаём как есть и ставим флаг.
    """
    if not is_enabled():
        logger.debug("Salebot не настроен (нет SALEBOT_API_TOKEN)")
        return False
    url = f"{SALEBOT_API_BASE}/{SALEBOT_API_TOKEN}/message"
    try:
        resp = http_client.request(
            "POST", url, json={"client_id": client_id, "message": message},
            timeout=10, idempotent=True,
        )
    except requests.RequestException as e:
        logger.error("Salebot send_message не удалось: %s", e)
        return False

    if resp.status_code == 200:
        logger.info("Salebot: уведомление отправлено client_id=%s", client_id)
        if db and req_id:
            from sqlalchemy import text
            db.execute(text("UPDATE reshipment_requests SET salebot_blocked=FALSE WHERE id=:id"), {"id": req_id})
            db.commit()
        return True
    if resp.status_code in (400, 403, 404, 410):
        logger.warning("Salebot: HTTP %s — %s", resp.status_code, resp.text[:200])
        if db and req_id:
            from sqlalchemy import text
            db.execute(text("UPDATE reshipment_requests SET salebot_blocked=TRUE WHERE id=:id"), {"id": req_id})
            db.commit()
            logger.info("Salebot: client_id=%s отмечен как заблокированный", client_id)
        return False
    logger.warning("Salebot: HTTP %s — %s", resp.status_code, resp.text[:200])
    return False


def format_approved_message(delivery_method: str = "cdek") -> str:
    """Текст при одобрении заявки. У Ozon нет публичного трек-номера."""
    if (delivery_method or "").strip() == "ozon":
        return (
            "✅ Ваша заявка одобрена!\n"
            "Мы готовим посылку к отправке.\n"
            "Забрать её можно будет в пункте Ozon по штрихкоду в приложении. "
            "Как открыть заказ — следующим сообщением."
        )
    return (
        "✅ Ваша заявка одобрена!\n"
        "Мы готовим посылку к отправке. Когда передадим в доставку — "
        "пришлём трек-номер для отслеживания."
    )


def notify_approved(client_id: str, req_id: int, delivery_method: str = "cdek") -> bool:
    return send_message(client_id, format_approved_message(delivery_method))


_RU_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _msk_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Moscow")
    except Exception:
        return timezone(timedelta(hours=3))


def parse_eta_date(value) -> Optional[date]:
    """Дата прибытия из API перевозчика → календарный день по Москве."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_msk_tz()).date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and float(value) > 10_000_000:
        dt = datetime.fromtimestamp(int(value), tz=timezone.utc)
        return dt.astimezone(_msk_tz()).date()
    raw = str(value).strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{10,13}", raw):
        ts = int(raw[:10])
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.astimezone(_msk_tz()).date()
    raw = raw.replace("Z", "+00:00")
    m = re.search(r"([+-])(\d{2})(\d{2})$", raw)
    if m and ":" not in raw[-5:]:
        raw = raw[: m.start()] + f"{m.group(1)}{m.group(2)}:{m.group(3)}"
    try:
        dt = datetime.fromisoformat(raw[:32])
        if dt.tzinfo is None:
            if "T" in raw:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                return dt.date()
        return dt.astimezone(_msk_tz()).date()
    except Exception:
        try:
            return date.fromisoformat(raw[:10])
        except Exception:
            return None


def format_eta_date(value) -> str:
    """18 сентября"""
    d = parse_eta_date(value)
    if not d:
        return ""
    return f"{d.day} {_RU_MONTHS[d.month - 1]}"


def _eta_block(eta_date) -> str:
    label = format_eta_date(eta_date)
    if not label:
        return ""
    return f"\nОриентировочно в пункте: {label}"


def carrier_label(delivery_method: str = "cdek") -> str:
    method = (delivery_method or "cdek").strip().lower()
    if method == "ozon":
        return "Ozon Доставка"
    if method == "yandex":
        return "Яндекс Доставка"
    return "СДЭК"


def _pvz_block(pvz_address: str = "", pvz_name: str = "") -> str:
    pvz = (pvz_address or "").strip()
    name = (pvz_name or "").strip()
    if pvz and name and name.lower() not in pvz.lower():
        return f"\nПункт выдачи: {name}\nАдрес: {pvz}"
    if pvz:
        return f"\nАдрес пункта выдачи: {pvz}"
    if name:
        return f"\nПункт выдачи: {name}"
    return ""


def format_delivery_status_message(
    status: str,
    *,
    track_number: str = "",
    delivery_method: str = "cdek",
    sharing_url: str = "",
    pvz_address: str = "",
    pvz_name: str = "",
    items_to_send: str = "",
    eta_date: str = "",
) -> str:
    """Текст статус-уведомления для Salebot и чата площадки.

    Состав заявки в текст не входит: короткий список деталей путает клиентов.
    Адрес пункта выдачи показываем во всех статусах, пока посылка ещё в пути.
    """
    del items_to_send  # оставляем аргумент, чтобы не ломать старые вызовы
    carrier = carrier_label(delivery_method)

    if delivery_method == "yandex" and sharing_url:
        track_block = f"Отследить: {sharing_url}"
    elif delivery_method == "ozon" and track_number:
        track_block = (
            f"Номер отправления Ozon: {track_number}\n"
            "Забрать можно в пункте Ozon по штрихкоду в приложении."
        )
    elif delivery_method == "cdek" and track_number:
        track_block = (
            f"Трек-номер СДЭК: {track_number}\n"
            f"Отследить: https://www.cdek.ru/track.html?order_id={track_number}"
        )
    elif track_number:
        track_block = f"Трек-номер: {track_number}"
    else:
        track_block = ""

    pvz_block = _pvz_block(pvz_address, pvz_name)
    eta_block = _eta_block(eta_date)

    if status == "shipped":
        body = (
            "📦 Ваша посылка отправлена!\n"
            f"Служба доставки: {carrier}"
            f"{pvz_block}"
            f"{eta_block}"
        )
        if track_block:
            body += f"\n{track_block}"
        return body

    if status == "in_transit":
        body = (
            "🚚 Ваша посылка в пути!\n"
            f"Служба доставки: {carrier}"
            f"{pvz_block}"
            f"{eta_block}"
        )
        if track_block:
            body += f"\n{track_block}"
        if not eta_block:
            body += "\nСкоро прибудет в ваш город."
        return body

    if status == "ready_for_pickup":
        body = (
            "📬 Ваша посылка готова к выдаче!\n"
            f"Служба доставки: {carrier}"
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

    if status == "returned":
        return "Посылка не была получена в пункте выдачи и возвращается к нам."

    return ""


def format_pickup_reminder(
    *,
    delivery_method: str = "cdek",
    pvz_address: str = "",
    pvz_name: str = "",
    keep_until: str = "",
    sharing_url: str = "",
    track_number: str = "",
) -> str:
    """Повторное напоминание: служба доставки должна совпадать с заявкой."""
    carrier = carrier_label(delivery_method)
    method = (delivery_method or "cdek").strip().lower()
    msg = f"📦 Напоминаем: ваша посылка ожидает вас в пункте выдачи {carrier}."
    msg += _pvz_block(pvz_address, pvz_name)
    if keep_until:
        msg += f"\nСрок бесплатного хранения: {keep_until}."
    if method == "yandex" and sharing_url:
        msg += f"\nОтследить: {sharing_url}"
    elif method == "ozon" and track_number:
        msg += f"\nНомер отправления Ozon: {track_number}"
    msg += "\nПожалуйста, заберите посылку, чтобы не пропустить срок хранения."
    return msg


def notify_shipped(client_id: str, req_id: int, track_number: str,
                   delivery_method: str = "cdek", confirm_url: str = "",
                   sharing_url: str = "", items_to_send: str = "",
                   pvz_address: str = "", pvz_name: str = "",
                   eta_date: str = "") -> bool:
    msg = format_delivery_status_message(
        "shipped",
        track_number=track_number,
        delivery_method=delivery_method,
        sharing_url=sharing_url,
        items_to_send=items_to_send,
        pvz_address=pvz_address,
        pvz_name=pvz_name,
        eta_date=eta_date,
    )
    return send_message(client_id, msg)


def notify_in_transit(client_id: str, req_id: int, track_number: str,
                      delivery_method: str = "cdek", sharing_url: str = "",
                      items_to_send: str = "",
                      pvz_address: str = "", pvz_name: str = "",
                      eta_date: str = "") -> bool:
    msg = format_delivery_status_message(
        "in_transit",
        track_number=track_number,
        delivery_method=delivery_method,
        sharing_url=sharing_url,
        items_to_send=items_to_send,
        pvz_address=pvz_address,
        pvz_name=pvz_name,
        eta_date=eta_date,
    )
    return send_message(client_id, msg)


def notify_ready_for_pickup(client_id: str, req_id: int, track_number: str,
                            delivery_method: str = "cdek", pvz_address: str = "",
                            sharing_url: str = "", items_to_send: str = "",
                            pvz_name: str = "", eta_date: str = "") -> bool:
    msg = format_delivery_status_message(
        "ready_for_pickup",
        track_number=track_number,
        delivery_method=delivery_method,
        sharing_url=sharing_url,
        pvz_address=pvz_address,
        pvz_name=pvz_name,
        items_to_send=items_to_send,
        eta_date=eta_date,
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
