"""Подбор самого дешёвого варианта доставки (СДЭК или Яндекс) до ближайшего к клиенту ПВЗ.

Клиент не выбирает перевозчика — только указывает адрес. Дальше система сама:
1. Находит ближайший ПВЗ СДЭК и ближайший ПВЗ Яндекса к этому адресу
2. Считает цену до каждого (тариф 483 у СДЭК, склад-склад через ПВЗ у Яндекса)
3. Возвращает более дешёвый вариант — карьер клиенту не показывается
"""
import logging
from typing import Optional

from . import cdek
from . import yandex_ndd

logger = logging.getLogger(__name__)


def resolve_best_pickup_point(
    city: str,
    address: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    weight_g: int = 300,
    declared_cost: float = 500.0,
) -> Optional[dict]:
    """
    Возвращает {
      "delivery_method": "cdek" | "yandex",
      "pvz_code":    str,   # cdek: код ПВЗ; yandex: platform_station_id
      "pvz_address": str,
      "pvz_name":    str,
      "cost":        float | None,
    }
    None — если ни один из перевозчиков не смог подобрать точку/цену (нужна ручная обработка).
    """
    candidates = []

    # ── СДЭК ──────────────────────────────────────────────────────────────
    try:
        if lat is not None and lon is not None:
            cdek_pvz = cdek.find_nearest_pvz_by_coords(city, lat, lon)
        else:
            pvzs = cdek.get_pvz_list(city)
            cdek_pvz = pvzs[0] if pvzs else None
        if cdek_pvz and cdek_pvz.get("code"):
            # calculate_cost_by_city() отдаёт total_sum (с НДС) — так же, как везде в остальной системе
            # (calculate_cost() по коду ПВЗ приоритизирует delivery_sum без НДС — не использовать для сравнения цен)
            cost = cdek.calculate_cost_by_city(city)
            if cost:
                candidates.append({
                    "delivery_method": "cdek",
                    "pvz_code":    cdek_pvz["code"],
                    "pvz_address": cdek_pvz.get("address", ""),
                    "pvz_name":    cdek_pvz.get("name", ""),
                    "lat":         cdek_pvz.get("lat"),
                    "lng":         cdek_pvz.get("lng"),
                    "cost":        cost,
                })
    except Exception as e:
        logger.warning("CDEK pvz/price lookup failed for city=%s: %s", city, e)

    # ── Яндекс (NDD, ПВЗ-ПВЗ) ────────────────────────────────────────────
    try:
        if yandex_ndd.is_enabled():
            geo = yandex_ndd.detect_geo_id(address or city)
            if geo:
                if lat is not None and lon is not None:
                    yd_pvz = yandex_ndd.nearest_pickup_point(geo["geo_id"], lat, lon)
                else:
                    pts = yandex_ndd.list_pickup_points(geo["geo_id"])
                    yd_pvz = pts[0] if pts else None
                if yd_pvz and yd_pvz.get("id"):
                    cost = yandex_ndd.calculate_price(
                        yd_pvz["id"], total_weight_g=weight_g, declared_cost=declared_cost,
                    )
                    if cost:
                        candidates.append({
                            "delivery_method": "yandex",
                            "pvz_code":    yd_pvz["id"],
                            "pvz_address": yd_pvz.get("address", ""),
                            "pvz_name":    yd_pvz.get("name", ""),
                            "lat":         yd_pvz.get("lat"),
                            "lng":         yd_pvz.get("lng"),
                            "cost":        cost,
                        })
    except Exception as e:
        logger.warning("Yandex NDD pvz/price lookup failed for address=%s: %s", address, e)

    if not candidates:
        return None
    winner = min(candidates, key=lambda c: c["cost"])
    # Сводка сравнения (для показа КС — почему выбран этот перевозчик)
    winner["all_candidates"] = [
        {"delivery_method": c["delivery_method"], "cost": c["cost"]} for c in candidates
    ]
    return winner
