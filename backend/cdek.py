"""СДЭК API v2 — интеграция для доотправки деталей."""
import os
import time
import smtplib
import requests as _req
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

CDEK_BASE       = os.getenv("CDEK_BASE", "https://api.cdek.ru/v2")
CLIENT_ID       = os.getenv("CDEK_CLIENT_ID", "")
CLIENT_SECRET   = os.getenv("CDEK_CLIENT_SECRET", "")
FROM_PVZ_CODE   = os.getenv("CDEK_FROM_PVZ_CODE", "")   # код ПВЗ в Казани, откуда сдаёте
FROM_CITY       = os.getenv("CDEK_FROM_CITY", "Казань")
FROM_CITY_CODE  = int(os.getenv("CDEK_FROM_CITY_CODE", "424"))  # 424 = Казань
TARIFF_CODE     = int(os.getenv("CDEK_TARIFF_CODE", "483"))  # 483 = Экспресс склад→склад (ПВЗ→ПВЗ)
PACKAGE_WEIGHT  = int(os.getenv("CDEK_PACKAGE_WEIGHT_G", "300"))   # граммы
PACKAGE_LENGTH  = int(os.getenv("CDEK_PACKAGE_LENGTH_CM", "15"))
PACKAGE_WIDTH   = int(os.getenv("CDEK_PACKAGE_WIDTH_CM", "10"))
PACKAGE_HEIGHT  = int(os.getenv("CDEK_PACKAGE_HEIGHT_CM", "5"))

# Кэш: pvz_code → city_code
_pvz_city_cache: dict[str, int] = {}

_token: Optional[str] = None
_token_expires: float = 0


# ── Авторизация ────────────────────────────────────────────────────────────────

def _get_token() -> str:
    global _token, _token_expires
    if _token and time.time() < _token_expires - 60:
        return _token
    resp = _req.post(
        f"{CDEK_BASE}/oauth/token",
        data={
            "grant_type":    "client_credentials",
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    _token = data["access_token"]
    _token_expires = time.time() + data.get("expires_in", 3600)
    return _token


def _h() -> dict:
    return {"Authorization": f"Bearer {_get_token()}", "Content-Type": "application/json"}


# ── Список ПВЗ ────────────────────────────────────────────────────────────────

def get_pvz_list(city_name: str) -> list[dict]:
    """Возвращает список ПВЗ СДЭК в заданном городе."""
    # Получаем код города
    r = _req.get(
        f"{CDEK_BASE}/location/cities",
        params={"city": city_name, "country_codes": "RU", "size": 5},
        headers=_h(),
        timeout=10,
    )
    r.raise_for_status()
    cities = r.json()
    if not cities:
        return []
    city_code = cities[0].get("code")

    # Получаем ПВЗ
    r = _req.get(
        f"{CDEK_BASE}/deliverypoints",
        params={"city_code": city_code, "type": "PVZ", "is_handout": True},
        headers=_h(),
        timeout=15,
    )
    r.raise_for_status()
    points = r.json() or []

    result = []
    for p in points:
        loc = p.get("location", {})
        result.append({
            "code":      p.get("code", ""),
            "name":      p.get("name", ""),
            "address":   loc.get("address_full", loc.get("address", "")),
            "work_time": p.get("work_time", ""),
            "phone":     p.get("phones", [{}])[0].get("number", "") if p.get("phones") else "",
        })
    return result


# ── Расчёт стоимости ──────────────────────────────────────────────────────────

def find_nearest_pvz(city_name: str) -> Optional[str]:
    """Найти ближайший/первый ПВЗ в городе. Используется при автосоздании заказа СДЭК."""
    pvzs = get_pvz_list(city_name)
    return pvzs[0]["code"] if pvzs else None


def calculate_cost_by_city(city_name: str) -> float:
    """Рассчитать стоимость по названию города (для шага одобрения КС)."""
    r = _req.get(
        f"{CDEK_BASE}/location/cities",
        params={"city": city_name, "country_codes": "RU", "size": 1},
        headers=_h(), timeout=10,
    )
    r.raise_for_status()
    cities = r.json()
    if not cities:
        return 0.0
    city_code = cities[0].get("code")
    payload = {
        "tariff_code":   TARIFF_CODE,
        "from_location": {"code": FROM_CITY_CODE},
        "to_location":   {"code": city_code},
        "packages": [{"weight": PACKAGE_WEIGHT, "length": PACKAGE_LENGTH, "width": PACKAGE_WIDTH, "height": PACKAGE_HEIGHT}],
    }
    r2 = _req.post(f"{CDEK_BASE}/calculator/tariff", json=payload, headers=_h(), timeout=10)
    r2.raise_for_status()
    return float(r2.json().get("total_sum", 0))


def _pvz_to_city_code(pvz_code: str) -> int:
    """Получить city_code для ПВЗ (нужен калькулятору стоимости)."""
    if pvz_code in _pvz_city_cache:
        return _pvz_city_cache[pvz_code]
    # Получаем список офисов с таким кодом, берём почтовый индекс, ищем город
    r = _req.get(f"{CDEK_BASE}/deliverypoints", params={"code": pvz_code}, headers=_h(), timeout=10)
    r.raise_for_status()
    points = r.json() or []
    if points:
        loc = points[0].get("location", {})
        city_code = loc.get("city_code") or loc.get("code")
        if city_code:
            _pvz_city_cache[pvz_code] = int(city_code)
            return int(city_code)
    return 44  # fallback: Москва


def calculate_cost(to_pvz_code: str) -> float:
    """Возвращает стоимость доставки (руб.) до ПВЗ клиента. Тариф 483: склад→склад."""
    to_city_code = _pvz_to_city_code(to_pvz_code)
    payload = {
        "tariff_code":   TARIFF_CODE,
        "from_location": {"code": FROM_CITY_CODE},
        "to_location":   {"code": to_city_code},
        "packages": [{
            "weight": PACKAGE_WEIGHT,
            "length": PACKAGE_LENGTH,
            "width":  PACKAGE_WIDTH,
            "height": PACKAGE_HEIGHT,
        }],
    }
    r = _req.post(f"{CDEK_BASE}/calculator/tariff", json=payload, headers=_h(), timeout=10)
    r.raise_for_status()
    data = r.json()
    return float(data.get("total_sum", 0))


# ── Создание заказа ───────────────────────────────────────────────────────────

def create_order(
    req_id:       int,
    customer_name: str,
    customer_phone: str,
    to_pvz_code:  str,
    items_description: str = "Комплектующие",
) -> dict:
    """
    Создаёт заказ в СДЭК. Возвращает {'uuid': ..., 'cdek_number': ...}.
    cdek_number может быть пустым — придёт через webhook.
    """
    # Тариф 483 склад→склад: from_location = город отправителя, delivery_point = ПВЗ получателя
    payload = {
        "tariff_code":    TARIFF_CODE,
        "from_location":  {"code": FROM_CITY_CODE},
        "delivery_point": to_pvz_code,
        "comment":        f"Доотправка #{req_id}: {items_description[:200]}",
        "sender":   {"name": "Видовит"},
        "recipient": {
            "name":   customer_name,
            "phones": [{"number": customer_phone}],
        },
        "packages": [{
            "number": f"SHIP-{req_id}",
            "weight": PACKAGE_WEIGHT,
            "length": PACKAGE_LENGTH,
            "width":  PACKAGE_WIDTH,
            "height": PACKAGE_HEIGHT,
            "items": [{
                "name":     items_description[:50] or "Комплектующие",
                "ware_key": f"PART-{req_id}",
                "payment":  {"value": 0},
                "cost":     100,
                "amount":   1,
                "weight":   PACKAGE_WEIGHT,
            }],
        }],
    }
    r = _req.post(f"{CDEK_BASE}/orders", json=payload, headers=_h(), timeout=15)
    r.raise_for_status()
    data = r.json()
    entity = data.get("entity") or {}
    return {
        "uuid":        entity.get("uuid", ""),
        "cdek_number": entity.get("cdek_number") or "",
    }


# ── Получить заказ по UUID (для получения cdek_number) ────────────────────────

def get_order_by_uuid(uuid: str) -> dict:
    r = _req.get(f"{CDEK_BASE}/orders/{uuid}", headers=_h(), timeout=10)
    r.raise_for_status()
    data = r.json()
    entity = data.get("entity") or {}
    return {
        "cdek_number": entity.get("cdek_number") or "",
        "status":      (entity.get("statuses") or [{}])[-1].get("name", ""),
    }


# ── Регистрация webhook ───────────────────────────────────────────────────────

def register_webhook(url: str) -> dict:
    """Регистрирует webhook для статусов заказа."""
    r = _req.post(
        f"{CDEK_BASE}/webhooks",
        json={"url": url, "type": "ORDER_STATUS"},
        headers=_h(),
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


# ── Email уведомление при отклонении ─────────────────────────────────────────

def send_rejection_email(
    to_email:         str,
    customer_name:    str,
    rejection_reason: str,
    req_id:           int,
) -> bool:
    """Отправляет клиенту email об отклонении заявки. Возвращает True если успешно."""
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")

    if not smtp_user or not smtp_pass or smtp_pass == "REPLACE_WITH_GMAIL_APP_PASSWORD":
        return False

    msg = MIMEMultipart()
    msg["From"]    = smtp_user
    msg["To"]      = to_email
    msg["Subject"] = f"Заявка #{req_id} на доотправку — обновление статуса"

    body = f"""Здравствуйте, {customer_name}!

Мы рассмотрели вашу заявку #{req_id} на доотправку детали.

К сожалению, в настоящий момент у нас нет возможности выполнить отправку.

Причина: {rejection_reason}

В качестве альтернативы предлагаем оформить возврат товара. \
Пожалуйста, свяжитесь с нашей службой поддержки, и мы поможем \
организовать возврат удобным для вас способом.

С уважением,
Команда поддержки Видовит"""

    msg.attach(MIMEText(body, "plain", "utf-8"))
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as srv:
            srv.starttls()
            srv.login(smtp_user, smtp_pass)
            srv.send_message(msg)
        return True
    except Exception as exc:
        print(f"[CDEK] email error: {exc}")
        return False
