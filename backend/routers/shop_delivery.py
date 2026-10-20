"""Публичный расчёт доставки для витрины CS-Cart. Доступ по SHOP_DELIVERY_TOKEN."""
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from .. import shop_delivery

router = APIRouter(prefix="/api/v1/shop/delivery", tags=["Shop delivery"])


def _auth(x_shop_token: str = Header("", alias="X-Shop-Token")):
    if not shop_delivery.token_ok(x_shop_token):
        raise HTTPException(status_code=403, detail="Нет доступа к расчёту доставки")


@router.get("/maps-config")
def maps_config(_: None = Depends(_auth)):
    key = shop_delivery.maps_js_key()
    if not key:
        raise HTTPException(status_code=503, detail="Ключ Яндекс.Карт не настроен")
    return {"apikey": key}


@router.get("/suggest-address")
def suggest_address(
    q: str = Query("", min_length=2),
    count: int = Query(7),
    _: None = Depends(_auth),
):
    return {"suggestions": shop_delivery.suggest(q, count=count)}


@router.get("/points")
def points(
    carrier: str = Query(...),
    city: str = Query(..., min_length=2),
    address: str = Query(""),
    lat: Optional[float] = Query(None),
    lon: Optional[float] = Query(None),
    weight_g: int = Query(shop_delivery.DEFAULT_WEIGHT_G),
    declared_cost: float = Query(500),
    phone: str = Query(""),
    packages: str = Query(""),
    _: None = Depends(_auth),
):
    carrier = (carrier or "").strip().lower()
    if carrier not in {"cdek", "yandex", "ozon"}:
        raise HTTPException(status_code=400, detail="carrier: cdek, yandex или ozon")
    parsed = shop_delivery.parse_packages(packages)
    return shop_delivery.list_points(
        carrier=carrier,
        city=city,
        address=address,
        lat=lat,
        lon=lon,
        weight_g=weight_g,
        declared_cost=declared_cost,
        phone=phone,
        packages=parsed,
    )


@router.get("/quote")
def quote(
    carrier: str = Query(...),
    city: str = Query(..., min_length=2),
    address: str = Query(""),
    lat: Optional[float] = Query(None),
    lon: Optional[float] = Query(None),
    weight_g: int = Query(shop_delivery.DEFAULT_WEIGHT_G),
    declared_cost: float = Query(500),
    pvz_code: str = Query(""),
    phone: str = Query(""),
    packages: str = Query(""),
    _: None = Depends(_auth),
):
    carrier = (carrier or "").strip().lower()
    if carrier not in {"cdek", "yandex", "ozon"}:
        raise HTTPException(status_code=400, detail="carrier: cdek, yandex или ozon")
    parsed = shop_delivery.parse_packages(packages)
    if pvz_code:
        row = shop_delivery.quote_point(
            carrier=carrier,
            pvz_code=pvz_code,
            city=city,
            address=address,
            lat=lat,
            lon=lon,
            weight_g=weight_g,
            declared_cost=declared_cost,
            phone=phone,
            packages=parsed,
        )
        if not row:
            return {"status": "not_found", "carrier": carrier, "data": None}
        return {"status": "ok", "carrier": carrier, "data": row}
    both = shop_delivery.quote_both(
        city=city,
        address=address,
        lat=lat,
        lon=lon,
        weight_g=weight_g,
        declared_cost=declared_cost,
        packages=parsed,
    )
    row = shop_delivery.public_row(carrier, both.get(carrier))
    if not row:
        return {"status": "not_found", "carrier": carrier, "data": None}
    return {"status": "ok", "carrier": carrier, "data": row}
