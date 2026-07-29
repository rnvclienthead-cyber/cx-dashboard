"""СДЭК API v2 — интеграция для доотправки деталей."""
import os
import time
import math
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
TARIFF_CODE     = int(os.getenv("CDEK_TARIFF_CODE", "136"))  # 136 = Посылка склад→склад (самый дешёвый доступный)
SENDER_NAME     = os.getenv("CDEK_SENDER_NAME", "Видовито")
SENDER_PHONE    = os.getenv("CDEK_SENDER_PHONE", "")          # телефон отправителя (заполнить в .env)
PACKAGE_WEIGHT  = int(os.getenv("CDEK_PACKAGE_WEIGHT_G", "100"))   # 100г
PACKAGE_LENGTH  = int(os.getenv("CDEK_PACKAGE_LENGTH_CM", "10"))   # 10 см
PACKAGE_WIDTH   = int(os.getenv("CDEK_PACKAGE_WIDTH_CM", "3"))     # 3 см
PACKAGE_HEIGHT  = int(os.getenv("CDEK_PACKAGE_HEIGHT_CM", "3"))    # 3 см
ITEM_COST       = int(os.getenv("CDEK_ITEM_COST_RUB", "1"))        # объявленная стоимость 1 руб
VAT_RATE        = float(os.getenv("CDEK_VAT_RATE", "22"))           # НДС % для оценки итоговой суммы

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


def _get_fresh_h() -> dict:
    """Принудительно обновить токен (при 403)."""
    global _token, _token_expires
    _token = None
    _token_expires = 0
    return _h()


def _api_get(url: str, params: dict, timeout: int = 15) -> _req.Response:
    """GET с автоматическим retry при 403 (истёкший/инвалидированный токен)."""
    r = _req.get(url, params=params, headers=_h(), timeout=timeout)
    if r.status_code == 403:
        r = _req.get(url, params=params, headers=_get_fresh_h(), timeout=timeout)
    r.raise_for_status()
    return r


def _api_post(url: str, json: dict, timeout: int = 15) -> _req.Response:
    """POST с автоматическим retry при 403."""
    r = _req.post(url, json=json, headers=_h(), timeout=timeout)
    if r.status_code == 403:
        r = _req.post(url, json=json, headers=_get_fresh_h(), timeout=timeout)
    r.raise_for_status()
    return r


# ── Список ПВЗ ────────────────────────────────────────────────────────────────

def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Расстояние в км. Обычный hypot по градусам некорректен на широте
    средней полосы РФ — градус долготы почти вдвое короче градуса широты."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _resolve_city_code(
    city_name: str,
    near_lat: Optional[float] = None,
    near_lon: Optional[float] = None,
    size: int = 10,
) -> Optional[int]:
    """Код города СДЭК. При координатах — ближайший омоним, иначе первый из ответа."""
    cities = _api_get(
        f"{CDEK_BASE}/location/cities",
        params={"city": city_name, "country_codes": "RU", "size": size},
        timeout=10,
    ).json() or []
    if not cities:
        return None
    if near_lat is not None and near_lon is not None:
        def _dist(c):
            clat, clon = c.get("latitude"), c.get("longitude")
            if clat is None or clon is None:
                return 1e9
            return _haversine_km(near_lat, near_lon, clat, clon)
        cities = sorted(cities, key=_dist)
    code = cities[0].get("code")
    return int(code) if code is not None else None


def get_pvz_list(
    city_name: str,
    near_lat: Optional[float] = None,
    near_lon: Optional[float] = None,
) -> list[dict]:
    """Возвращает список ПВЗ СДЭК в заданном городе.
    near_lat/near_lon — выбрать ближайший омоним города (Федоровский ≠ ХМАО)."""
    city_code = _resolve_city_code(city_name, near_lat, near_lon)
    if city_code is None:
        return []
    return get_pvz_list_by_city_code(int(city_code))


def get_pvz_list_by_city_code(city_code: int) -> list[dict]:
    """ПВЗ СДЭК по коду города."""
    points = _api_get(
        f"{CDEK_BASE}/deliverypoints",
        params={"city_code": city_code, "type": "PVZ", "is_handout": True},
        timeout=15,
    ).json() or []

    result = []
    for p in points:
        loc = p.get("location", {})
        result.append({
            "code":      p.get("code", ""),
            "name":      p.get("name", ""),
            "address":   loc.get("address_full", loc.get("address", "")),
            "work_time": p.get("work_time", ""),
            "phone":     p.get("phones", [{}])[0].get("number", "") if p.get("phones") else "",
            "lat":       loc.get("latitude"),
            "lng":       loc.get("longitude"),
            "city_code": city_code,
        })
    return result


# ── Расчёт стоимости ──────────────────────────────────────────────────────────

def find_nearest_pvz(city_name: str) -> Optional[str]:
    """Найти ближайший/первый ПВЗ в городе. Используется при автосоздании заказа СДЭК."""
    pvzs = get_pvz_list(city_name)
    return pvzs[0]["code"] if pvzs else None


def find_nearest_pvz_by_coords(city_name: str, target_lat: float, target_lon: float) -> Optional[dict]:
    """Ближайший к заданным координатам ПВЗ СДЭК в городе (для авто-подбора точки клиенту).
    Город-омоним выбирается ближайший к координатам клиента."""
    pvzs = [
        p for p in get_pvz_list(city_name, near_lat=target_lat, near_lon=target_lon)
        if p.get("lat") and p.get("lng")
    ]
    if not pvzs:
        return None
    return min(pvzs, key=lambda p: _haversine_km(target_lat, target_lon, p["lat"], p["lng"]))


def find_nearest_pvz_around_coords(
    target_lat: float,
    target_lon: float,
    seed_city: str = "",
    city_radius_km: float = 40.0,
    max_cities_with_pvz: int = 20,
) -> Optional[dict]:
    """Ближайший ПВЗ в радиусе: города региона вокруг координат + seed_city.

    Решает случай «деревня рядом с городом»: в самом городе ПВЗ есть, но ближе
    может быть точка в соседнем НП (Яхрома и т.п.). Города без ПВЗ пропускаем.
    """
    seed = (seed_city or "").strip()
    region_code = None
    seed_city_code = None
    if seed:
        cities = _api_get(
            f"{CDEK_BASE}/location/cities",
            params={"city": seed, "country_codes": "RU", "size": 5},
            timeout=10,
        ).json() or []
        if cities:
            def _city_dist(c):
                clat, clon = c.get("latitude"), c.get("longitude")
                if clat is None or clon is None:
                    return 1e9
                return _haversine_km(target_lat, target_lon, clat, clon)
            cities = sorted(cities, key=_city_dist)
            region_code = cities[0].get("region_code")
            seed_city_code = cities[0].get("code")

    candidates = []
    if region_code is not None:
        region_cities = _api_get(
            f"{CDEK_BASE}/location/cities",
            params={"country_codes": "RU", "region_code": region_code, "size": 1000},
            timeout=30,
        ).json() or []
        for c in region_cities:
            clat, clon = c.get("latitude"), c.get("longitude")
            if clat is None or clon is None:
                continue
            d = _haversine_km(target_lat, target_lon, clat, clon)
            if d <= city_radius_km:
                candidates.append((d, c.get("code"), c.get("city") or ""))
        candidates.sort(key=lambda x: x[0])

    if seed_city_code and not any(c[1] == seed_city_code for c in candidates):
        candidates.insert(0, (0.0, seed_city_code, seed))

    best = None
    best_dist = 1e9
    cities_with_pvz = 0
    for _, code, city_name in candidates:
        if not code:
            continue
        try:
            pvzs = [p for p in get_pvz_list_by_city_code(int(code)) if p.get("lat") and p.get("lng")]
        except Exception:
            continue
        if not pvzs:
            continue
        cities_with_pvz += 1
        for p in pvzs:
            d = _haversine_km(target_lat, target_lon, p["lat"], p["lng"])
            if d < best_dist:
                best_dist = d
                best = {**p, "city_name": city_name, "distance_km": round(d, 1)}
        # Уже нашли очень близко — дальше не долбим API
        if best_dist <= 3.0:
            break
        if cities_with_pvz >= max_cities_with_pvz:
            break
    return best


def nearby_city_names(
    target_lat: float,
    target_lon: float,
    seed_city: str = "",
    radius_km: float = 30.0,
    limit: int = 8,
) -> list[str]:
    """Названия населённых пунктов региона в радиусе — для доп. поиска Яндекс ПВЗ."""
    seed = (seed_city or "").strip()
    region_code = None
    if seed:
        cities = _api_get(
            f"{CDEK_BASE}/location/cities",
            params={"city": seed, "country_codes": "RU", "size": 5},
            timeout=10,
        ).json() or []
        if cities:
            def _city_dist(c):
                clat, clon = c.get("latitude"), c.get("longitude")
                if clat is None or clon is None:
                    return 1e9
                return _haversine_km(target_lat, target_lon, clat, clon)
            region_code = sorted(cities, key=_city_dist)[0].get("region_code")
    if region_code is None:
        return []

    region_cities = _api_get(
        f"{CDEK_BASE}/location/cities",
        params={"country_codes": "RU", "region_code": region_code, "size": 1000},
        timeout=30,
    ).json() or []
    ranked = []
    for c in region_cities:
        name = (c.get("city") or "").strip()
        clat, clon = c.get("latitude"), c.get("longitude")
        if not name or clat is None or clon is None:
            continue
        d = _haversine_km(target_lat, target_lon, clat, clon)
        if d <= radius_km:
            ranked.append((d, name))
    ranked.sort(key=lambda x: x[0])
    out, seen = [], set()
    # Сначала более «городские» кандидаты: дальше 5 км от точки (деревни без ПВЗ часто ближе)
    # плюс сам seed_city если есть
    preferred = []
    if seed:
        preferred.append((0.0, seed))
    preferred.extend((d, n) for d, n in ranked if d >= 5.0)
    preferred.extend((d, n) for d, n in ranked if d < 5.0)
    for _, name in preferred:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= limit:
            break
    return out


def calculate_cost_by_city(city_name: str, tariff_code: Optional[int] = None) -> float:
    """Рассчитать итоговую стоимость с НДС по названию города (для шага одобрения КС)."""
    cities = _api_get(
        f"{CDEK_BASE}/location/cities",
        params={"city": city_name, "country_codes": "RU", "size": 1},
        timeout=10,
    ).json()
    if not cities:
        return 0.0
    return calculate_cost_by_city_code(int(cities[0].get("code")), tariff_code=tariff_code)


def calculate_cost_by_city_code(city_code: int, tariff_code: Optional[int] = None) -> float:
    """Стоимость с НДС до city_code. Если основной тариф недоступен — берём
    самый дешёвый склад-склад из tarifflist (иначе деревни без тарифа 136
    отваливаются, и fallback цепляет омоним за тысячи км)."""
    preferred = tariff_code or TARIFF_CODE
    packages = [{"weight": PACKAGE_WEIGHT, "length": PACKAGE_LENGTH,
                 "width": PACKAGE_WIDTH, "height": PACKAGE_HEIGHT}]
    payload = {
        "tariff_code":   preferred,
        "from_location": {"code": FROM_CITY_CODE},
        "to_location":   {"code": city_code},
        "packages":      packages,
    }
    try:
        r = _req.post(f"{CDEK_BASE}/calculator/tariff", json=payload, headers=_h(), timeout=10)
        if r.status_code == 403:
            r = _req.post(f"{CDEK_BASE}/calculator/tariff", json=payload, headers=_get_fresh_h(), timeout=10)
        if r.status_code == 200:
            d = r.json() or {}
            total = float(d.get("total_sum") or d.get("delivery_sum") or 0)
            if total > 0:
                return total
    except Exception:
        pass

    # Fallback: любой доступный склад-склад
    try:
        r = _req.post(
            f"{CDEK_BASE}/calculator/tarifflist",
            json={
                "from_location": {"code": FROM_CITY_CODE},
                "to_location":   {"code": city_code},
                "packages":      packages,
            },
            headers=_h(),
            timeout=15,
        )
        if r.status_code != 200:
            return 0.0
        tariffs = (r.json() or {}).get("tariff_codes") or []
        valid = [
            t for t in tariffs
            if t.get("delivery_sum") and "склад-склад" in (t.get("tariff_name") or "").lower()
        ]
        if not valid:
            valid = [t for t in tariffs if t.get("delivery_sum")]
        if not valid:
            return 0.0
        valid.sort(key=lambda t: float(t.get("delivery_sum") or 9999))
        delivery_sum = float(valid[0]["delivery_sum"])
        # tarifflist отдаёт без НДС — как в get_tariff_options
        return round(delivery_sum * (1 + VAT_RATE / 100), 2)
    except Exception:
        return 0.0


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


def get_tariff_options(to_pvz_code: str) -> list[dict]:
    """Возвращает 2 самых дешёвых тарифа типа склад-склад для выбранного ПВЗ клиента."""
    to_city_code = _pvz_to_city_code(to_pvz_code)
    payload = {
        "from_location": {"code": FROM_CITY_CODE},
        "to_location":   {"code": to_city_code},
        "packages": [{"weight": PACKAGE_WEIGHT, "length": PACKAGE_LENGTH, "width": PACKAGE_WIDTH, "height": PACKAGE_HEIGHT}],
    }
    r = _req.post(f"{CDEK_BASE}/calculator/tarifflist", json=payload, headers=_h(), timeout=15)
    r.raise_for_status()
    tariffs = r.json().get("tariff_codes", [])
    # Только тарифы склад-склад (delivery_point = ПВЗ, а не постамат/дверь)
    valid = [
        t for t in tariffs
        if t.get("delivery_sum") and "склад-склад" in t.get("tariff_name", "").lower()
    ]
    valid.sort(key=lambda t: (t.get("delivery_sum", 9999), t.get("period_min", 99)))

    # НДС считаем локально (×1.22), без доп. запросов к СДЭК — иначе +2 круга на каждый показ
    vat_mult = 1 + VAT_RATE / 100
    result = []
    for t in valid[:2]:
        delivery_sum = float(t["delivery_sum"])
        result.append({
            "tariff_code": t["tariff_code"],
            "name":        t["tariff_name"],
            "cost":        delivery_sum,                       # без НДС
            "cost_total":  round(delivery_sum * vat_mult, 2),  # с НДС — итоговая к оплате
            "period_min":  t.get("period_min", 2),
            "period_max":  t.get("period_max", 3),
        })
    return result


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
    return float(data.get("delivery_sum") or data.get("total_sum") or 0)


# ── Создание заказа ───────────────────────────────────────────────────────────

def create_order(
    req_id:            int,
    customer_name:     str,
    customer_phone:    str,
    to_pvz_code:       str,
    items_description: str = "Комплектующие",
    tariff_code:       Optional[int] = None,
    customer_email:    Optional[str] = None,
) -> dict:
    """
    Создаёт заказ в СДЭК. Возвращает {'uuid': ..., 'cdek_number': ..., 'tariff_code': ...}.
    cdek_number может быть пустым — придёт через webhook.
    """
    code = tariff_code or TARIFF_CODE

    sender: dict = {"name": SENDER_NAME}
    if SENDER_PHONE:
        sender["phones"] = [{"number": SENDER_PHONE}]

    recipient: dict = {
        "name":   customer_name,
        "phones": [{"number": customer_phone}],
    }
    if customer_email:
        recipient["email"] = customer_email

    payload = {
        "tariff_code":    code,
        "shipment_point": FROM_PVZ_CODE,           # ПВЗ откуда сдаём (Техническая 23Б — вместо from_location)
        "delivery_point": to_pvz_code,
        "comment":        f"Отправка деталей #{req_id}: {items_description[:200]}",
        "sender":         sender,
        "recipient":      recipient,
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
                "cost":     ITEM_COST,
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
        "uuid":         entity.get("uuid", ""),
        "cdek_number":  entity.get("cdek_number") or "",
        "tariff_code":  code,
    }


# ── Получить заказ по UUID (для получения cdek_number) ────────────────────────

def get_order_by_uuid(uuid: str) -> dict:
    r = _req.get(f"{CDEK_BASE}/orders/{uuid}", headers=_h(), timeout=10)
    r.raise_for_status()
    data = r.json()
    entity = data.get("entity") or {}
    detail = entity.get("delivery_detail") or {}
    actual_cost = detail.get("total_sum")
    # statuses идут в обратном хронологическом порядке — первый ([0]) самый свежий
    last_status = (entity.get("statuses") or [{}])[0]
    return {
        "cdek_number":  entity.get("cdek_number") or "",
        "status":       last_status.get("name", ""),
        "status_code":  last_status.get("code", ""),
        "actual_cost":  float(actual_cost) if actual_cost else None,
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
    msg["Subject"] = "Заявка на отправку детали — обновление статуса"

    body = f"""Здравствуйте, {customer_name}!

Мы рассмотрели вашу заявку на отправку детали.

К сожалению, выполнить её не можем — запрашиваемая позиция отсутствует на нашем складе.

Рекомендуем оформить возврат товара в личном кабинете Wildberries. \
Если нужна помощь — свяжитесь с нашей службой поддержки.

С уважением,
Команда поддержки Видовито"""

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
