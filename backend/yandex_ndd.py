"""Яндекс Доставка по России (NDD) — расчёт цены, поиск ПВЗ и создание заказа склад(ПВЗ)->ПВЗ.

Используется тот же YANDEX_DELIVERY_API_KEY, что и в yandex_delivery.py (Express/Cargo) —
это другой продукт Яндекса, но авторизация общая для аккаунта.

Заказ создаётся полностью через API (offers/create -> offers/confirm), без выданного
менеджером platform_station_id склада — источником может быть обычный ПВЗ (куда мы физически
привозим посылку), так же как и destination. Ключевое поле точки — available_for_dropoff:
только такие точки принимают отправления от нас (не все ПВЗ, что выдают заказы, их принимают).
"""
import os
import math
import logging
import uuid as _uuid
from typing import Optional

import requests as _req

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

YD_API_KEY = os.getenv("YANDEX_DELIVERY_API_KEY", "")
NDD_BASE   = "https://b2b.taxi.yandex.net/api/b2b/platform"

# Наш склад-отправитель (Казань) и ближайший к нему ПВЗ Яндекса, принимающий отправления —
# туда физически везём посылки. ID найден один раз (см. FROM_PVZ_ADDRESS) и захардкожен в .env,
# чтобы не искать заново на каждый запрос — при переезде склада обновить оба значения.
FROM_ADDRESS     = os.getenv("YD_FROM_FULLNAME", "Казань, улица Техническая, 23Б")
FROM_PVZ_ADDRESS = os.getenv("YD_NDD_FROM_PVZ_ADDRESS", "Казань, улица Чернышевского, 33")
FROM_PVZ_ID      = os.getenv("YD_NDD_FROM_PVZ_ID", "019840850cff72889d44bbe3f4d4a81e")

_BAD_GEO_WORDS = ("село", "деревня", "станица", "снт", "садоводческ", "посёлок", "кп ")


def is_enabled() -> bool:
    return bool(YD_API_KEY)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {YD_API_KEY}",
        "Content-Type": "application/json",
        "Accept-Language": "ru",
    }


def _post(path: str, body: dict, timeout: int = 15) -> tuple[int, dict]:
    resp = _req.post(f"{NDD_BASE}{path}", json=body, headers=_headers(), timeout=timeout)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data


def _get(path: str, params: dict, timeout: int = 15) -> tuple[int, dict]:
    resp = _req.get(f"{NDD_BASE}{path}", params=params, headers=_headers(), timeout=timeout)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data


def detect_geo_id(location: str) -> Optional[dict]:
    """Резолвит адрес/город в geo_id. Отфильтровывает мусорные варианты (сёла-омонимы)."""
    sc, sd = _post("/location/detect", {"location": location})
    if sc != 200:
        return None
    variants = sd.get("variants", [])
    for v in variants:
        if not any(w in v["address"].lower() for w in _BAD_GEO_WORDS):
            return v
    return variants[0] if variants else None


def list_pickup_points(geo_id: int, lat: Optional[float] = None, lon: Optional[float] = None,
                        delta: float = 0.15, dropoff_only: bool = False) -> list[dict]:
    """
    Список ПВЗ Яндекса в зоне geo_id. Если заданы координаты — сужает окно вокруг них.
    dropoff_only=True — только точки, принимающие отправления от нас (available_for_dropoff),
    нужно для подбора СВОЕЙ точки приёма. Для точки выдачи клиенту фильтр не нужен.
    """
    body = {"geo_id": geo_id, "payment_method": "already_paid", "type": "pickup_point"}
    if lat is not None and lon is not None:
        body["latitude"]  = {"from": lat - delta, "to": lat + delta}
        body["longitude"] = {"from": lon - delta, "to": lon + delta}
    sc, sd = _post("/pickup-points/list", body)
    if sc != 200:
        return []
    points = sd.get("points", [])
    result = []
    for p in points:
        if dropoff_only and not p.get("available_for_dropoff"):
            continue
        addr = p.get("address", {})
        pos = p.get("position", {})
        result.append({
            "id":                    p.get("id", ""),
            "name":                  p.get("name", ""),
            "address":               addr.get("full_address", ""),
            "work_time":             "",
            "phone":                 (p.get("contact") or {}).get("phone", ""),
            "lat":                   pos.get("latitude"),
            "lng":                   pos.get("longitude"),
            "available_for_dropoff": p.get("available_for_dropoff", False),
        })
    return result


def _dist(lat1, lon1, lat2, lon2) -> float:
    """Расстояние в км (haversine). math.hypot по градусам некорректен —
    на широте Казани градус долготы почти вдвое короче градуса широты."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def nearest_pickup_point(geo_id: int, target_lat: float, target_lon: float,
                          delta: float = 0.15, dropoff_only: bool = False) -> Optional[dict]:
    """Ближайший к заданным координатам ПВЗ Яндекса."""
    points = list_pickup_points(geo_id, target_lat, target_lon, delta=delta, dropoff_only=dropoff_only)
    points = [p for p in points if p["lat"] is not None and p["lng"] is not None]
    if not points:
        return None
    return min(points, key=lambda p: _dist(target_lat, target_lon, p["lat"], p["lng"]))


def calculate_price(destination_platform_station_id: str, total_weight_g: int = 300,
                     declared_cost: float = 500.0, source_address: str = FROM_PVZ_ADDRESS) -> Optional[float]:
    """Цена доставки ПВЗ->ПВЗ (для предварительного сравнения с СДЭК). Возвращает рубли или None."""
    sc, sd = _post("/pricing-calculator", {
        "client_price": 0,
        "destination": {"platform_station_id": destination_platform_station_id},
        "payment_method": "already_paid",
        "source": {"address": source_address},
        "tariff": "time_interval",
        "total_assessed_price": declared_cost,
        "total_weight": total_weight_g,
    })
    if sc != 200:
        logger.info("NDD pricing-calculator failed: %s %s", sc, sd)
        return None
    raw = sd.get("pricing_total", "")
    try:
        return float(raw.split()[0])
    except (ValueError, IndexError):
        return None


def create_order(
    req_id: int,
    customer_name: str,
    customer_phone: str,
    destination_pvz_id: str,
    items_description: str = "Комплектующие мебели",
    weight_g: int = 300,
    declared_cost: float = 500.0,
    source_pvz_id: str = FROM_PVZ_ID,
) -> dict:
    """
    Полное автосоздание заказа: offers/create -> offers/confirm.
    Источник и назначение — обычные ПВЗ (platform_station по id), склад регистрировать не нужно.
    Возвращает {'request_id': ..., 'price': float|None}. Бросает исключение при ошибке API.
    """
    name_parts = (customer_name or "Клиент").strip().split(maxsplit=1)
    first_name = name_parts[0] if name_parts else "Клиент"
    last_name  = name_parts[1] if len(name_parts) > 1 else "-"
    phone_digits = "".join(c for c in customer_phone if c.isdigit())

    body = {
        "info": {
            "operator_request_id": f"reship-{req_id}-{_uuid.uuid4().hex[:8]}",
            "comment": f"Доотправка #{req_id}: {items_description[:150]}",
        },
        "last_mile_policy": 0,
        "source": {"platform_station": {"platform_id": source_pvz_id}},
        "destination": {"type": 1, "platform_station": {"platform_id": destination_pvz_id}},
        "items": [{
            "count": 1,
            "name": items_description[:50] or "Комплектующие",
            "article": f"SHIP-{req_id}",
            "billing_details": {"unit_price": int(declared_cost), "assessed_unit_price": int(declared_cost)},
            "physical_dims": {"predefined_volume": 1, "weight_gross": weight_g},
            "place_barcode": f"SHIP-{req_id}",
        }],
        "places": [{"physical_dims": {"predefined_volume": 100}, "barcode": f"SHIP-{req_id}"}],
        "billing_info": {"payment_method": 0},
        "recipient_info": {
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone_digits,
        },
    }

    sc, sd = _post("/offers/create", body)
    if sc != 200 or not sd.get("offers"):
        raise RuntimeError(f"offers/create failed: {sc} {sd}")
    offer = sd["offers"][0]
    price = None
    try:
        price = float(offer["offer_details"]["pricing_total"].split()[0])
    except (KeyError, ValueError, IndexError):
        pass

    sc2, sd2 = _post("/offers/confirm", {"offer_id": offer["offer_id"]})
    if sc2 != 200 or not sd2.get("request_id"):
        raise RuntimeError(f"offers/confirm failed: {sc2} {sd2}")

    logger.info("NDD order created: request_id=%s price=%s req_id=%s", sd2["request_id"], price, req_id)
    return {"request_id": sd2["request_id"], "price": price}


def get_order_status(request_id: str) -> dict:
    """Статус заказа по request_id."""
    sc, sd = _get("/request/info", {"request_id": request_id})
    if sc != 200:
        return {"status": "unknown"}
    state = sd.get("state", {})
    return {
        "status":      state.get("status", ""),
        "description": state.get("description", ""),
        "courier_order_id": sd.get("courier_order_id"),
        "sharing_url": sd.get("sharing_url"),
    }


def cancel_order(request_id: str) -> bool:
    """Отменить заказ."""
    sc, sd = _post("/request/cancel", {"request_id": request_id})
    return sc == 200
