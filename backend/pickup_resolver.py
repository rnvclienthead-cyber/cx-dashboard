"""Подбор ближайших ПВЗ СДЭК и Яндекса к адресу клиента + расчёт цены по обоим.

Перевозчик НЕ выбирается автоматически. Функция фиксирует адрес клиента и считает
цену до ближайшего ПВЗ каждой из двух ТК (СДЭК тариф 483, Яндекс NDD ПВЗ-ПВЗ, с НДС).
Дальше КС сам согласовывает на этапе одобрения заявки, какой вариант выгоднее.
"""
import logging
import os
import re
from typing import Optional

from . import cdek
from . import yandex_ndd

logger = logging.getLogger(__name__)


def distance_km(
    lat1: Optional[float], lon1: Optional[float],
    lat2: Optional[float], lon2: Optional[float],
) -> Optional[float]:
    """Расстояние по сфере (км), округление до 1 знака. None если нет координат."""
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None
    try:
        return round(cdek._haversine_km(float(lat1), float(lon1), float(lat2), float(lon2)), 1)
    except (TypeError, ValueError):
        return None


def _dadata_key() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    return (os.getenv("DADATA_API_KEY") or os.getenv("VITE_DADATA_KEY") or "").strip()


def _suggestion_to_resolved(suggestion: dict, fallback_q: str = "") -> Optional[dict]:
    """Превращает одну подсказку DaData в наш формат (нужны координаты и город)."""
    if not suggestion:
        return None
    data = suggestion.get("data") or {}
    lat = data.get("geo_lat")
    lon = data.get("geo_lon")
    if lat is None or lon is None:
        return None
    # Чистая область/край без города — для ПВЗ бесполезно
    city = (
        data.get("city") or data.get("settlement")
        or data.get("city_with_type") or data.get("settlement_with_type")
    )
    if not city:
        return None
    city = str(city).strip()
    granularity = data.get("granularity") or ""
    return {
        "address": suggestion.get("value") or fallback_q,
        "city": city,
        "lat": float(lat),
        "lon": float(lon),
        "address_imprecise": not data.get("street") and not data.get("house")
            and granularity != "flat",
    }


def suggest_addresses(query: str, count: int = 7) -> list:
    """Подсказки адресов через DaData (ключ только на сервере)."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    key = _dadata_key()
    if not key:
        return []
    import requests
    try:
        resp = requests.post(
            "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address",
            json={"query": q, "count": max(1, min(int(count), 10))},
            headers={
                "Authorization": f"Token {key}",
                "Content-Type": "application/json",
            },
            timeout=8,
        )
        if resp.status_code != 200:
            return []
        out = []
        for suggestion in (resp.json() or {}).get("suggestions") or []:
            resolved = _suggestion_to_resolved(suggestion, q)
            if resolved:
                out.append({
                    "value": resolved["address"],
                    "city": resolved["city"],
                    "lat": resolved["lat"],
                    "lon": resolved["lon"],
                    "address_imprecise": resolved["address_imprecise"],
                    "selectable": True,
                })
                continue
            value = (suggestion.get("value") or "").strip()
            if not value:
                continue
            data = suggestion.get("data") or {}
            out.append({
                "value": value,
                "city": data.get("city") or data.get("settlement") or "",
                "lat": None,
                "lon": None,
                "selectable": False,
            })
        return out
    except Exception as e:
        logger.warning("DaData suggest failed for %r: %s", q[:80], e)
        return []


def resolve_address(address: str) -> Optional[dict]:
    """Нормализует адрес на сервере: сначала DaData, затем Nominatim.

    Ключ DaData намеренно не передаётся в browser bundle: production-форма
    должна одинаково работать при чистой сборке из Git.
    """
    q = (address or "").strip()
    if len(q) < 3:
        return None

    import requests

    dadata_key = _dadata_key()
    if dadata_key:
        try:
            resp = requests.post(
                "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address",
                json={"query": q, "count": 5},
                headers={
                    "Authorization": f"Token {dadata_key}",
                    "Content-Type": "application/json",
                },
                timeout=8,
            )
            if resp.status_code == 200:
                suggestions = (resp.json() or {}).get("suggestions") or []
                # Первая подсказка с координатами и городом (не «Иркутская обл»)
                for suggestion in suggestions:
                    resolved = _suggestion_to_resolved(suggestion, q)
                    if resolved:
                        return resolved
        except Exception as e:
            logger.warning("geocode DaData failed for %r: %s", q[:80], e)

    # Fallback: Nominatim — сначала полный адрес, потом первая часть (город)
    for attempt in (q, q.split(",")[0].strip()):
        if len(attempt) < 3:
            continue
        try:
            resp = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": attempt, "format": "json", "limit": 1,
                    "countrycodes": "ru", "addressdetails": 1,
                },
                headers={"User-Agent": "cx-dashboard-reshipment/1.0 (vidovito)"},
                timeout=8,
            )
            if resp.status_code != 200:
                continue
            data = resp.json() or []
            if data:
                item = data[0]
                details = item.get("address") or {}
                city = (
                    details.get("city") or details.get("town") or details.get("village")
                    or details.get("municipality") or attempt
                )
                return {
                    "address": item.get("display_name") or q,
                    "city": city,
                    "lat": float(item["lat"]),
                    "lon": float(item["lon"]),
                    "address_imprecise": not any(
                        details.get(key) for key in ("road", "pedestrian", "house_number")
                    ),
                }
        except Exception as e:
            logger.warning("geocode Nominatim failed for %r: %s", attempt[:80], e)
    return None


def geocode_address(address: str) -> Optional[tuple]:
    """Возвращает (lat, lon) или None для старых вызовов."""
    resolved = resolve_address(address)
    if not resolved:
        return None
    return resolved["lat"], resolved["lon"]

# Административные суффиксы/префиксы, которые мешают CDEK найти город
_ADMIN = re.compile(
    r'\b(г|пгт|рп|пос|посёлок|поселок|деревня|дер|с|ст|обл|область|р-н|район|округ|ао|мо|г\.о)\b\.?\s*',
    re.IGNORECASE,
)
# Целая часть адреса — район/область/край (не НП). Иначе «Федоровский р-н»
# превращается в город «Федоровский» → ПВЗ-омоним за тысячи км.
_ADMIN_UNIT = re.compile(
    r'\b(р-н|район|обл\.?|область|край|округ|ао|республика|респ\.?)\b',
    re.IGNORECASE,
)
# Слова, указывающие на часть улицы/дома — такие части пропускаем
_STREET_KW = re.compile(
    r'\b(ул|улица|пер|переулок|пр|просп|бул|набереж|наб|пл|шоссе|ш|д|дом|стр|строение|корп|корпус|кв|квартира|офис|стр\.)\b',
    re.IGNORECASE,
)
# Не предлагаем ПВЗ СДЭК из другого региона (у Яндекса аналогично 35 км)
CDEK_MAX_KM = 40.0


def _extract_city(raw: str) -> str:
    """Из адреса «Город, улица, дом» берёт часть до первой запятой."""
    return raw.split(",")[0].strip() or raw


def _city_chain(city: str, address: str) -> list:
    """
    Последовательность вариантов названия города для перебора в CDEK:
    сначала переданный city, потом части полного адреса (без уличных частей).
    Позволяет найти CDEK-точки в ближайшем городе, когда клиент указал деревню/посёлок.
    """
    seen: set = set()
    result: list = []

    def _add(raw: str):
        name = _ADMIN.sub("", raw).strip(" .,")
        if len(name) >= 3 and name.lower() not in seen:
            seen.add(name.lower())
            result.append(name)

    _add(city)
    simple = _extract_city(city)
    if simple != city:
        _add(simple)

    if address:
        for part in address.split(","):
            part = part.strip()
            if _STREET_KW.search(part) or _ADMIN_UNIT.search(part):
                continue
            _add(part)

    return result


def get_cdek_candidate(
    city: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    address: str = "",
) -> Optional[dict]:
    """Ближайший ПВЗ СДЭК к адресу + цена (с НДС).
    При координатах — ищет по городам региона вокруг точки (не только city).
    Без координат — перебирает варианты названия города.
    """
    def _pack(pvz: dict, cost: float) -> dict:
        return {
            "pvz_code":    pvz["code"],
            "pvz_address": pvz.get("address", ""),
            "pvz_name":    pvz.get("name", ""),
            "lat":         pvz.get("lat"),
            "lng":         pvz.get("lng"),
            "cost":        cost,
            "distance_km": pvz.get("distance_km"),
        }

    def _within_max(pvz: dict) -> bool:
        if lat is None or lon is None or pvz.get("lat") is None or pvz.get("lng") is None:
            return True
        d = distance_km(lat, lon, pvz.get("lat"), pvz.get("lng"))
        if d is None:
            return True
        if d > CDEK_MAX_KM:
            logger.info(
                "CDEK: отброшен ПВЗ %s (%.1f км > %.0f) addr=%s",
                pvz.get("code"), d, CDEK_MAX_KM, (pvz.get("address") or "")[:80],
            )
            return False
        return True

    # 1) Координаты клиента — поиск по региону (деревня → соседние города)
    if lat is not None and lon is not None:
        try:
            pvz = cdek.find_nearest_pvz_around_coords(
                float(lat), float(lon), seed_city=city or _extract_city(address),
            )
            if pvz and pvz.get("code") and _within_max(pvz):
                city_code = pvz.get("city_code")
                if city_code:
                    cost = cdek.calculate_cost_by_city_code(int(city_code))
                else:
                    cost = cdek.calculate_cost_by_city(pvz.get("city_name") or city)
                if cost:
                    logger.info(
                        "CDEK: ПВЗ %s за %.1f км (city=%s)",
                        pvz.get("code"), pvz.get("distance_km") or 0, pvz.get("city_name"),
                    )
                    return _pack(pvz, cost)
        except Exception as e:
            logger.warning("CDEK around-coords failed: %s", e)

    def _try(city_name: str) -> Optional[dict]:
        try:
            if lat is not None and lon is not None:
                pvz = cdek.find_nearest_pvz_by_coords(city_name, lat, lon)
            else:
                pvzs = cdek.get_pvz_list(city_name)
                pvz = pvzs[0] if pvzs else None
            if not pvz or not pvz.get("code"):
                return None
            if not _within_max(pvz):
                return None
            city_code = pvz.get("city_code")
            if city_code:
                cost = cdek.calculate_cost_by_city_code(int(city_code))
            else:
                cost = cdek.calculate_cost_by_city(city_name)
            if not cost:
                return None
            if lat is not None and lon is not None and pvz.get("lat") is not None:
                pvz = {**pvz, "distance_km": distance_km(lat, lon, pvz.get("lat"), pvz.get("lng"))}
            return _pack(pvz, cost)
        except Exception as e:
            logger.warning("CDEK pvz/price lookup failed for city=%s: %s", city_name, e)
            return None

    for candidate in _city_chain(city, address):
        result = _try(candidate)
        if result:
            if candidate != city:
                logger.info("CDEK: подобрана точка через запасной вариант '%s' (исходный: '%s')", candidate, city)
            return result

    return None


def get_yandex_candidate(
    city: str, address: str, lat: Optional[float] = None, lon: Optional[float] = None,
    weight_g: int = 300, declared_cost: float = 500.0,
) -> Optional[dict]:
    """Ближайший ПВЗ Яндекса к адресу + цена (с НДС). None если не удалось подобрать.

    При неоднозначных деревнях («Каменка») сначала резолвим geo по городу (Дмитров),
    а ближайшую точку выбираем по lat/lon клиента. Если далеко — пробуем соседние НП.
    Дальше max_km — не предлагаем «Яндекс» с ПВЗ в другом регионе (омонимы geo).
    """
    MAX_KM = 35.0
    try:
        if not yandex_ndd.is_enabled():
            return None

        locations = []
        if city:
            locations.append(city.strip())
        if city and address and city.strip().lower() not in address.lower():
            locations.append(f"{city.strip()}, {address.strip()}")
        if address:
            locations.append(address.strip())
        seen: set = set()
        locations = [x for x in locations if x and not (x.lower() in seen or seen.add(x.lower()))]

        best = None
        best_d = 1e9

        def _consider(loc: str):
            nonlocal best, best_d
            p = yandex_ndd.nearest_pickup_point_across_geos(loc, lat, lon, delta=0.2)
            if not p or not p.get("id"):
                return
            if lat is not None and lon is not None and p.get("lat") is not None:
                d = distance_km(lat, lon, p.get("lat"), p.get("lng"))
                d = d if d is not None else 1e9
            else:
                d = 0.0
            if d > MAX_KM:
                logger.info(
                    "Yandex: отброшен ПВЗ %s (%.1f км > %.0f) для loc=%r",
                    p.get("address") or p.get("id"), d, MAX_KM, loc,
                )
                return
            if d < best_d:
                best_d = d
                best = p

        for loc in locations:
            _consider(loc)

        if lat is not None and lon is not None and (best is None or best_d > 12):
            try:
                nearby = cdek.nearby_city_names(float(lat), float(lon), seed_city=city or "", limit=8)
                for loc in nearby:
                    if loc.lower() in seen:
                        continue
                    seen.add(loc.lower())
                    _consider(loc)
                    if best_d <= 8:
                        break
            except Exception as e:
                logger.debug("Yandex nearby cities skip: %s", e)

        if not best or not best.get("id"):
            return None
        cost = yandex_ndd.calculate_price(best["id"], total_weight_g=weight_g, declared_cost=declared_cost)
        if not cost:
            return None
        return {
            "pvz_code":    best["id"],
            "pvz_address": best.get("address", ""),
            "pvz_name":    best.get("name", ""),
            "lat":         best.get("lat"),
            "lng":         best.get("lng"),
            "cost":        cost,
            "distance_km": round(best_d, 1) if best_d < 1e8 else None,
        }
    except Exception as e:
        logger.warning("Yandex NDD pvz/price lookup failed for address=%s: %s", address, e)
        return None


def get_both_candidates(
    city: str, address: str, lat: Optional[float] = None, lon: Optional[float] = None,
    weight_g: int = 300, declared_cost: float = 500.0,
) -> dict:
    """
    Возвращает {"cdek": {...} | None, "yandex": {...} | None} — оба варианта,
    без выбора победителя. Каждый: {pvz_code, pvz_address, pvz_name, lat, lng, cost}.
    """
    return {
        "cdek":   get_cdek_candidate(city, lat, lon, address=address),
        "yandex": get_yandex_candidate(city, address, lat, lon, weight_g, declared_cost),
    }
