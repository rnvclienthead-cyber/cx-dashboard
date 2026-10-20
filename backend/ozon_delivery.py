"""Ozon Доставка для бизнеса — ПВЗ→ПВЗ для доотправки деталей.

Ключи: OZON_DELIVERY_CLIENT_ID / OZON_DELIVERY_CLIENT_SECRET (OAuth UUID, не Seller API).
Сдача: ПВЗ Ozon на Глазунова 10 (Казань), не сортцентр.
Получение: ближайший ПВЗ к адресу клиента. Нужен аккаунт Ozon на телефон клиента.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests as _req

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

CLIENT_ID = os.getenv("OZON_DELIVERY_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("OZON_DELIVERY_CLIENT_SECRET", "").strip()
TOKEN_URL = os.getenv("OZON_DELIVERY_TOKEN_URL", "https://xapi.ozon.ru/oauth/token").strip()
BASE_URL = os.getenv("OZON_DELIVERY_BASE_URL", "https://api-delivery.ozon.ru").rstrip("/")
SCOPE = os.getenv("OZON_DELIVERY_SCOPE", "delivery-api.all").strip()

FROM_ADDRESS = os.getenv(
    "OZON_DELIVERY_FROM_ADDRESS",
    "Россия, Татарстан Республика, Казань, улица Глазунова, 10",
).strip()
SHIPMENT_METHOD_ID = os.getenv("OZON_DELIVERY_SHIPMENT_METHOD_ID", "").strip()
FROM_PVZ_ID = os.getenv("OZON_DELIVERY_FROM_PVZ_ID", "1224090").strip()

PACKAGE_WEIGHT_G = int(os.getenv("OZON_DELIVERY_WEIGHT_G", os.getenv("CDEK_PACKAGE_WEIGHT_G", "100")))
PACKAGE_L_MM = int(os.getenv("OZON_DELIVERY_LENGTH_MM", "100"))
PACKAGE_W_MM = int(os.getenv("OZON_DELIVERY_WIDTH_MM", "30"))
PACKAGE_H_MM = int(os.getenv("OZON_DELIVERY_HEIGHT_MM", "30"))
DECLARED_RUB = os.getenv("OZON_DELIVERY_DECLARED_RUB", "100")

# В малых городах ближайший ПВЗ Ozon может быть дальше, чем у СДЭК.
MAX_KM = 80.0
_POINTS_TTL = 6 * 3600
_CATALOG_TTL = 24 * 3600
# list отдаёт только id, без фильтра по городу. Раньше обрезка 20k
# (400 пачек info × 50) теряла ближайшие точки.
MAX_LIST_PAGES = 5000
_POINT_INFO_MAX_BATCHES = 4000
_QUOTE_TRIES = 16
_CITY_PREFIXES = (
    "г ", "город ", "ст-ца ", "ст.", "станица ", "посёлок ", "поселок ",
    "пгт ", "село ", "дер. ", "деревня ", "аул ", "хутор ",
)
_CACHE_FILE = Path(os.getenv(
    "OZON_DELIVERY_PVZ_CACHE",
    str(Path(__file__).resolve().parent / ".ozon_pvz_cache.json"),
))
_MAP_CACHE_FILE = Path(os.getenv(
    "OZON_DELIVERY_MAP_CACHE",
    str(Path(__file__).resolve().parent / ".ozon_map_points.json"),
))
_MAP_NEAR_LIMIT = 60

_token: Optional[str] = None
_token_expires: float = 0
_session = _req.Session()
_points_cache: tuple[float, list] = (0.0, [])
_enriched_cache: tuple[float, list] = (0.0, [])
_map_cache: tuple[float, list] = (0.0, [])
_from_cache: Optional[dict] = None
_last_list_truncated = False


def is_enabled() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET)


def normalize_phone(phone: str) -> Optional[str]:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits.startswith("7"):
        return "+" + digits
    return None


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlmb = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _token_fresh() -> str:
    global _token, _token_expires
    if _token and time.time() < _token_expires - 60:
        return _token
    resp = _session.post(
        TOKEN_URL,
        json={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
            "scope": [SCOPE],
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError("Ozon Delivery OAuth не вернул токен")
    expiry = data.get("expires_in")
    # Ozon отдаёт абсолютный unix timestamp
    if isinstance(expiry, (int, float)) and expiry > time.time() + 120:
        _token_expires = float(expiry)
    else:
        _token_expires = time.time() + 3000
    _token = token
    return _token


def _post(path: str, body: dict, timeout: int = 25, idempotency_key: Optional[str] = None) -> tuple[int, dict]:
    headers = {
        "Authorization": f"Bearer {_token_fresh()}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    url = f"{BASE_URL}{path}"
    resp = _session.post(url, json=body, headers=headers, timeout=timeout, allow_redirects=True)
    if resp.status_code in (401, 403):
        global _token, _token_expires
        _token = None
        _token_expires = 0
        headers["Authorization"] = f"Bearer {_token_fresh()}"
        resp = _session.post(url, json=body, headers=headers, timeout=timeout, allow_redirects=True)
    try:
        data = resp.json()
    except ValueError:
        data = {"message": (resp.text or "")[:240]}
    if not isinstance(data, dict):
        data = {"data": data}
    return resp.status_code, data


def _err_text(data: dict) -> str:
    if not isinstance(data, dict):
        return str(data or "")[:240]
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err)[:240]
    if err:
        return str(err)[:240]
    rows = data.get("results") or []
    if rows and isinstance(rows[0], dict):
        nested = rows[0].get("error")
        if isinstance(nested, dict):
            return str(nested.get("message") or nested.get("code") or nested)[:240]
        if nested:
            return str(nested)[:240]
    return str(data.get("message") or "")[:240]


def check_client(phone: str) -> dict:
    """Может ли этот телефон получать заказы Ozon Доставки (аккаунт Ozon)."""
    out = {"eligible": False, "phone": None, "reason": ""}
    if not is_enabled():
        out["reason"] = "Ключи Ozon Доставки не заданы"
        return out
    norm = normalize_phone(phone)
    out["phone"] = norm
    if not norm:
        out["reason"] = "Нужен российский номер +7"
        return out
    try:
        code, data = _post("/v1/delivery/check-client", {"phone_number": norm})
    except Exception as e:
        logger.warning("Ozon check-client failed: %s", e)
        out["reason"] = "Не удалось проверить аккаунт Ozon"
        return out
    if code != 200:
        out["reason"] = _err_text(data) or f"Ошибка Ozon ({code})"
        return out
    ok = bool(data.get("can_be_delivered"))
    out["eligible"] = ok
    if not ok:
        out["reason"] = "На этот номер нет аккаунта Ozon"
    return out


def order_external_id(req_id: int, attempt_key: Optional[str] = None) -> str:
    """Первый заказ — cx-425. Повтор после отмены — другой id, иначе Ozon вернёт старый."""
    base = f"cx-{int(req_id)}"
    extra = re.sub(r"[^A-Za-z0-9]+", "", (attempt_key or "").strip())[:24]
    return f"{base}-{extra}" if extra else base


def _is_req_external(value, req_id: int) -> bool:
    val = str(value or "").strip()
    prefix = f"cx-{int(req_id)}"
    return val == prefix or val.startswith(prefix + "-")


def _cutoff_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dimensions(
    weight_g: Optional[int] = None,
    length_mm: Optional[int] = None,
    width_mm: Optional[int] = None,
    height_mm: Optional[int] = None,
) -> dict:
    return {
        "weight_g": max(1, int(weight_g or PACKAGE_WEIGHT_G)),
        "length_mm": max(1, int(length_mm or PACKAGE_L_MM)),
        "width_mm": max(1, int(width_mm or PACKAGE_W_MM)),
        "height_mm": max(1, int(height_mm or PACKAGE_H_MM)),
    }


def _declared_floor() -> int:
    """Ozon не считает доставку при оценке ниже 100 ₽ (ошибка предрасчёта)."""
    try:
        return max(100, int(round(float(DECLARED_RUB))))
    except (TypeError, ValueError):
        return 100


def _declared_amount(declared_cost: Optional[float] = None) -> str:
    floor = _declared_floor()
    if declared_cost is None:
        return str(floor)
    try:
        value = float(declared_cost)
    except (TypeError, ValueError):
        return str(floor)
    return str(max(floor, int(round(value))))


def list_delivery_points(force: bool = False) -> list:
    """Кэш всех ПВЗ кабинета. Пустой список — в кабинете ещё нет метода сдачи."""
    global _points_cache, _last_list_truncated
    if not is_enabled():
        return []
    ts, cached = _points_cache
    if cached and not force and time.time() - ts < _POINTS_TTL:
        return cached
    points = []
    cursor = None
    truncated = False
    for page in range(MAX_LIST_PAGES):
        body = {"pagination": {"cursor": cursor, "limit": 100}}
        code, data = _post("/v1/delivery-point/list", body)
        if code != 200:
            logger.warning("Ozon delivery-point/list %s %s", code, _err_text(data))
            break
        chunk = data.get("delivery_points") or []
        points.extend(chunk)
        cursor = data.get("next_cursor")
        if not cursor or not chunk:
            break
        if page + 1 == MAX_LIST_PAGES:
            truncated = True
    if truncated and cursor:
        logger.warning(
            "Ozon delivery-point/list всё ещё не дочитан (%s точек) — каталог пометим неполным",
            len(points),
        )
    _last_list_truncated = truncated
    _points_cache = (time.time(), points)
    if not points:
        logger.info("Ozon delivery-point/list пуст — в кабинете нет метода сдачи в ПВЗ")
    else:
        logger.info("Ozon delivery-point/list: %s точек%s", len(points), " (обрезан)" if truncated else "")
    return points


def _load_catalog_file() -> dict:
    try:
        raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    points = raw.get("points")
    if not isinstance(points, list):
        return {}
    return raw


def _load_enriched_disk() -> Optional[list]:
    raw = _load_catalog_file()
    points = raw.get("points") if raw else None
    if not isinstance(points, list) or not points:
        return None
    ts = raw.get("ts")
    complete = bool(raw.get("complete"))
    if complete:
        return points
    if isinstance(ts, (int, float)) and time.time() - float(ts) <= _POINTS_TTL:
        return points
    return points


def _save_enriched_disk(points: list, complete: bool = False, listed: int = 0) -> None:
    payload = json.dumps({
        "ts": time.time(),
        "complete": bool(complete),
        "listed": int(listed or len(points)),
        "points": points,
    }, ensure_ascii=False)
    try:
        tmp = _CACHE_FILE.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(_CACHE_FILE)
    except OSError as e:
        logger.warning("Ozon PVZ cache write failed: %s", e)


def _point_ids_from_list(raw: list) -> list[int]:
    ids = []
    for p in raw:
        pid = p.get("delivery_point_id") or p.get("id")
        if isinstance(pid, int) and pid > 0:
            ids.append(pid)
    return ids


def _parse_missing_point_ids(err: str) -> set[int]:
    """Ozon: «Не найдены пункты выдачи: 140846,2654954.»"""
    found = set()
    for chunk in re.findall(r"\d+", err or ""):
        try:
            found.add(int(chunk))
        except ValueError:
            continue
    return found


def _rows_from_info(data: dict) -> list[dict]:
    out = []
    for row in data.get("delivery_points") or []:
        coords = row.get("coordinates") or {}
        lat, lon = coords.get("latitude"), coords.get("longitude")
        methods = row.get("shipment_method_ids") or []
        if isinstance(methods, int):
            methods = [methods]
        out.append({
            "id": row.get("delivery_point_id"),
            "name": row.get("name") or "",
            "address": row.get("full_address") or "",
            "lat": float(lat) if lat is not None else None,
            "lng": float(lon) if lon is not None else None,
            "active": bool(row.get("is_active", True)),
            "point_type": row.get("type") or "",
            "shipment_method_ids": [int(x) for x in methods if isinstance(x, int)],
        })
    return out


def _point_info(ids: list[int]) -> list[dict]:
    """Карточки ПВЗ. Если в пачке один мёртвый id — Ozon отдаёт 404 на всю пачку."""
    out: list[dict] = []
    pending = [int(x) for x in ids if isinstance(x, int) and x > 0]
    guard = 0
    while pending and guard < _POINT_INFO_MAX_BATCHES:
        guard += 1
        batch = pending[:50]
        pending = pending[50:]
        code, data = _post("/v1/delivery-point/info", {"delivery_point_ids": batch})
        if code == 200:
            out.extend(_rows_from_info(data))
            continue
        err = _err_text(data)
        missing = _parse_missing_point_ids(err) & set(batch)
        if missing:
            rest = [x for x in batch if x not in missing]
            pending = rest + pending
            continue
        if len(batch) == 1:
            logger.warning("Ozon delivery-point/info %s %s", code, err)
            continue
        mid = max(1, len(batch) // 2)
        pending = batch[:mid] + batch[mid:] + pending
    return out


def _expand_points_near(points: list[dict], lat: float, lon: float) -> list[dict]:
    """Список Ozon неполный: соседние id часто есть в кабинете, но не в list."""
    global _enriched_cache
    known = {int(p["id"]) for p in points if p.get("id")}
    nearby_ids = []
    for p in points:
        if p.get("id") is None or p.get("lat") is None or p.get("lng") is None:
            continue
        if _haversine_km(lat, lon, p["lat"], p["lng"]) > 20:
            continue
        nearby_ids.append(int(p["id"]))
    if not nearby_ids:
        return points
    probe = set()
    for pid in nearby_ids:
        for i in range(pid - 20, pid + 21):
            if i > 0 and i not in known:
                probe.add(i)
    extra = _point_info(sorted(probe)[:400])
    if not extra:
        return points
    methods = []
    for p in points:
        if p.get("shipment_method_ids"):
            methods = p["shipment_method_ids"]
            break
    by_id = {int(p["id"]): p for p in points if p.get("id")}
    for row in extra:
        pid = row.get("id")
        if not pid or int(pid) in by_id:
            continue
        if not row.get("shipment_method_ids") and methods:
            row["shipment_method_ids"] = list(methods)
        by_id[int(pid)] = row
    merged = list(by_id.values())
    ts, cached = _enriched_cache
    if cached:
        _enriched_cache = (ts or time.time(), merged)
    return merged


def _methods_by_id(raw: list) -> dict:
    out = {}
    for p in raw:
        pid = p.get("delivery_point_id") or p.get("id")
        methods = p.get("shipment_method_ids") or []
        if isinstance(methods, int):
            methods = [methods]
        if pid:
            out[pid] = [int(x) for x in methods if isinstance(x, int)]
    return out


def _enriched_points(force: bool = False) -> list[dict]:
    """Каталог с диска. Полную догрузку не делаем в HTTP-запросе — только sync_pvz_catalog()."""
    global _enriched_cache
    ts, cached = _enriched_cache
    if cached and not force:
        return cached
    disk = _load_enriched_disk()
    if disk:
        _enriched_cache = (time.time(), disk)
        return disk
    if force:
        return (sync_pvz_catalog(force=True) or {}).get("points") or []
    return []


def sync_pvz_catalog(force: bool = False) -> dict:
    """Дочитывает list и info без потолка 20k. Старые точки не выкидываем."""
    global _enriched_cache
    meta = _load_catalog_file()
    by_id = {}
    for p in meta.get("points") or []:
        if isinstance(p, dict) and p.get("id"):
            by_id[int(p["id"])] = p
    if (
        not force
        and meta.get("complete")
        and isinstance(meta.get("ts"), (int, float))
        and time.time() - float(meta["ts"]) < _CATALOG_TTL
        and len(by_id) > 20000
    ):
        points = list(by_id.values())
        _enriched_cache = (time.time(), points)
        return {"ok": True, "complete": True, "points": points, "listed": meta.get("listed") or len(points)}

    raw = list_delivery_points(force=True)
    listed_ids = _point_ids_from_list(raw)
    truncated = _last_list_truncated
    methods = _methods_by_id(raw)
    missing = [i for i in listed_ids if i not in by_id]
    fetched = 0
    if missing:
        logger.info("Ozon PVZ catalog: догружаем %s новых id (уже есть %s)", len(missing), len(by_id))
        chunk = 1500
        for i in range(0, len(missing), chunk):
            extra = _point_info(missing[i:i + chunk])
            fetched += len(extra)
            for row in extra:
                pid = row.get("id")
                if pid:
                    by_id[int(pid)] = row
            _save_enriched_disk(list(by_id.values()), complete=False, listed=len(listed_ids))
            logger.info("Ozon PVZ catalog: прогресс %s/%s новых, всего %s", fetched, len(missing), len(by_id))
    for row in by_id.values():
        pid = row.get("id")
        if pid and not row.get("shipment_method_ids"):
            row["shipment_method_ids"] = methods.get(pid) or methods.get(int(pid)) or []
    points = list(by_id.values())
    complete = bool(listed_ids) and not truncated and all(i in by_id for i in listed_ids)
    # 404 по части id — каталог всё равно считаем полным, если list дочитан
    if listed_ids and not truncated:
        complete = True
    _save_enriched_disk(points, complete=complete, listed=len(listed_ids))
    _enriched_cache = (time.time(), points)
    logger.info(
        "Ozon PVZ catalog: %s точек, list=%s, новых карточек=%s, complete=%s",
        len(points), len(listed_ids), fetched, complete,
    )
    return {
        "ok": True,
        "complete": complete,
        "points": points,
        "listed": len(listed_ids),
        "fetched": fetched,
    }


def _city_key(city: str) -> str:
    s = re.sub(r"\s+", " ", (city or "").lower().replace("ё", "е")).strip(" .,")
    for prefix in _CITY_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):].strip(" .,")
    return s


def _address_has_city(address: str, city_key: str) -> bool:
    """Город как отдельное слово в адресе. «Гиагинский район» ≠ «Гиагинская»."""
    if not city_key or len(city_key) < 3:
        return False
    addr = (address or "").lower().replace("ё", "е")
    return re.search(rf"(?<![а-яa-z]){re.escape(city_key)}(?![а-яa-z])", addr) is not None


def _rank_local_points(
    points: list[dict],
    lat: float,
    lon: float,
    city: str = "",
) -> list[dict]:
    """Сначала пункты в городе клиента, внутри — строго по расстоянию."""
    city_key = _city_key(city)
    scored = []
    for p in points:
        if not p.get("id") or p.get("lat") is None or p.get("lng") is None:
            continue
        if p.get("active") is False:
            continue
        d = _haversine_km(lat, lon, p["lat"], p["lng"])
        if d > MAX_KM:
            continue
        same_city = _address_has_city(p.get("address") or "", city_key)
        scored.append((same_city, d, p))
    if not scored:
        return []
    in_city = [x for x in scored if x[0]]
    pool = in_city if in_city else scored
    pool.sort(key=lambda x: x[1])
    ranked = []
    seen = set()
    for same, d, p in pool:
        pid = int(p["id"])
        if pid in seen:
            continue
        seen.add(pid)
        ranked.append({**p, "distance_km": round(d, 2), "same_city": same})
    if ranked:
        logger.info(
            "Ozon PVZ rank city=%r pool=%s nearest=%s %s (%.2f km)",
            city_key, len(ranked), ranked[0].get("id"), ranked[0].get("address"),
            ranked[0]["distance_km"],
        )
    return ranked


def _load_map_disk(allow_stale: bool = True) -> list[dict]:
    try:
        raw = json.loads(_MAP_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    points = raw.get("points")
    if not isinstance(points, list) or not points:
        return []
    ts = raw.get("ts")
    fresh = isinstance(ts, (int, float)) and time.time() - float(ts) <= _CATALOG_TTL
    if fresh or allow_stale:
        return points
    return []


def _save_map_disk(points: list) -> None:
    try:
        tmp = _MAP_CACHE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"ts": time.time(), "points": points}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_MAP_CACHE_FILE)
    except OSError as e:
        logger.warning("Ozon map cache write failed: %s", e)


def _seller_map_points(force: bool = False) -> list[dict]:
    """Полная карта ПВЗ Ozon (id + координаты). delivery-point/list для бизнеса дырявый."""
    global _map_cache
    ts, cached = _map_cache
    if cached and not force and time.time() - ts < _CATALOG_TTL:
        return cached
    if not force:
        disk = _load_map_disk(allow_stale=False)
        if disk:
            _map_cache = (time.time(), disk)
            return disk
    try:
        from . import ozon_client
        if not ozon_client.client_id() or not ozon_client.api_key():
            return cached or _load_map_disk()
        data = ozon_client.post("/v1/delivery/point/list", {}, timeout=60)
    except Exception as e:
        logger.warning("Ozon delivery/point/list failed: %s", e)
        return cached or _load_map_disk()
    out = []
    for row in data.get("points") or []:
        pid = row.get("map_point_id")
        coords = row.get("coordinate") or {}
        lat, lon = coords.get("lat"), coords.get("long")
        if not isinstance(pid, int) or lat is None or lon is None:
            continue
        try:
            out.append({"id": int(pid), "lat": float(lat), "lng": float(lon)})
        except (TypeError, ValueError):
            continue
    if out:
        _save_map_disk(out)
        _map_cache = (time.time(), out)
        logger.info("Ozon map points: %s", len(out))
        return out
    return cached or _load_map_disk()


def _merge_extra_rows(points: list[dict], extra: list[dict]) -> list[dict]:
    if not extra:
        return points
    methods = []
    for p in points:
        if p.get("shipment_method_ids"):
            methods = p["shipment_method_ids"]
            break
    by_id = {int(p["id"]): p for p in points if p.get("id")}
    for row in extra:
        pid = row.get("id")
        if not pid or int(pid) in by_id:
            continue
        if not row.get("shipment_method_ids") and methods:
            row["shipment_method_ids"] = list(methods)
        by_id[int(pid)] = row
    return list(by_id.values())


def _expand_from_map(points: list[dict], lat: float, lon: float) -> list[dict]:
    """Берём ближайшие точки с карты Ozon — их нет в кабинетном list, но info/checkout работают."""
    mmap = _seller_map_points()
    if not mmap:
        return points
    known = {int(p["id"]) for p in points if p.get("id")}
    nearby = []
    for m in mmap:
        pid = m.get("id")
        if not pid or int(pid) in known or m.get("lat") is None or m.get("lng") is None:
            continue
        d = _haversine_km(lat, lon, m["lat"], m["lng"])
        if d > MAX_KM:
            continue
        nearby.append((d, int(pid)))
    if not nearby:
        return points
    nearby.sort()
    extra = _point_info([pid for _, pid in nearby[:_MAP_NEAR_LIMIT]])
    if extra:
        logger.info(
            "Ozon map expand: +%s точек около %.5f,%.5f (ближайшая %.2f км)",
            len(extra), lat, lon, nearby[0][0],
        )
    return _merge_extra_rows(points, extra)


def _expand_around_ranked(points: list[dict], ranked: list[dict], lat: float, lon: float) -> list[dict]:
    """Карта Ozon + соседи по id. Кабинетный list неполный: нет Новорогожской 15 и Красной 337."""
    points = _expand_from_map(points, lat, lon)
    ranked = _rank_local_points(points, lat, lon) or ranked
    if not ranked:
        return _expand_points_near(points, lat, lon)
    seeds = ranked[:5]
    known = {int(p["id"]) for p in points if p.get("id")}
    probe = set()
    for p in seeds:
        pid = int(p["id"])
        for i in range(pid - 80, pid + 81):
            if i > 0 and i not in known:
                probe.add(i)
    extra = _point_info(sorted(probe)[:400]) if probe else []
    return _merge_extra_rows(points, extra)


def _nearest(points: list[dict], lat: float, lon: float) -> Optional[dict]:
    ranked = _rank_local_points(points, lat, lon)
    return ranked[0] if ranked else None


def from_dropoff_point() -> Optional[dict]:
    """ПВЗ сдачи: Глазунова 10, как в кабинете Ozon. Не ближайший к складу."""
    global _from_cache
    if _from_cache:
        return _from_cache
    if FROM_PVZ_ID.isdigit():
        info = _point_info([int(FROM_PVZ_ID)])
        if info:
            _from_cache = info[0]
            return _from_cache
    points = _enriched_points()
    if not points:
        return None
    needle = "глазунов"
    for p in points:
        addr = (p.get("address") or "").lower()
        if needle in addr and "казань" in addr and p.get("active") is not False:
            _from_cache = p
            return _from_cache
    lat = os.getenv("OZON_DELIVERY_FROM_LAT", "").strip()
    lon = os.getenv("OZON_DELIVERY_FROM_LON", "").strip()
    try:
        lat_f = float(lat) if lat else None
        lon_f = float(lon) if lon else None
    except ValueError:
        lat_f = lon_f = None
    if lat_f is None or lon_f is None:
        lat_f, lon_f = 55.733235, 49.145614  # Глазунова 10
    nearest = _nearest(points, float(lat_f), float(lon_f))
    if nearest:
        _from_cache = nearest
    return nearest


def _shipment_method_id(point: dict) -> Optional[int]:
    if SHIPMENT_METHOD_ID.isdigit():
        return int(SHIPMENT_METHOD_ID)
    ids = point.get("shipment_method_ids") or []
    return int(ids[0]) if ids else None


def _location_cutoff(method_id: int, lat: float, lon: float) -> Optional[str]:
    code, data = _post("/v1/delivery/location", {
        "coordinates": {"latitude": lat, "longitude": lon},
        "shipment_methods": [{"shipment_method_id": method_id, "cutoff_at": _cutoff_iso()}],
    })
    if code != 200:
        return None
    rows = data.get("results") or []
    if not rows:
        return None
    row = rows[0]
    if row.get("error"):
        return None
    return row.get("cutoff_at") or _cutoff_iso()


def _money_amount(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, dict):
        raw = value.get("amount")
    else:
        raw = value
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def list_nearby_points(lat: float, lon: float, limit: int = 20, city: str = "") -> list[dict]:
    """ПВЗ Ozon в городе клиента, от ближайшего. Без цены и без проверки телефона."""
    if not is_enabled() or lat is None or lon is None:
        return []
    points = _enriched_points()
    ranked = _rank_local_points(points, float(lat), float(lon), city)
    points = _expand_around_ranked(points, ranked, float(lat), float(lon))
    ranked = _rank_local_points(points, float(lat), float(lon), city)
    out = []
    for p in ranked[: max(1, int(limit))]:
        out.append({
            "pvz_code": str(p["id"]),
            "pvz_name": p.get("name") or "",
            "pvz_address": p.get("address") or "",
            "lat": p["lat"],
            "lng": p["lng"],
            "distance_km": p.get("distance_km"),
            "shipment_method_ids": p.get("shipment_method_ids") or [],
            "id": p["id"],
            "point_type": p.get("point_type") or "",
        })
    return out


def quote_to_point(
    point: dict,
    phone: str,
    lat: float,
    lon: float,
    weight_g: Optional[int] = None,
    declared_cost: Optional[float] = None,
    length_mm: Optional[int] = None,
    width_mm: Optional[int] = None,
    height_mm: Optional[int] = None,
) -> Optional[dict]:
    method_id = _shipment_method_id(point)
    if not method_id:
        return None
    cut_lat = point.get("lat")
    cut_lon = point.get("lng")
    if cut_lat is None or cut_lon is None:
        cut_lat, cut_lon = lat, lon
    cutoff = _location_cutoff(method_id, float(cut_lat), float(cut_lon)) or _cutoff_iso()
    dims = _dimensions()
    if weight_g:
        dims = {
            "weight_g": max(1, int(weight_g)),
            "length_mm": max(1, int(length_mm or 400)),
            "width_mm": max(1, int(width_mm or 300)),
            "height_mm": max(1, int(height_mm or 200)),
        }
    posting = {
        "request_id": 1,
        "shipment_method_id": method_id,
        "cutoff_at": cutoff,
        "declared_value": {"amount": _declared_amount(declared_cost), "currency_code": "RUB"},
        "dimensions": dims,
    }
    code, data = _post("/v1/delivery-point/check-availability", {
        "delivery_point_ids": [point["id"]],
        "shipment_method_id": method_id,
        "postings": [{
            "request_id": 1,
            "cutoff_at": cutoff,
            "declared_value": posting["declared_value"],
            "dimensions": posting["dimensions"],
        }],
    })
    if code == 200:
        for row in data.get("results") or []:
            if row.get("error"):
                logger.info("Ozon PVZ %s unavailable: %s", point["id"], row.get("error"))
                return None
    code, data = _post("/v1/order/checkout", {
        "recipient": {"phone_number": phone},
        "delivery": {"delivery_point": {"delivery_point_id": point["id"]}},
        "postings": [posting],
    })
    if code != 200:
        logger.info("Ozon checkout %s %s", code, _err_text(data))
        return None
    rows = data.get("results") or []
    if not rows or rows[0].get("error"):
        logger.info("Ozon checkout error %s", (rows[0].get("error") if rows else data))
        return None
    posting_out = rows[0].get("posting") or {}
    delivery = _money_amount(posting_out.get("estimated_delivery_cost")) or 0
    insurance = _money_amount(posting_out.get("estimated_insurance_cost")) or 0
    total = round(delivery + insurance, 2)
    if total <= 0:
        return None
    return {
        "cost": total,
        "delivery_cost": round(delivery, 2),
        "insurance_cost": round(insurance, 2),
        "days": posting_out.get("estimated_delivery_days"),
        "cutoff_at": posting_out.get("cutoff_at") or cutoff,
        "shipment_method_id": method_id,
    }


def get_candidate(
    lat: Optional[float],
    lon: Optional[float],
    phone: str,
    city: str = "",
) -> Optional[dict]:
    """Ближайший ПВЗ в городе клиента + цена. Не первый попавшийся из обрезанного списка."""
    if not is_enabled() or lat is None or lon is None:
        return None
    chk = check_client(phone)
    if not chk.get("eligible"):
        return {
            "available": False,
            "reason": chk.get("reason") or "Нет аккаунта Ozon",
            "client_ok": False,
        }
    points = _enriched_points()
    if not points:
        return {
            "available": False,
            "reason": "В кабинете Ozon Доставки нет ПВЗ. Включите сдачу в пункт выдачи (не сортцентр).",
            "client_ok": True,
        }
    ranked = _rank_local_points(points, float(lat), float(lon), city)
    points = _expand_around_ranked(points, ranked, float(lat), float(lon))
    ranked = _rank_local_points(points, float(lat), float(lon), city)
    if not ranked:
        return {
            "available": False,
            "reason": "Рядом нет пункта Ozon",
            "client_ok": True,
        }
    fallback_dest = None
    for dest in ranked[:_QUOTE_TRIES]:
        if not dest.get("shipment_method_ids"):
            for p in points:
                if p.get("shipment_method_ids"):
                    dest["shipment_method_ids"] = p["shipment_method_ids"]
                    break
        quote = quote_to_point(dest, chk["phone"], float(lat), float(lon))
        if quote:
            src = from_dropoff_point()
            logger.info(
                "Ozon PVZ chosen id=%s city=%r dist=%.2f km addr=%s tries_ok",
                dest.get("id"), city, dest.get("distance_km") or 0, dest.get("address"),
            )
            return {
                "available": True,
                "client_ok": True,
                "pvz_code": str(dest["id"]),
                "pvz_name": dest.get("name") or "",
                "pvz_address": dest.get("address") or "",
                "lat": dest.get("lat"),
                "lng": dest.get("lng"),
                "distance_km": dest.get("distance_km"),
                "cost": quote["cost"],
                "days": quote.get("days"),
                "shipment_method_id": quote["shipment_method_id"],
                "cutoff_at": quote["cutoff_at"],
                "from_pvz_id": str(src["id"]) if src else None,
                "from_pvz_address": (src or {}).get("address") or FROM_ADDRESS,
                "from_pvz_name": (src or {}).get("name") or "",
            }
        fallback_dest = fallback_dest or dest
    dest = fallback_dest or ranked[0]
    return {
        "available": False,
        "reason": "Ozon не посчитал доставку до ближайших пунктов",
        "client_ok": True,
        "pvz_code": str(dest["id"]),
        "pvz_address": dest.get("address") or "",
    }


def _recover_created_order(req_id: int, external_id: str = "") -> Optional[dict]:
    """Если create оборвался по сети, заказ мог уже появиться в кабинете."""
    try:
        found = find_posting(req_id=req_id)
    except Exception as e:
        logger.warning("Ozon recover find_posting #%s: %s", req_id, e)
        return None
    if not found:
        return None
    posting_number = str(found.get("posting_number") or "").strip()
    order_number = str(found.get("order_number") or "").strip()
    if not posting_number and not order_number:
        return None
    st = str(found.get("status") or "").lower()
    if st in CANCELED_STATUSES:
        return None
    if posting_number:
        try:
            approve_posting(posting_number)
        except Exception:
            pass
    logger.warning(
        "Ozon recovered existing order for #%s: %s / %s (%s)",
        req_id, order_number or posting_number, posting_number, external_id,
    )
    return {
        "order_number": order_number or posting_number,
        "posting_number": posting_number,
        "days": found.get("estimated_delivery_days"),
        "external_id": external_id or str(found.get("order_external_id") or ""),
    }


def create_order(
    *,
    req_id: int,
    customer_name: str,
    customer_phone: str,
    destination_pvz_id: str,
    items_description: str = "Комплектующие",
    shipment_method_id: Optional[int] = None,
    cutoff_at: Optional[str] = None,
    attempt_key: Optional[str] = None,
    declared_cost: Optional[float] = None,
    weight_g: Optional[int] = None,
    length_mm: Optional[int] = None,
    width_mm: Optional[int] = None,
    height_mm: Optional[int] = None,
) -> dict:
    if not is_enabled():
        raise RuntimeError("Ключи Ozon Доставки не заданы")
    phone = normalize_phone(customer_phone)
    if not phone:
        raise RuntimeError("Телефон клиента не подходит для Ozon")
    try:
        dest_id = int(destination_pvz_id)
    except (TypeError, ValueError) as e:
        raise RuntimeError("Некорректный ПВЗ Ozon") from e
    method_id = shipment_method_id
    if not method_id:
        src = from_dropoff_point()
        method_id = _shipment_method_id(src or {})
    if not method_id:
        try:
            method_id = int(DEFAULT_DROPOFF_BARCODE)
        except (TypeError, ValueError):
            method_id = None
    if not method_id:
        raise RuntimeError("Не выбран метод сдачи Ozon (ПВЗ). Настройте его в кабинете доставки.")
    external_id = order_external_id(req_id, attempt_key)
    name = (customer_name or "Клиент").strip()[:120]
    posting = {
        "request_id": 1,
        "shipment_method_id": int(method_id),
        "cutoff_at": cutoff_at or _cutoff_iso(),
        "declared_value": {"amount": _declared_amount(declared_cost), "currency_code": "RUB"},
        "dimensions": _dimensions(weight_g, length_mm, width_mm, height_mm),
        "posting_external_id": external_id,
        "description": (items_description or "Комплектующие")[:500],
    }
    try:
        code, data = _post(
            "/v1/order/create",
            {
                "order_external_id": external_id,
                "recipient": {"phone_number": phone, "full_name": name},
                "delivery": {"delivery_point": {"delivery_point_id": dest_id}},
                "postings": [posting],
            },
            timeout=60,
            idempotency_key=str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"cx-ozon-{external_id}")),
        )
    except Exception as e:
        recovered = _recover_created_order(req_id, external_id)
        if recovered:
            return recovered
        raise RuntimeError(str(e) or "Ozon create timeout") from e
    if code >= 400:
        recovered = _recover_created_order(req_id, external_id)
        if recovered:
            return recovered
        raise RuntimeError(_err_text(data) or f"Ozon create {code}")
    order_number = data.get("order_number") or ""
    postings = data.get("postings") or []
    posting_number = (postings[0].get("posting_number") if postings else "") or order_number
    if not order_number and not posting_number:
        recovered = _recover_created_order(req_id, external_id)
        if recovered:
            return recovered
        raise RuntimeError(_err_text(data) or "Ozon не вернул номер заказа")
    if posting_number:
        st = posting_status(posting_number)
        if st in CANCELED_STATUSES:
            raise RuntimeError(
                "Ozon вернул уже отменённый заказ. Нужен новый номер отправления, не старый."
            )
        approved = approve_posting(posting_number)
        if not approved.get("ok"):
            logger.warning(
                "Ozon posting approve %s failed: %s",
                posting_number, approved.get("error"),
            )
        else:
            # Не держим одобрение заявки 12 секунд — подтверждение догонит updater.
            ensure_ready_for_shipping(posting_number, wait_s=3)
    return {
        "order_number": order_number or posting_number,
        "posting_number": posting_number,
        "days": postings[0].get("estimated_delivery_days") if postings else None,
        "external_id": external_id,
    }


CANCELED_STATUSES = frozenset({"canceled", "cancelled"})

READY_FOR_SHIPPING = frozenset({
    "ready_for_shipping", "ready_to_ship", "awaiting_deliver",
})
WAITING_DROPOFF = frozenset({
    "created", "forming", "ready_for_shipping", "ready_to_ship",
    "awaiting_deliver", "awaiting_packaging", "awaiting_approve",
})
NEEDS_APPROVE = frozenset({
    "", "created", "forming", "awaiting_approve",
})


def posting_status(posting_number: str) -> str:
    pn = (posting_number or "").strip()
    if not pn:
        return ""
    code, data = _post("/v1/posting/info", {"posting_numbers": [pn]})
    if code != 200:
        return ""
    rows = data.get("postings") or []
    return str((rows[0] if rows else {}).get("status") or "").strip()


def approve_posting(posting_number: str) -> dict:
    """Без этого ПВЗ не принимает ни этикетку, ни штрихкод метода."""
    pn = (posting_number or "").strip()
    if not pn:
        return {"ok": False, "error": "no posting"}
    code, data = _post("/v1/posting/approve", {"posting_number": pn})
    if code >= 400:
        return {"ok": False, "error": _err_text(data) or f"approve {code}", "status_code": code}
    return {"ok": True}


def ensure_ready_for_shipping(posting_number: str, wait_s: float = 20) -> str:
    pn = (posting_number or "").strip()
    if not pn:
        return ""
    status = posting_status(pn)
    if status in READY_FOR_SHIPPING:
        return status
    if status in ("", "created"):
        approve_posting(pn)
    deadline = time.time() + max(0.0, float(wait_s))
    while True:
        status = posting_status(pn)
        if status in READY_FOR_SHIPPING:
            return status
        if status in ("cancelled", "canceled"):
            return status
        if time.time() >= deadline:
            return status
        time.sleep(1.5)


def fetch_label_pdf(posting_number: str) -> bytes:
    """Официальная этикетка Ozon с QR. Доступна после ready_for_shipping."""
    pn = (posting_number or "").strip()
    if not pn:
        return b""
    headers = {
        "Authorization": f"Bearer {_token_fresh()}",
        "Content-Type": "application/json",
    }
    url = f"{BASE_URL}/v1/posting/label"
    resp = _session.post(url, json={"posting_number": pn}, headers=headers, timeout=30)
    raw = resp.content or b""
    if raw.startswith(b"%PDF"):
        return raw
    try:
        data = resp.json()
    except ValueError:
        return b""
    for key in ("file", "content", "pdf", "label", "message"):
        val = data.get(key) if isinstance(data, dict) else None
        if isinstance(val, str) and val.startswith("%PDF"):
            return val.encode("latin-1", errors="ignore")
        if isinstance(val, str) and len(val) > 100:
            try:
                import base64
                decoded = base64.b64decode(val)
                if decoded.startswith(b"%PDF"):
                    return decoded
            except Exception:
                pass
    return b""


_postings_cache: tuple[float, list] = (0.0, [])


def list_postings(force: bool = False) -> list[dict]:
    """Все отправления кабинета. Нужны posting_number — на этикетке не order_number."""
    global _postings_cache
    ts, cached = _postings_cache
    if cached and not force and time.time() - ts < 300:
        return cached
    out = []
    cursor = None
    for _ in range(80):
        code, data = _post("/v1/posting/search", {"pagination": {"cursor": cursor, "limit": 100}})
        if code != 200:
            logger.warning("Ozon posting/search %s %s", code, _err_text(data))
            break
        chunk = data.get("postings") or []
        out.extend(chunk)
        cursor = data.get("next_cursor")
        if not cursor or not chunk:
            break
    _postings_cache = (time.time(), out)
    return out


def _pick_req_posting(rows: list, req_id: int) -> Optional[dict]:
    matched = [
        p for p in rows
        if _is_req_external(p.get("order_external_id"), req_id)
        or _is_req_external(p.get("posting_external_id"), req_id)
    ]
    if not matched:
        return None
    live = [
        p for p in matched
        if str(p.get("status") or "").lower() not in CANCELED_STATUSES
    ]
    pool = live or matched
    pool.sort(key=lambda p: str(p.get("created_at") or ""), reverse=True)
    return pool[0]


def find_posting(*, req_id: Optional[int] = None, order_number: str = "", posting_number: str = "") -> Optional[dict]:
    order_number = (order_number or "").strip()
    posting_number = (posting_number or "").strip()
    if posting_number:
        code, data = _post("/v1/posting/info", {"posting_numbers": [posting_number]})
        if code == 200:
            rows = data.get("postings") or []
            if rows:
                found = rows[0]
                st = str(found.get("status") or "").lower()
                if st not in CANCELED_STATUSES or not req_id:
                    return found
    rows = list_postings()
    if req_id:
        picked = _pick_req_posting(rows, req_id)
        if picked:
            return picked
    for p in rows:
        if posting_number and p.get("posting_number") == posting_number:
            return p
        if order_number and p.get("order_number") == order_number:
            return p
    if req_id or order_number:
        rows = list_postings(force=True)
        if req_id:
            picked = _pick_req_posting(rows, req_id)
            if picked:
                return picked
        for p in rows:
            if order_number and p.get("order_number") == order_number:
                return p
    return None


def resolve_label_barcode(*, req_id: int, order_number: str = "", posting_number: str = "") -> str:
    """На этикетку — posting_number. Номер заказа EB-… сканер ПВЗ не читает."""
    posting_number = (posting_number or "").strip()
    if posting_number and not posting_number.upper().startswith("REQ"):
        return posting_number
    found = find_posting(req_id=req_id, order_number=order_number, posting_number=posting_number)
    if found and found.get("posting_number"):
        return str(found["posting_number"])
    if (order_number or "").strip():
        return order_number.strip()
    return f"REQ{int(req_id):06d}"


# Штрихкод метода сдачи в нашем ПВЗ (Казань, Глазунова 10).
DEFAULT_DROPOFF_BARCODE = "1020005029438500"


def dropoff_barcode_info() -> dict:
    """Штрихкод метода сдачи — его сканируют в ПВЗ перед посылками."""
    env_bc = os.getenv("OZON_DELIVERY_DROPOFF_BARCODE", "").strip()
    method = int(SHIPMENT_METHOD_ID) if SHIPMENT_METHOD_ID.isdigit() else None
    src = from_dropoff_point()
    if not method:
        method = _shipment_method_id(src or {})
    barcode = env_bc or (str(method) if method else "") or DEFAULT_DROPOFF_BARCODE
    if not method and barcode.isdigit():
        method = int(barcode)
    return {
        "barcode": barcode,
        "method_id": method,
        "address": (src or {}).get("address") or FROM_ADDRESS,
        "from_pvz_id": str((src or {}).get("id") or FROM_PVZ_ID or ""),
        "from_pvz_name": (src or {}).get("name") or "",
    }


def barcode_svg(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        import re
        from reportlab.graphics.barcode import createBarcodeDrawing
        from reportlab.graphics import renderSVG
        drawing = createBarcodeDrawing(
            "Code128", value=text, barHeight=140, humanReadable=False, quiet=True,
        )
        svg = renderSVG.drawToString(drawing)
        w = re.search(r'\bwidth="([\d.]+)"', svg)
        h = re.search(r'\bheight="([\d.]+)"', svg)
        if w and h and "viewBox" not in svg:
            svg = re.sub(
                r"<svg\b",
                f'<svg viewBox="0 0 {w.group(1)} {h.group(1)}"',
                svg,
                count=1,
            )
        svg = re.sub(r'\bwidth="[^"]*"', 'width="100%"', svg, count=1)
        svg = re.sub(r'\bheight="[^"]*"', 'height="100%"', svg, count=1)
        if "preserveAspectRatio" not in svg:
            svg = svg.replace("<svg", '<svg preserveAspectRatio="none"', 1)
        return svg
    except Exception as e:
        logger.warning("Ozon barcode svg failed: %s", e)
        return ""


# Статусы Ozon Доставки → наши. Пока заказ ждёт сдачи — остаёмся в «собрано».
# Приёмка на ПВЗ сдачи = shipped (как СДЭК RECEIVED_AT_SHIPMENT_WAREHOUSE).
# Боевой статус после сдачи в ПВЗ — on_way («доставляется»).
STATUS_MAP = {
    "accepted": "shipped",
    "received": "shipped",
    "received_at_pickup_point": "shipped",
    "at_senders_pickup_point": "shipped",
    "dropoff": "shipped",
    "handed_over": "shipped",
    "in_pickup_point": "shipped",
    "in_transit": "in_transit",
    "on_way": "in_transit",
    "on_the_way": "in_transit",
    "sent": "in_transit",
    "sent_to_delivery": "in_transit",
    "sent_for_delivery": "in_transit",
    "in_delivery": "in_transit",
    "in_delivery_point": "ready_for_pickup",
    "delivering": "in_transit",
    "transportation": "in_transit",
    "arrived": "ready_for_pickup",
    "arrived_at_pickup_point": "ready_for_pickup",
    "at_pickup_point": "ready_for_pickup",
    "at_delivery_point": "ready_for_pickup",
    "in_pvz": "ready_for_pickup",
    "ready_for_pickup": "ready_for_pickup",
    "waiting_for_customer": "ready_for_pickup",
    "issued": "delivered",
    "delivered": "delivered",
    "received_by_customer": "delivered",
    "given_out": "delivered",
    "cancelled": "returned",
    "canceled": "returned",
    "returned": "returned",
    "returning": "returned",
    "not_delivered": "returned",
}


def _status_key(raw: str) -> str:
    return (raw or "").strip().lower().replace(" ", "_").replace("-", "_")


def needs_posting_approve(raw: str) -> bool:
    return _status_key(raw) in NEEDS_APPROVE


def map_posting_status(raw: str) -> Optional[str]:
    """Наш статус доотправки или None, если ещё ждём сдачи / статус неизвестен."""
    key = _status_key(raw)
    if not key or key in WAITING_DROPOFF:
        return None
    if key in STATUS_MAP:
        return STATUS_MAP[key]
    for token, mapped_status in sorted(STATUS_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        if token in key:
            return mapped_status
    return None


def _raw_status_from_posting(posting: dict) -> str:
    for key in ("status", "state", "posting_status", "status_name", "current_status"):
        val = posting.get(key)
        if isinstance(val, dict):
            val = val.get("name") or val.get("code") or val.get("id")
        if val:
            return str(val)
    return ""


def get_order_status(track: str) -> dict:
    """Статус отправления Ozon Доставки по номеру заказа/постинга."""
    num = (track or "").strip()
    if not num:
        return {}
    posting = None
    code, data = _post("/v1/posting/info", {"posting_numbers": [num]})
    if code == 200:
        rows = data.get("postings") or []
        posting = rows[0] if rows else None
    if not posting:
        found = find_posting(order_number=num, posting_number=num)
        if found and found.get("posting_number"):
            code, data = _post("/v1/posting/info", {"posting_numbers": [found["posting_number"]]})
            if code == 200:
                rows = data.get("postings") or []
                posting = rows[0] if rows else found
            else:
                posting = found
    if not posting:
        if code != 200:
            logger.info("Ozon posting/info %s %s", code, _err_text(data))
        return {}
    raw = _raw_status_from_posting(posting)
    mapped = map_posting_status(raw)
    if raw and not mapped and _status_key(raw) not in WAITING_DROPOFF:
        logger.info("Ozon unknown status %r for %s", raw, num)
    return {
        "status": mapped,
        "raw_status": raw,
        "posting": posting,
        "order_number": posting.get("order_number") or "",
        "posting_number": posting.get("posting_number") or "",
        "eta_raw": posting.get("delivery_at") or posting.get("original_delivery_at") or "",
    }

