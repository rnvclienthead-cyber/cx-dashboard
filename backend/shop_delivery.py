"""Расчёт доставки для витрины lk.vidovito.com: СДЭК, Яндекс и Ozon, как в доотправках."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

from . import cdek
from . import yandex_ndd
from . import ozon_delivery
from .pickup_resolver import (
    RUSSIA_ONLY_MESSAGE,
    STREET_REQUIRED_MESSAGE,
    address_missing_street,
    get_both_candidates,
    is_foreign_address_hint,
    list_cdek_points,
    list_ozon_points,
    list_yandex_points,
    resolve_address,
    suggest_addresses,
)

SHOP_TOKEN = os.getenv("SHOP_DELIVERY_TOKEN", "")
DEFAULT_WEIGHT_G = 2000
_POSTAMAT_RE = re.compile(
    r"постамат|почтомат|postamat|ozon\s*box|ozonbox|locker|lockbox|parcel.?lock|халва",
    re.I,
)
_CACHE_TTL = 45
_cache: dict[tuple, tuple[float, dict]] = {}
_points_cache: dict[tuple, tuple[float, dict]] = {}


def parse_packages(raw) -> list:
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:30]:
        if not isinstance(item, dict):
            continue
        weight = int(item.get("weight_g") or item.get("weight") or 0)
        length = int(item.get("length_cm") or item.get("length") or 0)
        width = int(item.get("width_cm") or item.get("width") or 0)
        height = int(item.get("height_cm") or item.get("height") or 0)
        if weight < 1:
            weight = DEFAULT_WEIGHT_G
        if length < 1 or width < 1 or height < 1:
            length, width, height = 40, 30, 20
        out.append({
            "weight_g": weight,
            "length_cm": length,
            "width_cm": width,
            "height_cm": height,
        })
    return out


def packages_weight_g(packages: list, fallback: int) -> int:
    if not packages:
        return max(1, int(fallback or DEFAULT_WEIGHT_G))
    return max(1, sum(int(p["weight_g"]) for p in packages))


def packages_outer_mm(packages: list, weight_g: int) -> dict:
    if not packages:
        return {
            "weight_g": max(1, int(weight_g or DEFAULT_WEIGHT_G)),
            "length_mm": 400,
            "width_mm": 300,
            "height_mm": 200,
        }
    return {
        "weight_g": packages_weight_g(packages, weight_g),
        "length_mm": max(int(p["length_cm"]) for p in packages) * 10,
        "width_mm": max(int(p["width_cm"]) for p in packages) * 10,
        "height_mm": sum(int(p["height_cm"]) for p in packages) * 10,
    }


def packages_cache_key(packages: list) -> tuple:
    return tuple(
        (int(p["weight_g"]), int(p["length_cm"]), int(p["width_cm"]), int(p["height_cm"]))
        for p in packages
    )


def shipment_units(packages: Optional[list], weight_g: int) -> list:
    """Каждая коробка из корзины — отдельное отправление, как на складе."""
    pkgs = parse_packages(packages)
    if pkgs:
        return pkgs
    return [{
        "weight_g": max(1, int(weight_g or DEFAULT_WEIGHT_G)),
        "length_cm": 40,
        "width_cm": 30,
        "height_cm": 20,
    }]


def _declared_shares(declared_cost: float, count: int) -> list:
    n = max(1, int(count))
    total = float(declared_cost or 500)
    base = round(total / n, 2)
    shares = [base] * n
    shares[-1] = round(total - base * (n - 1), 2)
    return shares


def _sum_costs(values: list) -> Optional[float]:
    if not values:
        return None
    parts = []
    for value in values:
        if value is None:
            return None
        amount = float(value)
        if amount <= 0:
            return None
        parts.append(amount)
    return round(sum(parts), 2)


def _quote_units(units: list, declared_cost: float, quote_fn):
    shares = _declared_shares(declared_cost, len(units))
    cache: dict = {}
    parts = []
    for unit, share in zip(units, shares):
        key = (
            int(unit["weight_g"]),
            int(unit["length_cm"]),
            int(unit["width_cm"]),
            int(unit["height_cm"]),
            share,
        )
        if key not in cache:
            cache[key] = quote_fn(unit, share)
        parts.append(cache[key])
    return _sum_costs(parts)


def quote_cdek_shipments(city_code: int, packages: Optional[list], weight_g: int) -> Optional[float]:
    units = shipment_units(packages, weight_g)

    def _one(unit, _share):
        try:
            return cdek.calculate_cost_by_city_code(
                int(city_code), weight_g=unit["weight_g"], packages=[unit],
            )
        except Exception:
            return None

    return _quote_units(units, 500.0, _one)


def quote_yandex_shipments(pvz_code: str, packages: Optional[list], weight_g: int, declared_cost: float) -> Optional[float]:
    units = shipment_units(packages, weight_g)

    def _one(unit, share):
        try:
            return yandex_ndd.calculate_price(
                pvz_code, total_weight_g=unit["weight_g"], declared_cost=share,
            )
        except Exception:
            return None

    return _quote_units(units, declared_cost, _one)


def quote_ozon_shipments(
    point: dict,
    phone: str,
    lat: float,
    lon: float,
    packages: Optional[list],
    weight_g: int,
    declared_cost: float,
) -> Optional[float]:
    units = shipment_units(packages, weight_g)

    def _one(unit, share):
        try:
            row = ozon_delivery.quote_to_point(
                point,
                phone,
                float(lat),
                float(lon),
                weight_g=unit["weight_g"],
                declared_cost=share,
                length_mm=int(unit["length_cm"]) * 10,
                width_mm=int(unit["width_cm"]) * 10,
                height_mm=int(unit["height_cm"]) * 10,
            )
            return (row or {}).get("cost")
        except Exception:
            return None

    return _quote_units(units, declared_cost, _one)


def token_ok(header_value: str) -> bool:
    expected = (SHOP_TOKEN or os.getenv("SHOP_DELIVERY_TOKEN", "")).strip()
    got = (header_value or "").strip()
    if not expected or not got:
        return False
    return got == expected


def maps_js_key() -> str:
    return (
        os.getenv("YANDEX_MAPS_JS_KEY")
        or os.getenv("VITE_YANDEX_MAPS_KEY")
        or os.getenv("YANDEX_API_KEY")
        or ""
    ).strip()


def _cache_get(store: dict, key: tuple) -> Optional[dict]:
    hit = store.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.time() - ts > _CACHE_TTL:
        store.pop(key, None)
        return None
    return val


def _cache_put(store: dict, key: tuple, val: dict, max_size: int = 80) -> dict:
    store[key] = (time.time(), val)
    if len(store) > max_size:
        oldest = sorted(store.items(), key=lambda kv: kv[1][0])[:20]
        for old_key, _ in oldest:
            store.pop(old_key, None)
    return val


def _normalize(city: str, address: str, lat: Optional[float], lon: Optional[float]) -> dict:
    city = (city or "").strip()
    address = (address or "").strip()
    blob = address or city
    if is_foreign_address_hint(city, address):
        return {"status": "outside_russia", "message": RUSSIA_ONLY_MESSAGE}
    normalized = resolve_address(blob) if blob else {}
    if normalized:
        city = normalized.get("city") or city
        address = normalized.get("address") or address or city
        if lat is None:
            lat = normalized.get("lat")
        if lon is None:
            lon = normalized.get("lon")
        if normalized.get("address_imprecise") or address_missing_street(normalized):
            return {
                "status": "need_street",
                "message": STREET_REQUIRED_MESSAGE,
                "client": {
                    "city": city,
                    "address": address,
                    "lat": lat,
                    "lon": lon,
                },
            }
    elif blob and len(blob) >= 3:
        return {
            "status": "need_street",
            "message": STREET_REQUIRED_MESSAGE,
        }
    return {
        "status": "ok",
        "client": {
            "city": city,
            "address": address or city,
            "lat": lat,
            "lon": lon,
        },
    }


def quote_both(
    city: str,
    address: str = "",
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    weight_g: int = DEFAULT_WEIGHT_G,
    declared_cost: float = 500.0,
    packages: Optional[list] = None,
) -> dict:
    packages = parse_packages(packages)
    city = (city or "").strip()
    address = (address or "").strip()
    weight_g = int(weight_g or DEFAULT_WEIGHT_G)
    if packages:
        weight_g = packages_weight_g(packages, weight_g)
    if weight_g < 1:
        weight_g = DEFAULT_WEIGHT_G

    normalized = resolve_address(address or city) or {}
    if normalized:
        city = normalized.get("city") or city
        address = normalized.get("address") or address or city
        if lat is None:
            lat = normalized.get("lat")
        if lon is None:
            lon = normalized.get("lon")

    key = (
        city.lower(),
        address.lower(),
        round(float(lat or 0), 4),
        round(float(lon or 0), 4),
        weight_g,
        int(declared_cost or 0),
        packages_cache_key(packages),
    )
    cached = _cache_get(_cache, key)
    if cached is not None:
        return cached

    both = get_both_candidates(
        city=city,
        address=address or city,
        lat=lat,
        lon=lon,
        weight_g=weight_g,
        declared_cost=declared_cost or 500.0,
    )
    return _cache_put(_cache, key, both)


def public_row(carrier: str, item: Optional[dict]) -> Optional[dict]:
    if not item or not item.get("cost"):
        return None
    return {
        "carrier": carrier,
        "cost": float(item["cost"]),
        "pvz_code": item.get("pvz_code") or "",
        "pvz_address": item.get("pvz_address") or "",
        "pvz_name": item.get("pvz_name") or "",
        "lat": item.get("lat"),
        "lng": item.get("lng"),
        "distance_km": item.get("distance_km"),
        "delivery_time": "2–5 дней",
    }


def is_postamat(item: Optional[dict]) -> bool:
    """Постаматы не берём: туда не влезает мебель."""
    if not item:
        return False
    kind = str(
        item.get("point_type")
        or item.get("type")
        or item.get("office_type")
        or ""
    ).lower()
    if kind in {"postamat", "postomat", "pochtomat", "locker", "lockbox", "parcel_locker", "ozon_box"}:
        return True
    if "postamat" in kind or "locker" in kind:
        return True
    blob = " ".join([
        str(item.get("pvz_name") or item.get("name") or ""),
        str(item.get("pvz_address") or item.get("address") or ""),
    ])
    return bool(_POSTAMAT_RE.search(blob))


def without_postamats(points: Optional[list], limit: int = 20) -> list:
    out = [p for p in (points or []) if not is_postamat(p)]
    return out[: max(1, int(limit))] if out else []


def public_point(carrier: str, item: dict) -> Optional[dict]:
    if not item or not item.get("pvz_code"):
        return None
    return {
        "carrier": carrier,
        "code": item.get("pvz_code") or "",
        "name": item.get("pvz_name") or "",
        "address": item.get("pvz_address") or "",
        "lat": item.get("lat"),
        "lng": item.get("lng"),
        "distance_km": item.get("distance_km"),
        "work_time": item.get("work_time") or "",
        "cost": float(item["cost"]) if item.get("cost") else None,
    }


def _attach_cdek_costs(points: list, weight_g: int, packages: Optional[list] = None) -> list:
    cache: dict = {}
    for p in points:
        city_code = p.get("city_code")
        if city_code:
            if city_code not in cache:
                cache[city_code] = quote_cdek_shipments(int(city_code), packages, weight_g)
            p["cost"] = cache.get(city_code)
        elif not p.get("cost"):
            p["cost"] = None
    return points


def _attach_yandex_costs(
    points: list,
    weight_g: int,
    declared_cost: float,
    packages: Optional[list] = None,
) -> list:
    if not points:
        return points
    base = quote_yandex_shipments(points[0]["pvz_code"], packages, weight_g, declared_cost)
    for p in points:
        p["cost"] = base
    return points


def _attach_ozon_costs(
    points: list,
    phone: str,
    lat: Optional[float],
    lon: Optional[float],
    weight_g: int,
    declared_cost: float,
    packages: Optional[list] = None,
) -> list:
    if not points or not phone or lat is None or lon is None:
        return points
    first = points[0]
    try:
        point_id = int(first.get("id") or first.get("pvz_code"))
    except (TypeError, ValueError):
        return points
    cost = quote_ozon_shipments(
        {
            "id": point_id,
            "shipment_method_ids": first.get("shipment_method_ids") or [],
        },
        phone,
        float(lat),
        float(lon),
        packages,
        weight_g,
        declared_cost,
    )
    for p in points:
        p["cost"] = cost
    return points


def list_points(
    carrier: str,
    city: str,
    address: str = "",
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    weight_g: int = DEFAULT_WEIGHT_G,
    declared_cost: float = 500.0,
    limit: int = 20,
    phone: str = "",
    packages: Optional[list] = None,
) -> dict:
    packages = parse_packages(packages)
    weight_g = int(weight_g or DEFAULT_WEIGHT_G)
    if packages:
        weight_g = packages_weight_g(packages, weight_g)
    if weight_g < 1:
        weight_g = DEFAULT_WEIGHT_G
    geo = _normalize(city, address, lat, lon)
    if geo.get("status") != "ok":
        return geo
    client = geo["client"]
    city = client["city"]
    address = client["address"]
    lat = client["lat"]
    lon = client["lon"]
    key = (
        carrier,
        (city or "").lower(),
        (address or "").lower(),
        round(float(lat or 0), 4),
        round(float(lon or 0), 4),
        weight_g,
        int(limit),
        (phone or "").strip() if carrier == "ozon" else "",
        packages_cache_key(packages),
    )
    cached = _cache_get(_points_cache, key)
    if cached is not None:
        return cached

    if carrier == "cdek":
        raw = list_cdek_points(city, lat, lon, address=address, limit=max(40, int(limit) * 2))
        raw = _attach_cdek_costs(raw, weight_g, packages)
    elif carrier == "ozon":
        if not ozon_delivery.is_enabled():
            return {"status": "unavailable", "message": "Ozon Доставка пока не подключена", "client": client}
        chk = ozon_delivery.check_client(phone)
        if not chk.get("eligible"):
            return {
                "status": "need_ozon_account",
                "message": "На этот номер нет профиля Ozon. Зарегистрируйтесь в приложении Ozon или выберите другую доставку.",
                "client": client,
            }
        raw = list_ozon_points(lat, lon, limit=max(40, int(limit) * 2), city=city)
        raw = _attach_ozon_costs(raw, chk.get("phone") or phone, lat, lon, weight_g, declared_cost or 500.0, packages)
    else:
        raw = list_yandex_points(city, address, lat, lon, limit=max(40, int(limit) * 2))
        raw = _attach_yandex_costs(raw, weight_g, declared_cost or 500.0, packages)
    raw = without_postamats(raw, limit)

    payload = {
        "status": "ok",
        "carrier": carrier,
        "client": client,
        "points": [p for p in (public_point(carrier, item) for item in raw) if p],
    }
    return _cache_put(_points_cache, key, payload)


def quote_point(
    carrier: str,
    pvz_code: str,
    city: str,
    address: str = "",
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    weight_g: int = DEFAULT_WEIGHT_G,
    declared_cost: float = 500.0,
    phone: str = "",
    packages: Optional[list] = None,
) -> Optional[dict]:
    packages = parse_packages(packages)
    if packages:
        weight_g = packages_weight_g(packages, weight_g)
    listed = list_points(
        carrier, city, address, lat, lon, weight_g, declared_cost, limit=20, phone=phone,
        packages=packages,
    )
    if listed.get("status") != "ok":
        return None
    match = next((p for p in listed.get("points") or [] if p.get("code") == pvz_code), None)
    if not match:
        return None
    cost = match.get("cost")
    if carrier == "yandex" and pvz_code:
        cost = quote_yandex_shipments(pvz_code, packages, weight_g, declared_cost or 500.0) or cost
    if carrier == "ozon" and pvz_code and phone:
        cost = quote_ozon_shipments(
            {"id": int(pvz_code), "shipment_method_ids": []},
            ozon_delivery.normalize_phone(phone) or phone,
            float(match.get("lat") if match.get("lat") is not None else lat),
            float(match.get("lng") if match.get("lng") is not None else lon),
            packages,
            weight_g,
            declared_cost or 500.0,
        ) or cost
    if not cost:
        return None
    return {
        "carrier": carrier,
        "cost": float(cost),
        "pvz_code": match["code"],
        "pvz_address": match.get("address") or "",
        "pvz_name": match.get("name") or "",
        "lat": match.get("lat"),
        "lng": match.get("lng"),
        "distance_km": match.get("distance_km"),
        "delivery_time": "2–5 дней",
    }


def suggest(query: str, count: int = 7) -> list:
    return suggest_addresses(query, count=count)
