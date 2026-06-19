"""Яндекс Доставка — B2B Cargo API integration."""
import os
import uuid as _uuid
import logging
import requests as _req

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

YD_API_KEY = os.getenv("YANDEX_DELIVERY_API_KEY", "")
YD_BASE    = "https://b2b.taxi.yandex.net/b2b/cargo/integration/v2"

# Наш склад-отправитель в Казани
FROM_FULLNAME = os.getenv("YD_FROM_FULLNAME", "Казань, улица Техническая, 23Б")
FROM_COORDS   = [float(x) for x in os.getenv("YD_FROM_COORDS", "49.1064,55.7961").split(",")]
FROM_NAME     = os.getenv("YD_FROM_NAME", "Видовито")
FROM_PHONE    = os.getenv("YD_FROM_PHONE", "+79172249575")


def is_enabled() -> bool:
    return bool(YD_API_KEY)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {YD_API_KEY}",
        "Accept-Language": "ru",
        "Content-Type": "application/json",
    }


def create_claim(
    req_id: int,
    customer_name: str,
    customer_phone: str,
    to_fullname: str,
    items_description: str = "Комплектующие мебели",
    declared_cost: float = 500.0,
) -> dict:
    """
    Создаёт заявку Яндекс Доставки.
    Возвращает {'claim_id': ..., 'status': ...}.
    Статус может быть 'new'/'estimating'/'estimating_failed' и т.д.
    """
    request_id = f"ship-{req_id}-{_uuid.uuid4().hex[:8]}"
    payload = {
        "route_points": [
            {
                "point_id":    1,
                "visit_order": 1,
                "address":     {"fullname": FROM_FULLNAME, "coordinates": FROM_COORDS},
                "type":        "source",
                "contact":     {"name": FROM_NAME, "phone": FROM_PHONE},
            },
            {
                "point_id":    2,
                "visit_order": 2,
                "address":     {"fullname": to_fullname},
                "type":        "destination",
                "contact":     {"name": customer_name, "phone": customer_phone},
            },
        ],
        "items": [{
            "title":         items_description[:50],
            "size":          {"length": 0.15, "width": 0.10, "height": 0.05},
            "weight":        0.3,
            "quantity":      1,
            "cost_value":    str(int(declared_cost)),
            "cost_currency": "RUB",
            "droppof_point": 2,
            "pickup_point":  1,
        }],
        "cargo_type": "cargo",
        "comment":    f"Доотправка #{req_id}: {items_description[:100]}",
    }
    resp = _req.post(
        f"{YD_BASE}/claims/create",
        params={"request_id": request_id},
        json=payload,
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    claim_id = data.get("id", "")
    status   = data.get("status", "")
    logger.info("YD claim created: claim_id=%s status=%s req_id=%s", claim_id, status, req_id)
    return {"claim_id": claim_id, "status": status}


def get_claim(claim_id: str) -> dict:
    """Получить статус заявки."""
    resp = _req.post(
        f"{YD_BASE}/claims/info",
        params={"claim_id": claim_id},
        headers=_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "claim_id": data.get("id", ""),
        "status":   data.get("status", ""),
        "version":  data.get("version", 1),
        "pricing":  data.get("pricing", {}),
    }


def cancel_claim(claim_id: str, version: int = 1) -> bool:
    """Отменить заявку."""
    resp = _req.post(
        f"{YD_BASE}/claims/cancel",
        params={"claim_id": claim_id},
        json={"cancel_state": "free", "version": version},
        headers=_headers(),
        timeout=10,
    )
    return resp.status_code == 200
