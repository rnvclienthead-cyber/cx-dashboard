"""Подбор ближайших ПВЗ СДЭК, Яндекса и Ozon к адресу клиента + расчёт цены.

Перевозчик НЕ выбирается автоматически. КС сравнивает варианты при одобрении.
Ozon — только если у клиента есть аккаунт Ozon на телефон и в кабинете включена сдача в ПВЗ.
"""
import logging
import os
import re
from typing import Optional

from . import cdek
from . import yandex_ndd
from . import ozon_delivery

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


# Отправка деталей — только РФ. Сообщение для формы клиента и API.
RUSSIA_ONLY_MESSAGE = (
    "Сейчас отправляем детали только по России. "
    "В другие страны доставка временно недоступна."
)
STREET_REQUIRED_MESSAGE = (
    "Укажите улицу и номер дома — так пункт выдачи будет рядом с вами."
)

# Явные маркеры зарубежья (Казахстан и др.) — ловим ещё до геокодера
_FOREIGN_ADDRESS_HINT = re.compile(
    r"(?iu)\b("
    r"казахстан|қазақстан|караганда|қарағанды|алматы|астана|нур[-\s]?султан|шымкент|"
    r"беларусь|белорусси[яи]|минск|гомель|брест|"
    r"киргиз|кыргыз|бишкек|узбекистан|ташкент|"
    r"армения|ереван|азербайджан|баку|"
    r"украин[аые]|киев|київ|харьков|одесс|"
    r"молдов|приднестр|грузия|тбилиси|"
    r"эстония|латвия|литва|польша|германия|израил"
    r")\b"
)

_DADATA_RU_LOCATIONS = {"locations": [{"country_iso_code": "RU"}], "restrict_value": True}


def address_missing_street(resolved: Optional[dict] = None, *, address: str = "", city: str = "") -> bool:
    """True, если выбран только город/населённый пункт — без улицы и дома."""
    if resolved is not None:
        return bool(resolved.get("address_imprecise"))
    blob = f"{address or ''} {city or ''}".strip()
    if not blob:
        return True
    normalized = resolve_address(blob)
    if normalized is not None:
        return bool(normalized.get("address_imprecise"))
    return not bool(re.search(
        r"(?iu)\b(ул|улица|пер|переулок|пр|просп|проспект|бул|бульвар|ш|шоссе|наб|набережная)\b"
        r"|\d",
        blob,
    ))


def is_foreign_address_hint(*parts: Optional[str]) -> bool:
    """True, если в тексте явно указана другая страна / крупный зарубежный город."""
    blob = " ".join(p for p in parts if p).strip()
    if not blob:
        return False
    return bool(_FOREIGN_ADDRESS_HINT.search(blob.replace("ё", "е")))


def _dadata_is_russia(data: dict) -> bool:
    """DaData: пустой country_iso_code считаем РФ (по умолчанию подсказки РФ)."""
    iso = str((data or {}).get("country_iso_code") or "").strip().upper()
    if not iso:
        return True
    return iso == "RU"


def _suggestion_to_resolved(suggestion: dict, fallback_q: str = "") -> Optional[dict]:
    """Превращает одну подсказку DaData в наш формат (нужны координаты и город)."""
    if not suggestion:
        return None
    data = suggestion.get("data") or {}
    if not _dadata_is_russia(data):
        return None
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
        "country_iso": "RU",
        "address_imprecise": not data.get("street") and not data.get("house")
            and granularity != "flat",
    }


def suggest_addresses(query: str, count: int = 7) -> list:
    """Подсказки адресов через DaData (ключ только на сервере). Только РФ."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    if is_foreign_address_hint(q):
        return []
    key = _dadata_key()
    if not key:
        return []
    import requests
    try:
        payload = {
            "query": q,
            "count": max(1, min(int(count), 10)),
            **_DADATA_RU_LOCATIONS,
        }
        resp = requests.post(
            "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address",
            json=payload,
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
            data = suggestion.get("data") or {}
            if not _dadata_is_russia(data):
                continue
            resolved = _suggestion_to_resolved(suggestion, q)
            if resolved:
                out.append({
                    "value": resolved["address"],
                    "city": resolved["city"],
                    "lat": resolved["lat"],
                    "lon": resolved["lon"],
                    "zip": str(data.get("postal_code") or ""),
                    "address_imprecise": resolved["address_imprecise"],
                    "selectable": not resolved["address_imprecise"],
                })
                continue
            value = (suggestion.get("value") or "").strip()
            if not value:
                continue
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
    """Нормализует адрес на сервере: сначала DaData, затем Nominatim. Только РФ.

    Ключ DaData намеренно не передаётся в browser bundle: production-форма
    должна одинаково работать при чистой сборке из Git.
    """
    q = (address or "").strip()
    if len(q) < 3:
        return None
    if is_foreign_address_hint(q):
        return None

    import requests

    dadata_key = _dadata_key()
    if dadata_key:
        try:
            resp = requests.post(
                "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address",
                json={"query": q, "count": 5, **_DADATA_RU_LOCATIONS},
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

    # Fallback: Nominatim только по РФ
    attempts = [q]
    if "," in q:
        attempts.append(q.split(",")[0].strip())
    first_word = q.replace(",", " ").split()[0] if q.replace(",", " ").split() else ""
    if first_word and len(first_word) >= 3 and first_word.lower() not in {a.lower() for a in attempts}:
        attempts.append(first_word)

    for attempt in attempts:
        if len(attempt) < 3:
            continue
        try:
            resp = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": attempt, "format": "json", "limit": 3,
                    "countrycodes": "ru", "addressdetails": 1,
                },
                headers={"User-Agent": "cx-dashboard-reshipment/1.0 (vidovito)"},
                timeout=8,
            )
            if resp.status_code != 200:
                continue
            data = resp.json() or []
            if not data:
                continue
            item = data[0]
            details = item.get("address") or {}
            cc = str(details.get("country_code") or "").lower()
            if cc and cc != "ru":
                continue
            city = (
                details.get("city") or details.get("town") or details.get("village")
                or details.get("municipality") or attempt
            )
            return {
                "address": item.get("display_name") or q,
                "city": city,
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "country_iso": "RU",
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
    weight_g: Optional[int] = None,
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
                    cost = cdek.calculate_cost_by_city_code(int(city_code), weight_g=weight_g)
                else:
                    cost = cdek.calculate_cost_by_city(pvz.get("city_name") or city, weight_g=weight_g)
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
                cost = cdek.calculate_cost_by_city_code(int(city_code), weight_g=weight_g)
            else:
                cost = cdek.calculate_cost_by_city(city_name, weight_g=weight_g)
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
                # Без координат клиента нельзя отличить омоним
                # («Молодёжный» в Иркутске для «Караганда Поселок Молодежный»).
                logger.info(
                    "Yandex: пропуск ПВЗ без координат клиента loc=%r pvz=%s",
                    loc, p.get("address") or p.get("id"),
                )
                return
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


def get_ozon_candidate(
    lat: Optional[float],
    lon: Optional[float],
    phone: str = "",
    city: str = "",
) -> Optional[dict]:
    """Ближайший ПВЗ Ozon в городе клиента + цена."""
    try:
        if not ozon_delivery.is_enabled():
            return None
        row = ozon_delivery.get_candidate(lat, lon, phone, city=city)
        if not row:
            return None
        if not row.get("available"):
            return {
                "available": False,
                "client_ok": bool(row.get("client_ok")),
                "reason": row.get("reason") or "",
            }
        return {
            "available": True,
            "client_ok": True,
            "pvz_code": row["pvz_code"],
            "pvz_address": row.get("pvz_address") or "",
            "pvz_name": row.get("pvz_name") or "",
            "lat": row.get("lat"),
            "lng": row.get("lng"),
            "cost": row.get("cost"),
            "distance_km": row.get("distance_km"),
            "from_pvz_id": row.get("from_pvz_id"),
            "from_pvz_address": row.get("from_pvz_address"),
            "shipment_method_id": row.get("shipment_method_id"),
            "cutoff_at": row.get("cutoff_at"),
        }
    except Exception as e:
        logger.warning("Ozon pvz/price lookup failed: %s", e)
        return None


def get_both_candidates(
    city: str, address: str, lat: Optional[float] = None, lon: Optional[float] = None,
    weight_g: Optional[int] = None, declared_cost: float = 500.0,
    phone: str = "",
) -> dict:
    """
    Возвращает cdek / yandex / ozon — без выбора победителя.
    Ozon требует телефон с аккаунтом Ozon; если нельзя — ozon.available=False + reason.
    weight_g=None — как в доотправках (СДЭК из .env, Яндекс 300 г).
    """
    yandex_w = 300 if weight_g is None else weight_g
    return {
        "cdek":   get_cdek_candidate(city, lat, lon, address=address, weight_g=weight_g),
        "yandex": get_yandex_candidate(city, address, lat, lon, yandex_w, declared_cost),
        "ozon":   get_ozon_candidate(lat, lon, phone, city=city),
    }


def list_cdek_points(
    city: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    address: str = "",
    limit: int = 20,
) -> list:
    """ПВЗ СДЭК от ближайшего к адресу. Для витрины: список + карта."""
    points: list = []
    seen: set = set()

    def _add(raw_list):
        for p in raw_list or []:
            code = p.get("code") or p.get("pvz_code")
            plat, plng = p.get("lat"), p.get("lng")
            if not code or code in seen or plat is None or plng is None:
                continue
            d = distance_km(lat, lon, plat, plng) if lat is not None and lon is not None else None
            if d is not None and d > CDEK_MAX_KM:
                continue
            seen.add(code)
            points.append({
                "pvz_code": code,
                "pvz_name": p.get("name") or p.get("pvz_name") or "",
                "pvz_address": p.get("address") or p.get("pvz_address") or "",
                "lat": plat,
                "lng": plng,
                "work_time": p.get("work_time") or "",
                "city_code": p.get("city_code"),
                "distance_km": d,
            })

    if city:
        try:
            _add(cdek.get_pvz_list(city, near_lat=lat, near_lon=lon))
        except Exception as e:
            logger.warning("CDEK list by city failed city=%s: %s", city, e)
    if lat is not None and lon is not None and len(points) < 8:
        try:
            nearest = cdek.find_nearest_pvz_around_coords(
                float(lat), float(lon), seed_city=city or _extract_city(address),
            )
            extra_city = (nearest or {}).get("city_name") or ""
            if extra_city and extra_city.lower() != (city or "").lower():
                _add(cdek.get_pvz_list(extra_city, near_lat=lat, near_lon=lon))
            if nearest:
                _add([nearest])
        except Exception as e:
            logger.warning("CDEK list around coords failed: %s", e)

    points.sort(key=lambda x: (x["distance_km"] is None, x["distance_km"] or 0))
    return points[: max(1, int(limit))]


def list_ozon_points(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    limit: int = 20,
    city: str = "",
) -> list:
    """ПВЗ Ozon от ближайшего в городе клиента. Для витрины: список + карта."""
    if not ozon_delivery.is_enabled() or lat is None or lon is None:
        return []
    try:
        return ozon_delivery.list_nearby_points(float(lat), float(lon), limit=limit, city=city)
    except Exception as e:
        logger.warning("Ozon list nearby failed: %s", e)
        return []


def list_yandex_points(
    city: str,
    address: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    limit: int = 20,
) -> list:
    """ПВЗ Яндекса от ближайшего к адресу. Для витрины: список + карта."""
    if not yandex_ndd.is_enabled():
        return []
    loc = address or city
    if city and address and city.strip().lower() not in address.lower():
        loc = f"{city.strip()}, {address.strip()}"
    try:
        raw = yandex_ndd.list_nearby_pickup_points(
            loc, lat, lon, limit=limit,
        )
    except Exception as e:
        logger.warning("Yandex list nearby failed loc=%r: %s", loc, e)
        return []
    out = []
    for p in raw:
        out.append({
            "pvz_code": p.get("id") or "",
            "pvz_name": p.get("name") or "",
            "pvz_address": p.get("address") or "",
            "lat": p.get("lat"),
            "lng": p.get("lng"),
            "work_time": p.get("work_time") or "",
            "distance_km": p.get("distance_km"),
        })
    return out
