import secrets
import os
import json
import uuid
import asyncio
import logging
import base64
import tempfile
import hashlib
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional
from ..database import get_db, db_session
from ..wb_return_logistics import FALLBACK, fetch_avg_return_delivery
from .auth import get_current_user
from weasyprint import HTML
from weasyprint.text.fonts import FontConfiguration

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    _limiter = Limiter(key_func=get_remote_address)
    _rate_limit = _limiter.limit("10/hour")
    HAS_LIMITER = True
except ImportError:
    HAS_LIMITER = False
    _rate_limit = lambda f: f

logger = logging.getLogger(__name__)

def _resolve_upload_dir() -> Path:
    preferred = Path(os.getenv("CX_UPLOAD_BASE_DIR", "/root/cx-dashboard/uploads")) / "reshipment"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "cx-dashboard-uploads" / "reshipment"
        fallback.mkdir(parents=True, exist_ok=True)
        logger.warning("reshipment uploads fallback to %s", fallback)
        return fallback


UPLOAD_DIR = _resolve_upload_dir()
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
MAX_FILE_SIZE_MB = 15
_ACT_COMPANY_NAME = os.getenv("RESHIPMENT_ACT_COMPANY_NAME", "ООО «Видовито»")
_ACT_SIGNER_NAME = os.getenv("RESHIPMENT_ACT_SIGNER_NAME", "")
_ACT_SIGNER_ROLE = os.getenv("RESHIPMENT_ACT_SIGNER_ROLE", "Ответственный за передачу")
_ACT_STAMP_PATH = os.getenv("RESHIPMENT_ACT_STAMP_PATH", "").strip()
_ACT_CUSTOMER_NAME = os.getenv("RESHIPMENT_ACT_CUSTOMER_NAME", 'ООО "РНВ"')
_ACT_EXECUTOR_NAME = os.getenv("RESHIPMENT_ACT_EXECUTOR_NAME", "ООО Яндекс.Доставка")
_ACT_SENDER_NAME = os.getenv("RESHIPMENT_ACT_SENDER_NAME", 'ООО "РНВ"')
_ACT_PARTNER_NAME = os.getenv("RESHIPMENT_ACT_PARTNER_NAME", 'ООО "Яндекс"')
_ACT_DECLARED_VALUE = os.getenv("RESHIPMENT_ACT_DECLARED_VALUE", "0.01")
_ACT_WEIGHT_KG = os.getenv("RESHIPMENT_ACT_WEIGHT_KG", "0.001")
_ACT_CARGO_PLACES = os.getenv("RESHIPMENT_ACT_CARGO_PLACES", "1")

# ── Публичный роутер (без авторизации) ──────────────────────────────────────
public_router = APIRouter(prefix="/api/v1/reshipment", tags=["Reshipment / Доотправка"])

# ── Внутренний роутер (с авторизацией) ──────────────────────────────────────
router = APIRouter(
    prefix="/api/v1/reshipment",
    tags=["Reshipment / Доотправка"],
    dependencies=[Depends(get_current_user)]
)


def _maps_js_key() -> str:
    """Ключ JS API Яндекс.Карт. Не путать с ключом LLM (хотя локально они совпадают)."""
    return (
        os.getenv("YANDEX_MAPS_JS_KEY")
        or os.getenv("VITE_YANDEX_MAPS_KEY")
        or os.getenv("YANDEX_API_KEY")
        or ""
    ).strip()


@router.get("/maps-config")
def get_maps_config():
    """Ключ карты для кабинета КС. Не зашиваем в сборку фронта — иначе git-деплой теряет .env."""
    key = _maps_js_key()
    if not key:
        raise HTTPException(status_code=503, detail="Ключ Яндекс.Карт не настроен")
    return {"apikey": key}


# ── Схемы ────────────────────────────────────────────────────────────────────

class ApproveRequest(BaseModel):
    matched_srid: Optional[str] = None
    match_notes: Optional[str] = None
    moderator_comment: Optional[str] = None
    processed_by: Optional[str] = None
    tariff_code: Optional[int] = None
    delivery_method: Optional[str] = None  # 'cdek' | 'yandex' | 'ozon'


class RejectRequest(BaseModel):
    moderator_comment: str
    processed_by: Optional[str] = None


def _skip_reject_stock_template(promo_code, claim_result) -> bool:
    """Не слать «не можем отправить / оформите возврат», если уходит промокод или возврат WB уже есть."""
    if promo_code:
        return True
    if not claim_result or claim_result.get("skipped") == "no_claim":
        return False
    return bool(claim_result.get("claim_id") or claim_result.get("ok"))


class PromoCodeUpdate(BaseModel):
    code: str
    expires_at: Optional[str] = None  # ISO date, напр. "2026-07-06"
    wb_uuid: Optional[str] = None  # ID промокода из кабинета WB
    valid_from: Optional[str] = None  # ISO date начала действия
    processed_by: Optional[str] = None


PROMO_DISCOUNT_PERCENT = 15


def _promo_history_stats(db: Session) -> list[dict]:
    """История промокодов + отправки из CX и заказы/скидка из финотчёта WB."""
    rows = db.execute(text("""
        SELECT
            h.id,
            h.code,
            h.wb_uuid,
            h.discount_percent,
            h.valid_from,
            h.expires_at,
            h.created_at,
            h.created_by,
            h.is_active,
            (
                SELECT COUNT(*)::int
                FROM reshipment_requests r
                WHERE UPPER(TRIM(r.promo_code)) = UPPER(TRIM(h.code))
                  AND r.promo_sent_at IS NOT NULL
            ) AS sent_count,
            CASE
                WHEN h.wb_uuid IS NULL OR TRIM(h.wb_uuid) = '' THEN NULL
                ELSE (
                    SELECT COUNT(*)::int
                    FROM wb_report_detail d
                    WHERE d.uuid_promocode = h.wb_uuid
                      AND d.doc_type_name = 'Продажа'
                )
            END AS orders_count,
            CASE
                WHEN h.wb_uuid IS NULL OR TRIM(h.wb_uuid) = '' THEN NULL
                ELSE (
                    SELECT COUNT(*)::int
                    FROM wb_report_detail d
                    WHERE d.uuid_promocode = h.wb_uuid
                      AND d.doc_type_name = 'Возврат'
                )
            END AS returns_count,
            CASE
                WHEN h.wb_uuid IS NULL OR TRIM(h.wb_uuid) = '' THEN NULL
                ELSE (
                    SELECT COALESCE(SUM(
                        COALESCE(d.retail_price, 0)
                        * COALESCE(NULLIF(d.sale_price_promocode_discount_prc, 0), h.discount_percent, 15)
                        / 100.0
                    ), 0)
                    FROM wb_report_detail d
                    WHERE d.uuid_promocode = h.wb_uuid
                      AND d.doc_type_name = 'Продажа'
                )
            END AS discount_sales_sum,
            CASE
                WHEN h.wb_uuid IS NULL OR TRIM(h.wb_uuid) = '' THEN NULL
                ELSE (
                    SELECT COALESCE(SUM(
                        COALESCE(d.retail_price, 0)
                        * COALESCE(NULLIF(d.sale_price_promocode_discount_prc, 0), h.discount_percent, 15)
                        / 100.0
                    ), 0)
                    FROM wb_report_detail d
                    WHERE d.uuid_promocode = h.wb_uuid
                      AND d.doc_type_name = 'Возврат'
                )
            END AS discount_returns_sum
        FROM reshipment_promo_history h
        ORDER BY h.is_active DESC, h.created_at DESC, h.id DESC
    """)).mappings().all()
    out = []
    for r in rows:
        sales = r["discount_sales_sum"]
        rets = r["discount_returns_sum"]
        net = None
        if sales is not None:
            net = round(float(sales) - float(rets or 0), 2)
        out.append({
            "id": r["id"],
            "code": r["code"],
            "wb_uuid": r["wb_uuid"],
            "discount_percent": float(r["discount_percent"] or PROMO_DISCOUNT_PERCENT),
            "valid_from": r["valid_from"],
            "expires_at": r["expires_at"],
            "created_at": r["created_at"],
            "created_by": r["created_by"],
            "is_active": bool(r["is_active"]),
            "sent_count": int(r["sent_count"] or 0),
            "orders_count": None if r["orders_count"] is None else int(r["orders_count"]),
            "returns_count": None if r["returns_count"] is None else int(r["returns_count"]),
            "discount_sum": net,
        })
    return out

_RU_MONTHS_GEN = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def _fmt_ru_date(d) -> str:
    if not d:
        return ""
    return f"{d.day} {_RU_MONTHS_GEN.get(d.month, '')}"


def _require_yandex_act_access(current_user: dict):
    if current_user.get("role") not in ("admin", "warehouse_manager"):
        raise HTTPException(status_code=403, detail="Только для администратора и менеджера склада")


def _require_yandex_act_delete_access(current_user: dict):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Только администратор может удалять акты")


def _get_yandex_act_stamp_data_uri() -> str:
    if not _ACT_STAMP_PATH:
        return ""
    try:
        path = Path(_ACT_STAMP_PATH)
        if not path.exists() or not path.is_file():
            return ""
        mime = "image/png"
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif path.suffix.lower() == ".webp":
            mime = "image/webp"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    except Exception:
        return ""


def _next_yandex_act_number(db: Session) -> str:
    prefix = datetime.date.today().strftime("YA-%Y%m%d-")
    last = db.execute(text("""
        SELECT act_number
        FROM yandex_dispatch_acts
        WHERE act_number LIKE :prefix
        ORDER BY act_number DESC
        LIMIT 1
    """), {"prefix": f"{prefix}%"}).scalar()
    seq = 1
    if last:
        try:
            seq = int(str(last).rsplit("-", 1)[-1]) + 1
        except Exception:
            seq = 1
    return f"{prefix}{seq:03d}"


def _fallback_customer_track_number(row) -> str:
    token_source = str(
        row.get("chat_token")
        or row.get("confirmation_token")
        or row.get("track_number")
        or row["id"]
    )
    return f"reship-{row['id']}-{hashlib.sha1(token_source.encode('utf-8')).hexdigest()[:8]}"


def _customer_track_number_for_act(row) -> str:
    """Номер в акте должен совпадать с номером заказа в Яндексе, не с локальным хешем."""
    track = (row.get("track_number") or "").strip()
    if track:
        try:
            from .. import yandex_ndd
            real = yandex_ndd.get_operator_request_id(track)
            if real:
                return real
        except Exception as e:
            logger.warning("yandex operator_request_id for #%s: %s", row.get("id"), e)
    return _fallback_customer_track_number(row)


def _serialize_yandex_dispatch_rows(rows) -> list[dict]:
    payload = []
    for idx, row in enumerate(rows, 1):
        customer_track = _customer_track_number_for_act(row)
        payload.append({
            "line_no": idx,
            "request_id": row["id"],
            "customer_track_number": customer_track,
            "track_number": row.get("track_number") or "",
            "customer_name": row.get("customer_name") or "",
            "customer_phone": row.get("customer_phone") or "",
            "city": row.get("address_city") or row.get("client_pvz_city") or "",
            "pickup_address": row.get("yandex_pvz_address") or row.get("client_pvz_address") or "",
            "pickup_name": row.get("yandex_pvz_name") or "",
            "items_to_send": row.get("items_to_send") or "",
            "shipping_cost": float(row["shipping_cost"]) if row.get("shipping_cost") is not None else None,
            "packed_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        })
    return payload


def _render_yandex_dispatch_act_html(
    act_number: str,
    created_at,
    payload: list[dict],
    total_shipping_cost: float,
    created_by: str = "",
) -> str:
    created_dt = created_at or datetime.datetime.now()
    act_date = created_dt.strftime("%d.%m.%Y")
    rows_html = []
    for row in payload:
        rows_html.append(f"""
            <tr>
              <td>{row['line_no']}</td>
              <td>{row['customer_track_number']}</td>
              <td>{row['track_number'] or '—'}</td>
              <td class="num">{_ACT_DECLARED_VALUE}</td>
              <td class="num">{_ACT_WEIGHT_KG}</td>
              <td class="num">{_ACT_CARGO_PLACES}</td>
            </tr>
        """.strip())
    total_weight = len(payload) * float(_ACT_WEIGHT_KG)
    total_places = len(payload) * int(_ACT_CARGO_PLACES)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <style>
    @page {{ size: A4 portrait; margin: 16mm 16mm 18mm; }}
    body {{ font-family: DejaVu Sans, Arial, sans-serif; color: #111; font-size: 11px; line-height: 1.35; }}
    h1 {{ margin: 0 0 12mm; font-size: 18px; font-weight: 700; }}
    .party {{ margin-bottom: 9mm; font-size: 11px; }}
    .lead {{ margin: 0 0 14mm; font-size: 11px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 3mm; }}
    th, td {{ border: 1px solid #222; padding: 2.6mm 3mm; vertical-align: top; }}
    th {{ text-align: left; font-size: 10px; font-weight: 700; }}
    .num {{ text-align: right; white-space: nowrap; }}
    .post-table {{ margin-top: 10mm; }}
    .post-table p {{ margin: 10mm 0 0; font-size: 11px; font-weight: 700; }}
    .page-break {{ page-break-before: always; }}
    .sign-title {{ margin: 0 0 10mm; font-size: 11px; font-weight: 700; }}
    .sign-section {{ margin: 0 0 14mm; }}
    .line-a {{ width: 118mm; border-bottom: 1px solid #111; height: 0; margin-bottom: 8mm; }}
    .line-b {{ width: 64mm; border-bottom: 1px solid #111; height: 0; display: inline-block; vertical-align: middle; }}
    .line-c {{ width: 40mm; border-bottom: 1px solid #111; height: 0; display: inline-block; vertical-align: middle; }}
    .sign-row {{ margin-top: 8mm; white-space: nowrap; }}
    .trust {{ margin: 8mm 0 0; }}
  </style>
</head>
<body>
  <h1>Акт приема-передачи Отправления от {act_date}</h1>

  <div class="party">
    <div><strong>Заказчик:</strong> {_ACT_CUSTOMER_NAME}</div>
    <div><strong>Исполнитель:</strong> {_ACT_EXECUTOR_NAME}</div>
    <div><strong>Отправитель:</strong> {_ACT_SENDER_NAME}</div>
    <div><strong>Партнер:</strong> {_ACT_PARTNER_NAME}</div>
  </div>

  <p class="lead">
    Настоящий акт составлен о том, что в указанную выше дату (дата приема Отправления)
    Отправитель передал, а Партнер принял следующие Отправления, подлежащие передаче
    получателям Заказчика:
  </p>

  <table>
    <thead>
      <tr>
        <th style="width:7%">№ п/п</th>
        <th style="width:28%">№ Отправления в системе заказчика</th>
        <th style="width:25%">№ Отправления в системе Исполнителя</th>
        <th style="width:13%">Объявленная ценность</th>
        <th style="width:11%">Вес, кг</th>
        <th style="width:16%">Кол-во грузовых мест, шт</th>
      </tr>
    </thead>
    <tbody>
      {"".join(rows_html)}
      <tr>
        <td colspan="4" class="num"><strong>ИТОГО</strong></td>
        <td class="num"><strong>{total_weight:.3f}</strong></td>
        <td class="num"><strong>{total_places}</strong></td>
      </tr>
    </tbody>
  </table>

  <div class="post-table">
    <p>Передаваемые заказы упакованы в индивидуальные упаковки. Упаковка не нарушена. Замечаний по внешнему виду заказов со стороны Партнера не имеется.</p>
    <p>По акту передано {total_places} шт. заказов в ненарушенной упаковке. По настоящему акту произведена приемка по количеству грузовых мест</p>
    <p>Партнером не производилась приемка по количеству и наименованию товаров, входящих в заказ.</p>
  </div>

  <div class="page-break"></div>

  <p class="sign-title">Приемка была осуществлена по количеству грузовых мест. По акту передано {total_places} шт. грузовых мест</p>

  <div class="sign-section">
    <p class="sign-title">Отправления передал (Отправитель):</p>
    <div class="line-a"></div>
    <div class="sign-row">
      <span class="line-b"></span>
      (<span class="line-c"></span>)
      (подпись)
      (расшифровка)
    </div>
  </div>

  <div class="sign-section">
    <p class="sign-title">Отправления принял (Партнер):</p>
    <div class="line-b" style="width: 76mm;"></div>
    <div class="trust">(реквизиты доверенности представителя Исполнителя)</div>
    <div class="sign-row">
      <span class="line-b"></span>
      (<span class="line-c"></span>)
      (подпись)
      (расшифровка)
    </div>
  </div>

  <div class="sign-section">
    <p class="sign-title">Отправления принял (Сортировочный центр):</p>
    <div class="line-b" style="width: 76mm;"></div>
    <div class="trust">(реквизиты доверенности представителя Исполнителя)</div>
    <div class="sign-row">
      <span class="line-b"></span>
      (<span class="line-c"></span>)
      (подпись)
      (расшифровка)
    </div>
  </div>
</body>
</html>"""


@router.get("/promo-code")
def get_promo_code(db: Session = Depends(get_db)):
    row = db.execute(text(
        "SELECT value, updated_at, updated_by, expires_at FROM reshipment_settings WHERE key = 'promo_code'"
    )).mappings().first()
    active = db.execute(text("""
        SELECT wb_uuid, valid_from FROM reshipment_promo_history
        WHERE is_active = TRUE
        ORDER BY id DESC LIMIT 1
    """)).mappings().first()
    return {
        "status": "success",
        "code": (row["value"] if row else "") or "",
        "updated_at": row["updated_at"] if row else None,
        "updated_by": row["updated_by"] if row else None,
        "expires_at": row["expires_at"] if row else None,
        "wb_uuid": (active["wb_uuid"] if active else None) or "",
        "valid_from": active["valid_from"] if active else None,
        "discount_percent": PROMO_DISCOUNT_PERCENT,
        "history": _promo_history_stats(db),
    }


@router.post("/promo-code")
def set_promo_code(payload: PromoCodeUpdate, db: Session = Depends(get_db)):
    code = payload.code.strip().upper()
    wb_uuid = (payload.wb_uuid or "").strip() or None
    expires_at = payload.expires_at or None
    valid_from = payload.valid_from or None
    by = payload.processed_by

    db.execute(text("""
        UPDATE reshipment_settings
        SET value = :code, updated_at = NOW(), updated_by = :by, expires_at = :expires_at
        WHERE key = 'promo_code'
    """), {"code": code, "by": by, "expires_at": expires_at})

    # Если уже есть активный с тем же кодом — обновляем его, иначе закрываем старый и создаём новый
    active = db.execute(text("""
        SELECT id, code FROM reshipment_promo_history WHERE is_active = TRUE ORDER BY id DESC LIMIT 1
    """)).mappings().first()

    if active and (active["code"] or "").upper() == code:
        db.execute(text("""
            UPDATE reshipment_promo_history SET
                wb_uuid = COALESCE(:wb_uuid, wb_uuid),
                expires_at = COALESCE(:expires_at, expires_at),
                valid_from = COALESCE(:valid_from, valid_from),
                created_by = COALESCE(:by, created_by)
            WHERE id = :id
        """), {
            "wb_uuid": wb_uuid, "expires_at": expires_at, "valid_from": valid_from,
            "by": by, "id": active["id"],
        })
    else:
        db.execute(text("UPDATE reshipment_promo_history SET is_active = FALSE WHERE is_active = TRUE"))
        db.execute(text("""
            INSERT INTO reshipment_promo_history
                (code, wb_uuid, discount_percent, valid_from, expires_at, created_by, is_active)
            VALUES
                (:code, :wb_uuid, :pct, COALESCE(:valid_from, CURRENT_DATE), :expires_at, :by, TRUE)
        """), {
            "code": code, "wb_uuid": wb_uuid, "pct": PROMO_DISCOUNT_PERCENT,
            "valid_from": valid_from, "expires_at": expires_at, "by": by,
        })

    db.commit()
    return {"status": "success", "code": code, "history": _promo_history_stats(db)}


@router.patch("/promo-code/history/{item_id}")
def update_promo_history_uuid(item_id: int, payload: dict, db: Session = Depends(get_db)):
    """Дописать ID промокода WB к уже сохранённому коду в истории."""
    wb_uuid = (payload.get("wb_uuid") or "").strip() or None
    if not wb_uuid:
        raise HTTPException(status_code=400, detail="Укажите wb_uuid")
    res = db.execute(text("""
        UPDATE reshipment_promo_history SET wb_uuid = :wb_uuid WHERE id = :id
        RETURNING id
    """), {"wb_uuid": wb_uuid, "id": item_id})
    if not res.first():
        raise HTTPException(status_code=404, detail="Код не найден в истории")
    db.commit()
    return {"status": "success", "history": _promo_history_stats(db)}


class WarehouseRejectRequest(BaseModel):
    rejection_reason: str
    processed_by: Optional[str] = None


class BulkConfirmRequest(BaseModel):
    ids: list[int]
    processed_by: Optional[str] = None
    tariff_code: Optional[int] = None


class ShipYandexRequest(BaseModel):
    track_number: str
    shipping_cost: Optional[float] = None
    processed_by: Optional[str] = None


# ── Публичные эндпоинты ───────────────────────────────────────────────────────

@public_router.get("/categories")
def get_categories(db: Session = Depends(get_db)):
    """Список категорий товаров для формы"""
    rows = db.execute(text("""
        SELECT DISTINCT category_1 FROM wb_assortment
        WHERE matrix_status IN ('В матрице', 'Новинка')
          AND category_1 IS NOT NULL AND category_1 != ''
        ORDER BY category_1
    """)).fetchall()

    if rows:
        return {"categories": [r[0] for r in rows]}

    rows = db.execute(text("""
        SELECT DISTINCT category FROM wb_logistics
        WHERE category IS NOT NULL AND category != ''
        ORDER BY category
    """)).fetchall()
    return {"categories": [r[0] for r in rows]}


@public_router.get("/pvz")
def get_pvz_list(city: str = Query(..., min_length=2)):
    """Список ПВЗ СДЭК по городу — для формы клиента."""
    try:
        from ..cdek import get_pvz_list
        points = get_pvz_list(city)
        return {"status": "success", "data": points}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка получения ПВЗ: {e}")


@public_router.get("/suggest-address")
def suggest_address(q: str = Query("", min_length=0), count: int = Query(7, ge=1, le=10)):
    """Подсказки адресов DaData для формы клиента (ключ только на сервере)."""
    from ..pickup_resolver import suggest_addresses
    query = (q or "").strip()
    if len(query) < 2:
        return {"status": "success", "suggestions": []}
    return {"status": "success", "suggestions": suggest_addresses(query, count=count)}


@public_router.get("/nearest-pickup")
def get_nearest_pickup(
    city: str = Query(..., min_length=2),
    address: str = Query(""),
    lat: Optional[float] = Query(None),
    lon: Optional[float] = Query(None),
    phone: str = Query(""),
):
    """
    Превью для формы клиента: проверяет, что хотя бы один перевозчик (СДЭК, Яндекс или Ozon)
    смог подобрать точку рядом с адресом. Перевозчик и цена клиенту НЕ
    показываются — окончательный выбор делает КС на этапе одобрения заявки.
    Отправка деталей — только по России.
    """
    try:
        from ..pickup_resolver import (
            get_both_candidates, resolve_address,
            is_foreign_address_hint, address_missing_street,
            RUSSIA_ONLY_MESSAGE, STREET_REQUIRED_MESSAGE,
        )
        if is_foreign_address_hint(city, address):
            return {
                "status": "outside_russia",
                "message": RUSSIA_ONLY_MESSAGE,
                "data": None,
            }
        normalized = resolve_address(address or city)
        if address_missing_street(normalized, address=address, city=city):
            return {
                "status": "need_street",
                "message": STREET_REQUIRED_MESSAGE,
                "data": None,
            }
        if lat is None or lon is None:
            if normalized:
                city = normalized["city"]
                address = normalized["address"]
                lat = normalized["lat"]
                lon = normalized["lon"]
            elif is_foreign_address_hint(city, address) or not (address or city):
                return {
                    "status": "outside_russia" if is_foreign_address_hint(city, address) else "not_found",
                    "message": RUSSIA_ONLY_MESSAGE if is_foreign_address_hint(city, address) else None,
                    "data": None,
                }
        candidates = get_both_candidates(city=city, address=address or city, lat=lat, lon=lon, phone=phone or "")
        cdek_c, yd_c, oz_c = candidates.get("cdek"), candidates.get("yandex"), candidates.get("ozon")
        oz_ok = bool(oz_c and oz_c.get("available"))
        if not cdek_c and not yd_c and not oz_ok:
            # Нет ПВЗ рядом — для зарубежья (или опечаток) даём то же понятное сообщение,
            # если адрес выглядит как не-РФ; иначе обычный not_found.
            if is_foreign_address_hint(city, address, (normalized or {}).get("address")):
                return {
                    "status": "outside_russia",
                    "message": RUSSIA_ONLY_MESSAGE,
                    "data": None,
                }
            return {"status": "not_found", "data": None}
        # Для превью клиенту достаточно любого найденного адреса — без деталей точки/цены
        pick = cdek_c or yd_c or (oz_c if oz_ok else None)
        return {
            "status": "success",
            "data": {
                "pvz_address": pick["pvz_address"],
                "address": (normalized or {}).get("address") or address or city,
                "city": (normalized or {}).get("city") or city,
                "lat": (normalized or {}).get("lat", lat),
                "lon": (normalized or {}).get("lon", lon),
                "address_imprecise": (normalized or {}).get("address_imprecise", False),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка подбора пункта выдачи: {e}")


@public_router.get("/ozon-check")
def ozon_check(phone: str = Query("")):
    """На форме заявки: можно ли этому телефону получать Ozon Доставку."""
    from .. import ozon_delivery
    result = ozon_delivery.check_client(phone)
    eligible = bool(result.get("eligible"))
    if eligible:
        hint = (
            "Можем отправить через Ozon — обычно это выгоднее. "
            "Забрать посылку можно в пункте Ozon по штрихкоду в приложении."
        )
    else:
        hint = (
            result.get("reason")
            or "На этот номер нет аккаунта Ozon"
        )
        hint += ". Тогда отправим СДЭК или Яндекс. Чтобы открыть Ozon — зарегистрируйтесь в приложении Ozon на этот номер."
    return {
        "status": "success",
        "eligible": eligible,
        "hint": hint,
        "enabled": ozon_delivery.is_enabled(),
    }


@public_router.post("/upload-photo")
async def upload_photo(request: Request, file: UploadFile = File(...)):
    """Загрузка фото от клиента"""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Допустимые форматы: JPG, PNG, WEBP, HEIC")

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Файл слишком большой (максимум {MAX_FILE_SIZE_MB} МБ)")

    safe_name = f"{uuid.uuid4().hex}{ext}"
    file_path = UPLOAD_DIR / safe_name
    file_path.write_bytes(contents)
    return {"filename": safe_name, "url": f"/uploads/reshipment/{safe_name}"}


def _find_open_reshipment(db: Session, phone: str, chat_id: str | None):
    return db.execute(text("""
        SELECT id FROM reshipment_requests
        WHERE status NOT IN ('rejected', 'delivered')
          AND (
            customer_phone = :phone
            OR (:chat IS NOT NULL AND (wb_chat_id = :chat OR ym_chat_id = :chat OR ozon_chat_id = :chat))
          )
        ORDER BY id DESC
        LIMIT 1
    """), {"phone": phone, "chat": chat_id}).mappings().first()


def claim_submit_token(db: Session, token: str):
    """Занять ссылку сразу, до долгого подбора ПВЗ. Иначе две вкладки успевают создать две заявки."""
    row = db.execute(text("""
        UPDATE reshipment_chat_tokens
        SET used_at = NOW()
        WHERE token = :t
          AND used_at IS NULL
          AND request_id IS NULL
          AND COALESCE(expired, FALSE) IS NOT TRUE
          AND (expires_at IS NULL OR expires_at > NOW())
        RETURNING chat_id, mp
    """), {"t": token}).mappings().first()
    return dict(row) if row else None


def release_submit_token(db: Session, token: str) -> None:
    db.execute(text("""
        UPDATE reshipment_chat_tokens
        SET used_at = NULL
        WHERE token = :t AND request_id IS NULL
    """), {"t": token})


@public_router.post("/submit")
def submit_reshipment(
    request: Request,
    customer_name: str = Form(...),
    customer_phone: str = Form(...),
    customer_email: str = Form(None),
    order_number: str = Form(None),
    product_category: str = Form(None),
    problem_type: str = Form(...),
    items_to_send: str = Form(...),
    # ПВЗ — новые поля
    client_pvz_code: str = Form(None),
    client_pvz_address: str = Form(None),
    client_pvz_city: str = Form(None),
    # Координаты адреса клиента (геокодирование на фронте) — если переданы,
    # пункт выдачи и перевозчик подбираются автоматически (см. pickup_resolver)
    client_lat: float = Form(None),
    client_lon: float = Form(None),
    # Старые адресные поля (оставляем для совместимости)
    address_postal: str = Form(None),
    address_region: str = Form(None),
    address_city: str = Form(None),
    address_street: str = Form(None),
    address_house: str = Form(None),
    personal_data_consent: bool = Form(...),
    photo_files: str = Form(None),
    honeypot: str = Form(""),
    # WB чат — привязка если форма открыта по ссылке из чата
    wb_chat_id: str = Form(None),
    # Токен из ссылки менеджера (новая схема привязки через токен)
    chat_token: str = Form(None),
    # Salebot — ID подписчика, если форма открыта по ссылке из бота (?sbc=...)
    salebot_client_id: str = Form(None),
    db: Session = Depends(get_db)
):
    """Отправка публичной формы клиентом"""
    if honeypot:
        return JSONResponse({"status": "success", "id": 0, "message": "Заявка принята."})

    if not personal_data_consent:
        raise HTTPException(status_code=400, detail="Необходимо согласие на обработку персональных данных")
    if not customer_name.strip():
        raise HTTPException(status_code=400, detail="Укажите имя")
    if not customer_phone.strip():
        raise HTTPException(status_code=400, detail="Укажите телефон")
    if not problem_type.strip():
        raise HTTPException(status_code=400, detail="Выберите тип проблемы")
    if not items_to_send.strip():
        raise HTTPException(status_code=400, detail="Укажите, что нужно отправить")
    if not address_city and not client_pvz_city:
        raise HTTPException(status_code=400, detail="Укажите город доставки")
    from ..pickup_resolver import STREET_REQUIRED_MESSAGE, address_missing_street, resolve_address
    _addr_for_street = (client_pvz_address or address_street or "").strip()
    _city_for_street = (address_city or client_pvz_city or "").strip()
    _resolved_street = resolve_address(_addr_for_street or _city_for_street) if (_addr_for_street or _city_for_street) else None
    if address_missing_street(_resolved_street, address=_addr_for_street, city=_city_for_street):
        raise HTTPException(status_code=400, detail=STREET_REQUIRED_MESSAGE)

    # Отправка деталей — только по России (на этапе формы)
    from ..pickup_resolver import is_foreign_address_hint, RUSSIA_ONLY_MESSAGE
    if is_foreign_address_hint(
        client_pvz_city, client_pvz_address, address_city, address_region, address_postal,
    ):
        raise HTTPException(status_code=400, detail=RUSSIA_ONLY_MESSAGE)

    # ── Защита от дублей ──────────────────────────────────────────────────────
    _phone_clean = customer_phone.strip()
    _chat_clean  = (wb_chat_id or "").strip() or None
    _token_clean = (chat_token or "").strip() or None
    _token_mp    = None
    token_claimed = False

    # Токен одноразовый: занимаем ссылку сразу, до подбора ПВЗ (он может идти минуты).
    # Иначе клиент жмёт ещё раз / открывает вторую вкладку — и получаются две заявки.
    if _token_clean:
        _tok_check = db.execute(text("""
            SELECT used_at, expired, expires_at, request_id, chat_id, mp
            FROM reshipment_chat_tokens
            WHERE token = :t
        """), {"t": _token_clean}).mappings().first()
        if not _tok_check:
            raise HTTPException(
                status_code=400,
                detail="Ссылка недействительна. Попросите менеджера выслать новую ссылку в чате.",
            )
        from ..chat_bot import form_token_block_reason
        _block = form_token_block_reason(
            db, _token_clean, chat_id=_tok_check.get("chat_id") or _chat_clean,
        )
        if _block == "return_approved":
            raise HTTPException(
                status_code=400,
                detail="По этому заказу возврат уже одобрен. Ссылка на форму доотправки больше не действует.",
            )
        if _tok_check["used_at"] or _tok_check["request_id"]:
            rid = _tok_check["request_id"]
            raise HTTPException(
                status_code=409,
                detail=(
                    f"По этой ссылке заявка уже принята{f' (#{rid})' if rid else ''}. "
                    "Попросите менеджера выслать новую ссылку — старую использовать нельзя."
                ),
            )
        if _tok_check["expired"] or (
            _tok_check["expires_at"]
            and _tok_check["expires_at"] < __import__("datetime").datetime.utcnow()
        ):
            raise HTTPException(
                status_code=400,
                detail="Ссылка устарела. Попросите менеджера выслать новую ссылку в чате.",
            )
        if not _chat_clean and _tok_check["chat_id"]:
            _chat_clean = _tok_check["chat_id"]
        _token_mp = (_tok_check.get("mp") or "").strip().lower() or None
        claimed = claim_submit_token(db, _token_clean)
        if not claimed:
            raise HTTPException(
                status_code=409,
                detail="По этой ссылке заявка уже принята. Попросите менеджера выслать новую ссылку.",
            )
        db.commit()
        token_claimed = True
        if not _chat_clean and claimed.get("chat_id"):
            _chat_clean = claimed["chat_id"]
        if not _token_mp:
            _token_mp = (claimed.get("mp") or "").strip().lower() or None

    _existing = _find_open_reshipment(db, _phone_clean, _chat_clean)
    if _existing:
        if token_claimed:
            release_submit_token(db, _token_clean)
            db.commit()
        raise HTTPException(
            status_code=409,
            detail=f"Заявка #{_existing['id']} по этому клиенту уже обрабатывается. "
                   "Дублирование запрещено.",
        )
    # ── Конец защиты от дублей ───────────────────────────────────────────────

    shipping_address = client_pvz_address or ", ".join(filter(None, [
        address_postal, address_region, address_city, address_street, address_house
    ])) or None

    # Фиксируем адрес клиента и считаем цену до ближайшего ПВЗ СДЭК И Яндекса —
    # перевозчик НЕ выбирается автоматически, оба варианта сохраняются как есть,
    # КС сам согласовывает более выгодный на этапе одобрения (см. approve_request).
    # client_pvz_code/address/city + cdek_cost — кандидат СДЭК (существующие поля).
    # yandex_pvz_code/address/name + yandex_cost — кандидат Яндекса (новые поля).
    resolved_cdek_cost = None
    yandex_pvz_code    = None
    yandex_pvz_address = None
    yandex_pvz_name    = None
    yandex_cost        = None
    cdek_pvz_lat = cdek_pvz_lng = None
    yandex_pvz_lat = yandex_pvz_lng = None
    ozon_pvz_code = ozon_pvz_address = ozon_pvz_name = None
    ozon_cost = None
    ozon_pvz_lat = ozon_pvz_lng = None
    ozon_from_pvz_id = ozon_from_pvz_address = None
    ozon_client_ok = None
    ozon_reason = None
    ozon_shipment_method_id = None
    city_for_resolver = address_city or client_pvz_city
    if city_for_resolver:
        try:
            from ..pickup_resolver import get_both_candidates
            candidates = get_both_candidates(
                city=city_for_resolver,
                address=shipping_address or city_for_resolver,
                lat=client_lat, lon=client_lon,
                phone=customer_phone or "",
            )
            cdek_c = candidates.get("cdek")
            if cdek_c:
                client_pvz_code     = cdek_c["pvz_code"]
                client_pvz_address  = cdek_c["pvz_address"]
                client_pvz_city     = city_for_resolver
                resolved_cdek_cost  = cdek_c.get("cost")
                cdek_pvz_lat        = cdek_c.get("lat")
                cdek_pvz_lng        = cdek_c.get("lng")
            yd_c = candidates.get("yandex")
            if yd_c:
                yandex_pvz_code    = yd_c["pvz_code"]
                yandex_pvz_address = yd_c["pvz_address"]
                yandex_pvz_name    = yd_c.get("pvz_name")
                yandex_cost        = yd_c.get("cost")
                yandex_pvz_lat     = yd_c.get("lat")
                yandex_pvz_lng     = yd_c.get("lng")
            oz_c = candidates.get("ozon")
            if oz_c and oz_c.get("available"):
                ozon_pvz_code = oz_c["pvz_code"]
                ozon_pvz_address = oz_c.get("pvz_address")
                ozon_pvz_name = oz_c.get("pvz_name")
                ozon_cost = oz_c.get("cost")
                ozon_pvz_lat = oz_c.get("lat")
                ozon_pvz_lng = oz_c.get("lng")
                ozon_from_pvz_id = oz_c.get("from_pvz_id")
                ozon_from_pvz_address = oz_c.get("from_pvz_address")
                ozon_client_ok = True
                ozon_shipment_method_id = oz_c.get("shipment_method_id")
            elif oz_c:
                ozon_client_ok = bool(oz_c.get("client_ok"))
                ozon_reason = oz_c.get("reason") or ""
            if not cdek_c and not yd_c and not (oz_c and oz_c.get("available")):
                logger.warning("pickup_resolver: ни один перевозчик не подобрал точку для city=%s", city_for_resolver)
        except Exception as e:
            logger.warning("pickup_resolver failed at submit: %s", e)

    # Salebot client ID — чистим значение из формы
    clean_salebot_id = (salebot_client_id or "").strip() or None

    # Если форма пришла из чата (WB или ЯМ) — достаём метаданные
    wb_reply_sign  = None
    wb_nm_id       = None
    wb_client_id   = None
    wb_chat_id_val = None
    ym_chat_id_val = None
    ozon_chat_id_val = None
    marketplace    = "wb"
    # Чат уже мог быть взят из токена выше — до claim used_at. Повторный
    # поиск с used_at IS NULL после занятия ссылки как раз обнулял заказ.
    clean_chat_id  = (wb_chat_id or "").strip() or _chat_clean or None
    clean_token    = (chat_token or "").strip() or None
    token_mp       = _token_mp
    category_val   = (product_category or "").strip() or None
    order_num_val  = (order_number or "").strip() or None
    supplier_article_val = None

    if clean_token:
        from .. import chat_bot as _chat_bot
        sent_cid = _chat_bot.chat_id_for_form_token(db, clean_token)
        if sent_cid:
            clean_chat_id = sent_cid
    if not clean_chat_id and clean_token:
        token_row = db.execute(text("""
            SELECT chat_id, mp FROM reshipment_chat_tokens
            WHERE token = :token
        """), {"token": clean_token}).mappings().first()
        if token_row and token_row["chat_id"]:
            clean_chat_id = token_row["chat_id"]
            token_mp = token_mp or (token_row.get("mp") or "").strip().lower() or None

    if clean_chat_id:
        # Явный mp с токена или автодетект по таблицам чатов
        mp = token_mp
        if not mp:
            if db.execute(text(
                "SELECT 1 FROM ym_chats WHERE chat_id = :cid"
            ), {"cid": clean_chat_id}).first():
                mp = "ym"
            elif db.execute(text(
                "SELECT 1 FROM ozon_chats WHERE chat_id = :cid"
            ), {"cid": clean_chat_id}).first():
                mp = "ozon"
            elif db.execute(text(
                "SELECT 1 FROM wb_chats WHERE chat_id = :cid"
            ), {"cid": clean_chat_id}).first():
                mp = "wb"
            elif db.execute(text(
                "SELECT 1 FROM wb_chat_pending WHERE chat_id = :cid"
            ), {"cid": clean_chat_id}).first():
                mp = "wb"

        if mp == "ym":
            marketplace = "ym"
            ym_chat_id_val = clean_chat_id
            ym_row = db.execute(text("""
                SELECT order_id, context_article, context_type
                FROM ym_chats WHERE chat_id = :cid
            """), {"cid": clean_chat_id}).mappings().first()
            if ym_row:
                ctx = (ym_row.get("context_type") or "").strip().upper()
                # DIRECT — вопрос по карточке, не чат заказа. Артикул/номер
                # оттуда в заявку не копируем, иначе привяжется чужой товар.
                if ctx in ("ORDER", "RETURN"):
                    if not order_num_val and ym_row.get("order_id"):
                        order_num_val = ym_row["order_id"]
                    art = (ym_row.get("context_article") or "").strip() or None
                    if art:
                        supplier_article_val = art
                        if not category_val:
                            category_val = art
        elif mp == "ozon":
            marketplace = "ozon"
            ozon_chat_id_val = clean_chat_id
            ozon_row = db.execute(text("""
                SELECT order_number, context_article, context_sku
                FROM ozon_chats WHERE chat_id = :cid
            """), {"cid": clean_chat_id}).mappings().first()
            if ozon_row:
                art = (ozon_row.get("context_article") or "").strip() or None
                if art:
                    supplier_article_val = art
                    if not category_val:
                        category_val = art
                if not order_num_val and ozon_row.get("order_number"):
                    order_num_val = ozon_row["order_number"]
        else:
            marketplace = "wb"
            wb_chat_id_val = clean_chat_id
            pending = db.execute(text("""
                SELECT reply_sign, nm_id, client_id FROM wb_chat_pending WHERE chat_id = :cid
            """), {"cid": clean_chat_id}).mappings().first()
            if pending:
                wb_reply_sign = pending["reply_sign"]
                wb_nm_id      = pending["nm_id"]
                wb_client_id  = pending["client_id"]
            if not wb_reply_sign:
                chat_meta = db.execute(text("""
                    SELECT reply_sign, nm_id FROM wb_chats WHERE chat_id = :cid
                """), {"cid": clean_chat_id}).mappings().first()
                if chat_meta:
                    wb_reply_sign = chat_meta.get("reply_sign") or wb_reply_sign
                    wb_nm_id = wb_nm_id or chat_meta.get("nm_id")
            if wb_nm_id and not supplier_article_val:
                from ..chat_bot import _get_article
                art = (_get_article(db, wb_nm_id) or "").strip()
                if art:
                    supplier_article_val = art

    try:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                   {"k": f"resh|{_phone_clean}|{_chat_clean or ''}"})
        _again = _find_open_reshipment(db, _phone_clean, _chat_clean)
        if _again:
            raise HTTPException(
                status_code=409,
                detail=f"Заявка #{_again['id']} по этому клиенту уже обрабатывается. "
                       "Дублирование запрещено.",
            )
        result = db.execute(text("""
            INSERT INTO reshipment_requests
                (customer_name, customer_email, customer_phone, order_number, marketplace,
                 product_category, problem_type, items_to_send, supplier_article,
                 shipping_address, address_postal, address_region,
                 address_city, address_street, address_house,
                 client_pvz_code, client_pvz_address, client_pvz_city, cdek_cost,
                 yandex_pvz_code, yandex_pvz_address, yandex_pvz_name, yandex_cost,
                 client_lat, client_lon, cdek_pvz_lat, cdek_pvz_lng, yandex_pvz_lat, yandex_pvz_lng,
                 ozon_pvz_code, ozon_pvz_address, ozon_pvz_name, ozon_cost,
                 ozon_pvz_lat, ozon_pvz_lng, ozon_from_pvz_id, ozon_from_pvz_address,
                 ozon_client_ok, ozon_reason, ozon_shipment_method_id,
                 photo_files, privacy_consent,
                 wb_chat_id, wb_reply_sign, wb_nm_id, wb_client_id, ym_chat_id, ozon_chat_id,
                 salebot_client_id, chat_token)
            VALUES
                (:name, :email, :phone, :order_num, :marketplace,
                 :category, :problem_type, :items, :supplier_article,
                 :address, :postal, :region,
                 :city, :street, :house,
                 :pvz_code, :pvz_address, :pvz_city, :cdek_cost,
                 :yandex_pvz_code, :yandex_pvz_address, :yandex_pvz_name, :yandex_cost,
                 :client_lat, :client_lon, :cdek_pvz_lat, :cdek_pvz_lng, :yandex_pvz_lat, :yandex_pvz_lng,
                 :ozon_pvz_code, :ozon_pvz_address, :ozon_pvz_name, :ozon_cost,
                 :ozon_pvz_lat, :ozon_pvz_lng, :ozon_from_pvz_id, :ozon_from_pvz_address,
                 :ozon_client_ok, :ozon_reason, :ozon_shipment_method_id,
                 :photos, :consent,
                 :wb_chat_id, :wb_reply_sign, :wb_nm_id, :wb_client_id, :ym_chat_id, :ozon_chat_id,
                 :salebot_client_id, :chat_token)
            RETURNING id
        """), {
            "name":          customer_name.strip(),
            "email":         customer_email,
            "phone":         customer_phone.strip(),
            "order_num":     order_num_val,
            "marketplace":   marketplace,
            "category":      category_val,
            "problem_type":  problem_type,
            "items":         items_to_send.strip(),
            "supplier_article": supplier_article_val,
            "address":       shipping_address,
            "postal":        address_postal,
            "region":        address_region,
            "city":          address_city or client_pvz_city,
            "street":        address_street,
            "house":         address_house,
            "pvz_code":      client_pvz_code,
            "pvz_address":   client_pvz_address,
            "pvz_city":      client_pvz_city,
            "cdek_cost":     resolved_cdek_cost,
            "yandex_pvz_code":    yandex_pvz_code,
            "yandex_pvz_address": yandex_pvz_address,
            "yandex_pvz_name":    yandex_pvz_name,
            "yandex_cost":        yandex_cost,
            "client_lat":         client_lat,
            "client_lon":         client_lon,
            "cdek_pvz_lat":       cdek_pvz_lat,
            "cdek_pvz_lng":       cdek_pvz_lng,
            "yandex_pvz_lat":     yandex_pvz_lat,
            "yandex_pvz_lng":     yandex_pvz_lng,
            "ozon_pvz_code":      ozon_pvz_code,
            "ozon_pvz_address":   ozon_pvz_address,
            "ozon_pvz_name":      ozon_pvz_name,
            "ozon_cost":          ozon_cost,
            "ozon_pvz_lat":       ozon_pvz_lat,
            "ozon_pvz_lng":       ozon_pvz_lng,
            "ozon_from_pvz_id":   ozon_from_pvz_id,
            "ozon_from_pvz_address": ozon_from_pvz_address,
            "ozon_client_ok":     ozon_client_ok,
            "ozon_reason":        ozon_reason,
            "ozon_shipment_method_id": ozon_shipment_method_id,
            "photos":        photo_files,
            "consent":       personal_data_consent,
            "wb_chat_id":        wb_chat_id_val,
            "wb_reply_sign":     wb_reply_sign,
            "wb_nm_id":          wb_nm_id,
            "wb_client_id":      wb_client_id,
            "ym_chat_id":        ym_chat_id_val,
            "ozon_chat_id":      ozon_chat_id_val,
            "salebot_client_id": clean_salebot_id,
            "chat_token":        clean_token,
        })
        db.commit()
        new_id = result.scalar()

        # Обновляем статус pending записи (только WB)
        if wb_chat_id_val:
            db.execute(text("""
                UPDATE wb_chat_pending
                SET status = 'form_submitted', request_id = :req_id, updated_at = NOW()
                WHERE chat_id = :cid
            """), {"req_id": new_id, "cid": wb_chat_id_val})
            db.commit()

        # Помечаем токен как использованный атомарно (второй клиент с той же ссылкой не пройдёт)
        if clean_token:
            db.execute(text("""
                UPDATE reshipment_chat_tokens
                SET request_id = :req_id,
                    mp = COALESCE(mp, :mp),
                    chat_id = COALESCE(NULLIF(chat_id, ''), :cid)
                WHERE token = :token
            """), {
                "req_id": new_id,
                "token": clean_token,
                "mp": marketplace if (wb_chat_id_val or ym_chat_id_val or ozon_chat_id_val) else None,
                "cid": wb_chat_id_val or ym_chat_id_val or ozon_chat_id_val,
            })
            db.commit()

        return {"status": "success", "id": new_id, "message": "Заявка принята. Мы свяжемся с вами."}
    except HTTPException:
        db.rollback()
        if token_claimed:
            try:
                release_submit_token(db, _token_clean)
                db.commit()
            except Exception:
                pass
        raise
    except Exception as e:
        db.rollback()
        if token_claimed:
            try:
                release_submit_token(db, _token_clean)
                db.commit()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Ошибка при сохранении: {e}")


@public_router.get("/confirm/{token}")
def confirm_delivery(token: str, db: Session = Depends(get_db)):
    """Клиент подтверждает получение по ссылке с токеном"""
    row = db.execute(text("""
        SELECT id, status FROM reshipment_requests WHERE confirmation_token = :token
    """), {"token": token}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Ссылка недействительна")
    if row["status"] == "delivered":
        return {"status": "already_confirmed", "message": "Вы уже подтвердили получение. Спасибо!"}
    if row["status"] != "shipped":
        raise HTTPException(status_code=400, detail="Заявка ещё не отправлена")

    db.execute(text("""
        UPDATE reshipment_requests SET status = 'delivered', confirmed_at = NOW()
        WHERE confirmation_token = :token
    """), {"token": token})
    db.commit()
    return {"status": "success", "message": "Получение подтверждено. Спасибо!"}


@public_router.post("/link-salebot")
async def link_salebot(request: Request, db: Session = Depends(get_db)):
    """Salebot вызывает этот webhook когда клиент нажимает /start box{ID} в боте."""
    import logging
    logger = logging.getLogger(__name__)

    payload = {}
    try:
        payload = await request.json()
    except Exception:
        try:
            form = await request.form()
            payload = dict(form)
        except Exception:
            pass

    logger.warning("link-salebot payload: %s", payload)

    client_id   = str(payload.get("telegram_id") or payload.get("client_id", "")).strip()
    start_param = str(payload.get("start_param", "")).strip()

    if not client_id or not start_param.startswith("box"):
        return {"ok": True}

    try:
        req_id = int(start_param[3:])
    except ValueError:
        return {"ok": True}

    # Проверяем: уже привязан?
    existing = db.execute(text("""
        SELECT salebot_client_id FROM reshipment_requests WHERE id = :id
    """), {"id": req_id}).mappings().first()

    if not existing:
        return {"ok": True}

    if existing["salebot_client_id"]:
        logger.warning("link-salebot: telegram_id=%s уже привязан к заявке #%s — повтор проигнорирован", client_id, req_id)
        return {"ok": True}

    from .. import salebot as _sb
    messenger = _sb.get_client_messenger(client_id)

    db.execute(text("""
        UPDATE reshipment_requests
        SET salebot_client_id = :cid, salebot_messenger = :messenger, salebot_blocked = FALSE
        WHERE id = :id AND salebot_client_id IS NULL
    """), {"cid": client_id, "messenger": messenger, "id": req_id})
    db.commit()
    logger.warning("link-salebot: привязан telegram_id=%s (messenger=%s) к заявке #%s", client_id, messenger, req_id)
    return {"ok": True}


@public_router.post("/cdek-webhook")
def cdek_webhook(payload: dict, db: Session = Depends(get_db)):
    """Webhook от СДЭК: обновление статуса доставки."""
    import logging as _log
    _log.getLogger("cdek_webhook").warning("CDEK webhook payload: %s", payload)
    attributes = payload.get("attributes", {})
    cdek_number = attributes.get("cdek_number", "")
    status_code = attributes.get("status_code", "")
    _log.getLogger("cdek_webhook").warning("CDEK webhook: cdek_number=%r status_code=%r", cdek_number, status_code)

    if not cdek_number:
        return {"ok": True}

    try:
        from .. import shop_orders as shop_orders_mod
        shop_orders_mod.apply_cdek_webhook(
            db,
            cdek_number,
            status_code,
            uuid=str(attributes.get("uuid") or payload.get("uuid") or ""),
        )
    except Exception:
        _log.getLogger("cdek_webhook").warning("shop orders CDEK webhook failed", exc_info=True)

    # Маппинг CDEK кодов → наши статусы
    CDEK_MAP = {
        "RECEIVED_AT_SHIPMENT_WAREHOUSE":              ("shipped",          "approved,packed"),
        "READY_FOR_SHIPMENT_IN_SENDER_CITY":           ("in_transit",       "approved,packed,shipped"),
        "TAKEN_BY_TRANSPORTER_FROM_SENDER_CITY":       ("in_transit",       "approved,packed,shipped"),
        "SENT_TO_TRANSIT_CITY":                        ("in_transit",       "approved,packed,shipped"),
        "ACCEPTED_AT_TRANSIT_WAREHOUSE":               ("in_transit",       "approved,packed,shipped"),
        "READY_FOR_SHIPMENT_IN_TRANSIT_CITY":          ("in_transit",       "approved,packed,shipped"),
        "TAKEN_BY_TRANSPORTER_FROM_TRANSIT_CITY":      ("in_transit",       "approved,packed,shipped"),
        "SENT_TO_RECIPIENT_CITY":                      ("in_transit",       "approved,packed,shipped"),
        "ACCEPTED_IN_TRANSIT_CITY":                    ("in_transit",       "approved,packed,shipped"),
        "ACCEPTED_IN_RECIPIENT_CITY":                  ("in_transit",       "approved,packed,shipped"),
        "ACCEPTED_AT_PICK_UP_POINT":                   ("ready_for_pickup", "approved,packed,shipped,in_transit"),
        "READY_FOR_PICKUP":                            ("ready_for_pickup", "approved,packed,shipped,in_transit"),
        "DELIVERED":                                   ("delivered",        "shipped,in_transit,ready_for_pickup"),
        "NOT_DELIVERED":                               ("returned",         "shipped,in_transit,ready_for_pickup"),
    }

    mapping = CDEK_MAP.get(status_code)
    if mapping:
        new_status, from_statuses = mapping
        from_list = tuple(from_statuses.split(","))

        row = db.execute(text(f"""
            UPDATE reshipment_requests
            SET status = :new_status,
                shipped_at = CASE WHEN :new_status = 'shipped' THEN COALESCE(shipped_at, NOW()) ELSE shipped_at END,
                confirmed_at = CASE WHEN :new_status IN ('delivered','returned') THEN COALESCE(confirmed_at, NOW()) ELSE confirmed_at END,
                confirmation_token = COALESCE(confirmation_token, :token)
            WHERE cdek_number = :num AND status = ANY(:from_list)
            RETURNING id, salebot_client_id, wb_reply_sign, ym_chat_id, ozon_chat_id, marketplace, client_pvz_address, wb_chat_id
        """), {"new_status": new_status, "num": cdek_number,
               "token": secrets.token_hex(32), "from_list": list(from_list)}).mappings().first()
        db.commit()

        if row:
            from .. import reshipment_notify
            reshipment_notify.notify_delivery_status(
                db, dict(row), new_status,
                delivery_method="cdek",
                track_number=cdek_number,
                pvz_address=row.get("client_pvz_address") or "",
            )

    return {"ok": True}


# ── Генерация токена для привязки WB чата ────────────────────────────────────

@router.post("/generate-token")
def generate_chat_token(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Генерирует одноразовый токен (живёт 7 дней).
    Воркер привязывает chatID когда менеджер отправит ссылку в чат WB или ЯМ."""
    token = secrets.token_urlsafe(12)
    db.execute(text("""
        INSERT INTO reshipment_chat_tokens (token, created_by, expires_at)
        VALUES (:token, :user, NOW() + INTERVAL '7 days')
    """), {"token": token, "user": current_user.get("username", "")})
    # Чистим: помечаем просроченные (для заглушки на форме)
    db.execute(text("""
        UPDATE reshipment_chat_tokens
        SET expired = TRUE
        WHERE used_at IS NULL AND expires_at < NOW() AND expired IS NOT TRUE
    """))
    # Физически удаляем совсем старые (30+ дней)
    db.execute(text("""
        DELETE FROM reshipment_chat_tokens
        WHERE created_at < NOW() - INTERVAL '30 days' AND used_at IS NULL
    """))
    db.commit()
    return {"token": token, "url": f"https://box.vidovito.com/reshipment/form?t={token}"}


@public_router.get("/check-token/{token}")
def check_token(token: str, db: Session = Depends(get_db)):
    """Форма клиента проверяет токен при загрузке."""
    row = db.execute(text("""
        SELECT used_at, expired, expires_at, request_id FROM reshipment_chat_tokens WHERE token = :token
    """), {"token": token}).mappings().first()
    if not row:
        return {"valid": False, "reason": "not_found"}
    if row["used_at"] or row["request_id"]:
        return {"valid": False, "reason": "used"}
    if row["expired"] or (row["expires_at"] and row["expires_at"] < __import__("datetime").datetime.utcnow()):
        return {"valid": False, "reason": "expired"}
    from ..chat_bot import form_token_block_reason
    blocked = form_token_block_reason(db, token)
    if blocked:
        return {"valid": False, "reason": blocked}
    return {"valid": True}


# ── Внутренние эндпоинты ──────────────────────────────────────────────────────

@router.get("/requests")
def get_requests(
    status: Optional[str] = Query(None),
    carrier: Optional[str] = Query(None, description="'cdek' | 'yandex' — фильтр по перевозчику"),
    db: Session = Depends(get_db),
):
    where = "WHERE 1=1"
    params: dict = {}
    if status:
        where += " AND r.status = :status"
        params["status"] = status
    if carrier:
        where += " AND r.delivery_method = :carrier"
        params["carrier"] = carrier

    rows = db.execute(text(f"""
        SELECT r.id, r.created_at, r.updated_at, r.customer_name, r.customer_email, r.customer_phone,
               r.order_number, r.product_category, r.problem_type, r.items_to_send,
               r.supplier_article,
               r.shipping_address, r.address_postal, r.address_region,
               r.address_city, r.address_street, r.address_house,
               r.client_pvz_code, r.client_pvz_address, r.client_pvz_city,
               r.photo_files, r.status,
               r.matched_srid, r.match_notes, r.moderator_comment, r.processed_by, r.processed_at,
               r.rejected_by, r.rejection_reason,
               r.track_number, r.shipping_cost, r.cdek_uuid, r.cdek_number, r.cdek_cost,
               r.cdek_cost_actual,
               r.yandex_pvz_code, r.yandex_pvz_address, r.yandex_cost,
               r.yandex_sharing_url,
               r.ozon_pvz_code, r.ozon_pvz_address, r.ozon_cost, r.ozon_cost_actual,
               r.ozon_client_ok, r.ozon_reason,
               r.ozon_from_pvz_address, r.ozon_order_number, r.ozon_posting_number,
               r.shipped_at, r.confirmed_at, r.review_requested,
               r.review_step, r.review_msg1_at, r.review_msg2_at, r.review_msg3_at,
               r.wb_chat_id, r.wb_nm_id, r.wb_client_id, r.wb_reply_sign, r.delivery_method,
               r.ym_chat_id, r.ozon_chat_id, r.marketplace,
               y.cabinet_chat_id AS ym_cabinet_chat_id,
               y.context_type AS ym_context_type,
               COALESCE(NULLIF(TRIM(r.supplier_article), ''), y.context_article, oc.context_article) AS ym_context_article,
               y.order_id AS ym_order_id,
               oc.order_number AS ozon_chat_order_number,
               r.salebot_client_id, r.salebot_messenger, r.salebot_blocked,
               r.promo_code, r.promo_sent_at, r.chat_token, r.confirmation_token,
               -- Отзыв ДО отправки (зафиксирован при одобрении; on_approve — устаревший тип для старых заявок)
               (SELECT s.valuation FROM wb_feedback_snapshots s
                WHERE s.request_id = r.id AND s.snapshot_type IN ('before', 'on_approve')
                ORDER BY s.captured_at ASC LIMIT 1) AS wb_before_rating,
               -- Отзыв ПОСЛЕ получения (зафиксирован при доставке)
               (SELECT s.valuation FROM wb_feedback_snapshots s
                WHERE s.request_id = r.id AND s.snapshot_type = 'after'
                ORDER BY s.captured_at DESC LIMIT 1) AS wb_after_rating
        FROM reshipment_requests r
        LEFT JOIN ym_chats y ON y.chat_id = r.ym_chat_id
        LEFT JOIN ozon_chats oc ON oc.chat_id = r.ozon_chat_id
        {where}
        ORDER BY r.updated_at DESC
    """), params).mappings().all()

    try:
        from .. import yandex_ndd
        default_src = yandex_ndd.source_station_label()
    except Exception:
        default_src = "Казань, Тульская 40"
    return {
        "status": "success",
        "count": len(rows),
        "yandex_default_source_pvz": default_src,
        "data": [dict(r) for r in rows],
    }


@router.get("/requests/{req_id}")
def get_request(req_id: int, db: Session = Depends(get_db)):
    row = db.execute(text("SELECT * FROM reshipment_requests WHERE id = :id"), {"id": req_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    data = dict(row)
    # Точка сдачи Яндекса: из реального заказа, иначе текущий дефолт .env
    try:
        from .. import yandex_ndd
        data["yandex_default_source_pvz"] = yandex_ndd.source_station_label()
        track = (data.get("track_number") or "").strip()
        if (data.get("delivery_method") or "") == "yandex" and track:
            real = yandex_ndd.get_order_source_label(track)
            data["yandex_source_pvz"] = real or data["yandex_default_source_pvz"]
            sharing = (data.get("yandex_sharing_url") or "").strip()
            if not sharing:
                info = yandex_ndd.get_order_status(track)
                sharing = (info.get("sharing_url") or "").strip()
                if sharing:
                    data["yandex_sharing_url"] = sharing
                    db.execute(text("""
                        UPDATE reshipment_requests
                        SET yandex_sharing_url = :url
                        WHERE id = :id AND (yandex_sharing_url IS NULL OR yandex_sharing_url = '')
                    """), {"url": sharing, "id": req_id})
                    db.commit()
        else:
            data["yandex_source_pvz"] = data["yandex_default_source_pvz"]
    except Exception:
        data.setdefault("yandex_source_pvz", "Казань, Тульская 40")
        data.setdefault("yandex_default_source_pvz", data["yandex_source_pvz"])
    return {"status": "success", "data": data}


@router.get("/requests/{req_id}/tariff-options")
def get_tariff_options(req_id: int, db: Session = Depends(get_db)):
    """2 самых дешёвых тарифа СДЭК склад-склад для этой заявки."""
    row = db.execute(text("""
        SELECT client_pvz_code, address_city, client_pvz_city, delivery_method
        FROM reshipment_requests WHERE id = :id
    """), {"id": req_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    if row["delivery_method"] == "yandex":
        # client_pvz_code тут — id точки Яндекса, не код ПВЗ СДЭК; тарифы СДЭК не применимы
        return {"status": "not_applicable", "options": []}

    pvz_code = row["client_pvz_code"]
    if not pvz_code:
        city = row["address_city"] or row["client_pvz_city"]
        if city:
            try:
                from ..cdek import find_nearest_pvz
                pvz_code = find_nearest_pvz(city)
            except Exception:
                pass

    if not pvz_code:
        return {"status": "no_pvz", "options": []}

    try:
        from ..cdek import get_tariff_options as cdek_tariff_options
        options = cdek_tariff_options(pvz_code)
        return {"status": "success", "options": options}
    except Exception as e:
        return {"status": "error", "options": [], "message": str(e)}


@router.get("/requests/{req_id}/status-history")
def get_status_history(req_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """История изменений статуса заявки (МСК = UTC+3)."""
    rows = db.execute(text("""
        SELECT old_status, new_status, changed_by,
               changed_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Moscow' AS changed_at_msk
        FROM reshipment_status_log
        WHERE request_id = :id
        ORDER BY changed_at ASC
    """), {"id": req_id}).mappings().fetchall()
    return [
        {
            "old_status": r["old_status"],
            "new_status": r["new_status"],
            "changed_by": r["changed_by"],
            "changed_at": r["changed_at_msk"].isoformat() if r["changed_at_msk"] else None,
        }
        for r in rows
    ]


@router.get("/requests/{req_id}/economics")
def get_request_economics(
    req_id: int,
    carrier: Optional[str] = Query(None, description="'cdek' | 'yandex' — чью стоимость доставки использовать в варианте V3. По умолчанию — уже одобренный перевозчик, иначе первый доступный."),
    db: Session = Depends(get_db),
):
    """Экономика отправки: данные из финотчёта WB для расчёта 3 вариантов работы с возвратом.
    Стоимость доставки в варианте V3 берётся по выбранному перевозчику (carrier) — до одобрения
    заявки КС может посмотреть экономику и для СДЭК, и для Яндекса, чтобы сравнить и согласовать."""
    req = db.execute(text("""
        SELECT matched_srid, cdek_cost, cdek_cost_actual, yandex_cost, shipping_cost,
               ozon_cost, ozon_cost_actual, delivery_method, wb_nm_id
        FROM reshipment_requests WHERE id = :id
    """), {"id": req_id}).mappings().first()
    if not req:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    # Какого перевозчика используем для стоимости доставки в V3
    chosen_carrier = carrier or req["delivery_method"]
    if chosen_carrier not in ("cdek", "yandex", "ozon"):
        if req["ozon_cost"] is not None:
            chosen_carrier = "ozon"
        elif req["cdek_cost"] is not None:
            chosen_carrier = "cdek"
        elif req["yandex_cost"] is not None:
            chosen_carrier = "yandex"
        else:
            chosen_carrier = "cdek"
    from ..reshipment_costs import delivery_amounts
    amounts = delivery_amounts(
        chosen_carrier,
        cdek_cost=req["cdek_cost"],
        cdek_cost_actual=req.get("cdek_cost_actual"),
        yandex_cost=req["yandex_cost"],
        shipping_cost=req["shipping_cost"],
        ozon_cost=req.get("ozon_cost"),
        ozon_cost_actual=req.get("ozon_cost_actual"),
    )
    delivery_cost = amounts["main"]

    matched_srid = req["matched_srid"]
    wb_nm_id     = req["wb_nm_id"]
    supplier_article = None  # определим ниже из финотчёта или wb_logistics

    result = {
        "matched_srid":        matched_srid,
        "carrier":             chosen_carrier,
        "delivery_cost":       delivery_cost,
        "delivery_cost_plan":  amounts["plan"],
        "delivery_cost_fact":  amounts["fact"],
        "delivery_cost_diff":  amounts["diff"],
        # из финотчёта (заполняется если есть matched_srid)
        "ppvz_for_pay":        None,   # выплата WB за продажу
        "acquiring_fee_sale":  None,   # эквайринг при продаже
        "retail_amount":        None,   # сумма продажи (что заплатил клиент)
        "acquiring_fee_return":None,   # эквайринг при возврате (доп. потеря)
        "acquiring_fee_return_estimated": False,  # True = расчётный 4%, не из отчёта
        "rebill_logistic":     None,   # штраф за логистику (из пустых строк)
        "penalty":             None,   # штраф (из пустых строк)
        "delivery_rub":        None,   # доставка из пустых строк (обычно 0)
        # себестоимость
        "cog":                 None,
        "cog_exact":           False,  # True = точная по поставке, False = последняя по артикулу
        # средняя стоимость обратной доставки WB→ПВЗ по артикулу
        "avg_return_delivery": None,
        "avg_return_delivery_n": 0,
        "avg_return_delivery_source": "fallback",
        # флаг: данные из финотчёта доступны
        "has_report_data":     False,
        "ppvz_estimated":      False,  # True = выплата с последней продажи этого артикула, заказ ещё не в отчёте
    }

    sa_name = None
    sale = ret = empty = None
    if matched_srid:
        # 1. Строка «Продажа» — берём ту, где есть выплата (у одного srid бывает нулевая строка)
        sale = db.execute(text("""
            SELECT ppvz_for_pay, acquiring_fee, retail_amount, sa_name
            FROM wb_report_detail
            WHERE LOWER(srid) = LOWER(:srid) AND doc_type_name = 'Продажа'
            ORDER BY (COALESCE(ppvz_for_pay, 0) > 0) DESC, rrd_id DESC
            LIMIT 1
        """), {"srid": matched_srid}).mappings().first()

        # 2. Строка «Возврат»
        ret = db.execute(text("""
            SELECT acquiring_fee, ppvz_sales_commission
            FROM wb_report_detail
            WHERE LOWER(srid) = LOWER(:srid) AND doc_type_name = 'Возврат'
            LIMIT 1
        """), {"srid": matched_srid}).mappings().first()

        # 3. Пустые строки (логистика и штрафы)
        empty = db.execute(text("""
            SELECT COALESCE(SUM(rebill_logistic_cost),0) AS rebill_logistic,
                   COALESCE(SUM(penalty),0)             AS penalty,
                   COALESCE(SUM(delivery_rub),0)        AS delivery_rub
            FROM wb_report_detail
            WHERE LOWER(srid) = LOWER(:srid) AND doc_type_name = ''
        """), {"srid": matched_srid}).mappings().first()

        sa_name = (sale["sa_name"] if sale and sale.get("sa_name") else None)

    # Заказ свежий — в еженедельном финотчёте его ещё нет. Берём последнюю
    # продажу этого же nm_id, чтобы экономика не была пустой.
    if (not sale or not sale.get("ppvz_for_pay")) and wb_nm_id:
        try:
            fb_sale = db.execute(text("""
                SELECT ppvz_for_pay, acquiring_fee, retail_amount, sa_name
                FROM wb_report_detail
                WHERE nm_id = :nm_id
                  AND doc_type_name = 'Продажа'
                  AND COALESCE(ppvz_for_pay, 0) > 0
                ORDER BY rr_dt DESC NULLS LAST, rrd_id DESC
                LIMIT 1
            """), {"nm_id": int(wb_nm_id)}).mappings().first()
        except (TypeError, ValueError):
            fb_sale = None
        except Exception as e:
            print(f"[economics] nm_id sale fallback #{req_id}: {e}")
            fb_sale = None
            try:
                db.rollback()
            except Exception:
                pass
        if fb_sale:
            sale = fb_sale
            result["ppvz_estimated"] = True
            if not sa_name:
                sa_name = fb_sale.get("sa_name")

    if not sa_name and matched_srid:
        sa_row = db.execute(text("""
            SELECT sa_name FROM wb_report_detail
            WHERE LOWER(srid) = LOWER(:srid) AND sa_name IS NOT NULL AND sa_name != ''
            LIMIT 1
        """), {"srid": matched_srid}).mappings().first()
        sa_name = sa_row["sa_name"] if sa_row else None

    if sale or ret or (empty and (float(empty["rebill_logistic"] or 0) or float(empty["penalty"] or 0) or float(empty["delivery_rub"] or 0))):
        result["has_report_data"]      = True
        result["ppvz_for_pay"]         = float(sale["ppvz_for_pay"] or 0) if sale else None
        result["acquiring_fee_sale"]   = float(sale["acquiring_fee"] or 0) if sale else None
        result["retail_amount"]        = float(sale["retail_amount"] or 0) if sale else None
        result["rebill_logistic"]      = float(empty["rebill_logistic"] or 0) if empty else None
        result["penalty"]              = float(empty["penalty"] or 0) if empty else None
        result["delivery_rub"]         = float(empty["delivery_rub"] or 0) if empty else None
        ret_acq = float(ret["acquiring_fee"] or 0) if ret else 0
        if ret_acq == 0 and result["retail_amount"]:
            result["acquiring_fee_return"]           = round(result["retail_amount"] * 0.04, 2)
            result["acquiring_fee_return_estimated"] = True
        else:
            result["acquiring_fee_return"]           = ret_acq
            result["acquiring_fee_return_estimated"] = False

    # Фолбэк: артикул через wb_nm_id → wb_logistics (nm_id в БД — text)
    if not sa_name and wb_nm_id:
        try:
            nm_row = db.execute(text("""
                SELECT TRIM(supplier_article) AS sa FROM wb_logistics
                WHERE nm_id = :nm_id AND supplier_article IS NOT NULL AND supplier_article != ''
                LIMIT 1
            """), {"nm_id": str(wb_nm_id)}).mappings().first()
            if nm_row:
                sa_name = nm_row["sa"]
        except Exception as e:
            print(f"[economics] nm_id lookup #{req_id}: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    # 5. Себестоимость: точный матч по поставке через wb_logistics → wb_invoices → wb_cogs
    if sa_name and matched_srid:
        exact_cog = db.execute(text("""
            SELECT wc.cost_value
            FROM wb_logistics l
            JOIN wb_invoices inv
                ON SPLIT_PART(l.income_id, '.', 1) = SPLIT_PART(inv.supply_id, '.', 1)
               AND LOWER(TRIM(inv.supplier_article)) = LOWER(TRIM(:sa))
            JOIN wb_cogs wc
                ON LOWER(TRIM(wc.supplier_article)) = LOWER(TRIM(:sa))
               AND TRIM(wc.invoice_num) = TRIM(inv.invoice_num)
               AND wc.cost_value > 0
            WHERE LOWER(TRIM(l.srid)) = LOWER(TRIM(:srid))
            LIMIT 1
        """), {"sa": sa_name, "srid": matched_srid}).mappings().first()
        if exact_cog:
            result["cog"]       = float(exact_cog["cost_value"])
            result["cog_exact"] = True
    if result["cog"] is None and sa_name:
        fallback_cog = db.execute(text("""
            SELECT cost_value FROM wb_cogs
            WHERE LOWER(TRIM(supplier_article)) = LOWER(TRIM(:sa)) AND cost_value > 0
            ORDER BY id DESC LIMIT 1
        """), {"sa": sa_name}).mappings().first()
        result["cog"] = float(fallback_cog["cost_value"]) if fallback_cog else None

    # 6. Обратная логистика: среднее по последним 10 возвратам артикула
    ret_info = fetch_avg_return_delivery(db, sa_name)
    result["avg_return_delivery"] = ret_info["value"]
    result["avg_return_delivery_n"] = ret_info["n"]
    result["avg_return_delivery_source"] = ret_info["source"]

    # 7. Рассчитываем итоги по 3 вариантам
    ppvz       = result["ppvz_for_pay"]
    acq_ret    = result["acquiring_fee_return"] or 0
    rebill     = result["rebill_logistic"] or 0
    penalty    = result["penalty"] or 0
    deliv      = result["delivery_rub"] or 0
    cog        = result["cog"] or 0
    ret_deliv  = result["avg_return_delivery"] or FALLBACK

    wb_return_costs = acq_ret + rebill + penalty + deliv  # WB-расходы при любом возврате

    # Вариант 1: возвращаем деньги, товар у клиента
    v1_loss = cog + wb_return_costs  # теряем СС + все WB-расходы

    # Вариант 2: возвращаем деньги, товар едет к нам на ПВЗ (СС тоже потеря — товар не продать)
    v2_loss = cog + wb_return_costs + ret_deliv

    # Вариант 3: отправляем деталь, продажа сохраняется
    if ppvz is not None:
        v3_profit  = ppvz - cog - delivery_cost
        # выгода vs V1: V3_net − V1_net = ppvz + acq + rebill + penalty + deliv − доставка (СС взаимно отменяется)
        v3_benefit = ppvz + wb_return_costs - delivery_cost
    else:
        v3_profit  = None
        v3_benefit = None

    result["variants"] = {
        "v1": {
            "label":      "Возврат денег · товар у клиента",
            "total_loss": round(v1_loss, 2),
            "breakdown": {
                "cog":         round(cog, 2),
                "acquiring":   round(acq_ret, 2),
                "rebill":      round(rebill, 2),
                "penalty":     round(penalty, 2),
                "delivery":    round(deliv, 2),
            }
        },
        "v2": {
            "label":       "Возврат денег · товар к нам на ПВЗ",
            "total_loss":  round(v2_loss, 2),
            "breakdown": {
                "cog":             round(cog, 2),
                "acquiring":       round(acq_ret, 2),
                "rebill":          round(rebill, 2),
                "penalty":         round(penalty, 2),
                "delivery":        round(deliv, 2),
                "return_delivery": round(ret_deliv, 2),
            }
        },
        "v3": {
            "label":       "Отправляем деталь · продажа сохранена",
            "net_profit":  round(v3_profit, 2) if v3_profit is not None else None,
            "benefit_vs_v1": round(v3_benefit, 2) if v3_benefit is not None else None,
            "breakdown": {
                "ppvz_for_pay": round(ppvz, 2) if ppvz is not None else None,
                "cog":          round(cog, 2),
                "delivery_cost": round(delivery_cost, 2),
            }
        }
    }

    return {"status": "success", "data": result}


@router.get("/requests/{req_id}/cost")
def get_shipping_cost(req_id: int, db: Session = Depends(get_db)):
    """
    Стоимость доставки по ОБОИМ перевозчикам (для показа КС при одобрении) —
    посчитаны один раз при подаче заявки (см. pickup_resolver), заново не считаем.
    Если заявка уже одобрена — delivery_method зафиксирован, отдаём его отдельным полем.
    """
    row = db.execute(text("""
        SELECT address_city, client_pvz_city, client_pvz_address, shipping_address,
               cdek_cost, yandex_cost, yandex_pvz_address, yandex_pvz_name,
               ozon_cost, ozon_pvz_address, ozon_pvz_name, ozon_client_ok, ozon_reason,
               ozon_from_pvz_address,
               delivery_method,
               client_lat, client_lon,
               cdek_pvz_lat, cdek_pvz_lng, yandex_pvz_lat, yandex_pvz_lng,
               ozon_pvz_lat, ozon_pvz_lng
        FROM reshipment_requests WHERE id = :id
    """), {"id": req_id}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    from ..pickup_resolver import distance_km

    client_lat = row["client_lat"]
    client_lon = row["client_lon"]
    cdek_available   = row["cdek_cost"] is not None
    yandex_available = row["yandex_cost"] is not None
    ozon_available   = row["ozon_cost"] is not None

    return {
        "status": "success",
        "delivery_method": row["delivery_method"],  # None пока не одобрена
        "client": {
            "lat":     float(client_lat) if client_lat is not None else None,
            "lon":     float(client_lon) if client_lon is not None else None,
            "address": row["shipping_address"] or row["address_city"] or row["client_pvz_city"],
        },
        "cdek": {
            "available":    cdek_available,
            "cost":         float(row["cdek_cost"]) if cdek_available else None,
            "pvz_address":  row["client_pvz_address"],
            "lat":          float(row["cdek_pvz_lat"]) if row["cdek_pvz_lat"] is not None else None,
            "lng":          float(row["cdek_pvz_lng"]) if row["cdek_pvz_lng"] is not None else None,
            "distance_km":  distance_km(client_lat, client_lon, row["cdek_pvz_lat"], row["cdek_pvz_lng"]),
        },
        "yandex": {
            "available":    yandex_available,
            "cost":         float(row["yandex_cost"]) if yandex_available else None,
            "pvz_address":  row["yandex_pvz_address"],
            "pvz_name":     row["yandex_pvz_name"],
            "lat":          float(row["yandex_pvz_lat"]) if row["yandex_pvz_lat"] is not None else None,
            "lng":          float(row["yandex_pvz_lng"]) if row["yandex_pvz_lng"] is not None else None,
            "distance_km":  distance_km(client_lat, client_lon, row["yandex_pvz_lat"], row["yandex_pvz_lng"]),
        },
        "ozon": {
            "available":    ozon_available,
            "cost":         float(row["ozon_cost"]) if ozon_available else None,
            "pvz_address":  row["ozon_pvz_address"],
            "pvz_name":     row["ozon_pvz_name"],
            "lat":          float(row["ozon_pvz_lat"]) if row["ozon_pvz_lat"] is not None else None,
            "lng":          float(row["ozon_pvz_lng"]) if row["ozon_pvz_lng"] is not None else None,
            "distance_km":  distance_km(client_lat, client_lon, row["ozon_pvz_lat"], row["ozon_pvz_lng"]),
            "client_ok":    row["ozon_client_ok"],
            "reason":       row["ozon_reason"] or "",
            "from_pvz_address": row["ozon_from_pvz_address"] or "",
            "recommended":  ozon_available,
        },
    }


@router.post("/requests/{req_id}/approve")
def approve_request(req_id: int, payload: ApproveRequest, db: Session = Depends(get_db)):
    row = _check_exists(req_id, db)
    if row["status"] not in ("new", "matched"):
        raise HTTPException(status_code=400, detail=f"Заявка уже в статусе '{row['status']}' — повторное одобрение невозможно")

    # КС выбирает перевозчика на этапе одобрения — оба варианта (ПВЗ + цена)
    # уже посчитаны при подаче заявки (см. pickup_resolver.get_both_candidates).
    has_cdek_candidate   = bool(row.get("client_pvz_code"))
    has_yandex_candidate = bool(row.get("yandex_pvz_code"))
    has_ozon_candidate   = bool(row.get("ozon_pvz_code") and row.get("ozon_cost") is not None)
    chosen_method = payload.delivery_method
    if not chosen_method:
        available = [m for m, ok in (("ozon", has_ozon_candidate), ("cdek", has_cdek_candidate), ("yandex", has_yandex_candidate)) if ok]
        if len(available) == 1:
            chosen_method = available[0]
        elif has_ozon_candidate:
            chosen_method = "ozon"
        else:
            raise HTTPException(status_code=400, detail="Укажите перевозчика (cdek, yandex или ozon)")
    if chosen_method not in ("cdek", "yandex", "ozon"):
        raise HTTPException(status_code=400, detail="delivery_method должен быть 'cdek', 'yandex' или 'ozon'")
    is_yandex = chosen_method == "yandex"
    is_ozon = chosen_method == "ozon"

    cdek_uuid    = row.get("cdek_uuid") or ""
    cdek_number  = row.get("cdek_number") or ""
    cost         = row.get("cdek_cost")
    pvz_code     = row.get("client_pvz_code") or ""
    cdek_tariff  = None
    cdek_error   = None
    yandex_track = row.get("track_number") or ""
    yandex_error = None
    # При выборе Яндекса переносим его кандидата в "рабочие" поля client_pvz_*,
    # которые читает весь остальной код (уведомления, панель склада и т.п.)
    yandex_pvz_code    = row.get("yandex_pvz_code") or ""
    yandex_pvz_address = row.get("yandex_pvz_address") or ""
    yandex_pvz_city    = row.get("client_pvz_city") or row.get("address_city")
    ozon_error = None
    ozon_order = row.get("ozon_order_number") or ""
    ozon_posting = row.get("ozon_posting_number") or ""

    if is_ozon:
        pvz_code = row.get("ozon_pvz_code") or ""
        cost = row.get("ozon_cost")
        if not ozon_order:
            try:
                from .. import ozon_delivery
                if not pvz_code:
                    raise RuntimeError("У заявки нет подобранного ПВЗ Ozon")
                oz_result = ozon_delivery.create_order(
                    req_id=req_id,
                    customer_name=row["customer_name"],
                    customer_phone=row["customer_phone"],
                    destination_pvz_id=pvz_code,
                    items_description=row.get("items_to_send", "Комплектующие"),
                    shipment_method_id=row.get("ozon_shipment_method_id"),
                )
                ozon_order = oz_result.get("order_number") or oz_result.get("posting_number") or ""
                ozon_posting = oz_result.get("posting_number") or ""
            except Exception as e:
                ozon_error = str(e)
                print(f"[Ozon Delivery] approve #{req_id}: {e}")

    if is_yandex:
        pvz_code = yandex_pvz_code
        cost = row.get("yandex_cost")
        if not yandex_track:
            try:
                from .. import yandex_ndd
                if not pvz_code:
                    raise RuntimeError("У заявки нет подобранного ПВЗ Яндекса")
                yd_result = yandex_ndd.create_order(
                    req_id=req_id,
                    customer_name=row["customer_name"],
                    customer_phone=row["customer_phone"],
                    destination_pvz_id=pvz_code,
                    items_description=row.get("items_to_send", "Комплектующие"),
                )
                yandex_track = yd_result["request_id"]
                if yd_result.get("price"):
                    cost = yd_result["price"]
            except Exception as e:
                yandex_error = str(e)
                print(f"[Yandex NDD] approve #{req_id}: {e}")

    if not is_yandex and not is_ozon and not cdek_uuid:  # Не создавать повторно если уже есть
        try:
            from ..cdek import create_order, calculate_cost_for_pvz, find_nearest_pvz
            city = row.get("address_city") or row.get("client_pvz_city")
            pvz_code = row.get("client_pvz_code") or ""
            if not pvz_code and city:
                pvz_code = find_nearest_pvz(city) or ""
            if pvz_code:
                cdek_result = create_order(
                    req_id=req_id,
                    customer_name=row["customer_name"],
                    customer_phone=row["customer_phone"],
                    to_pvz_code=pvz_code,
                    items_description=row.get("items_to_send", "Комплектующие"),
                    tariff_code=payload.tariff_code,
                    customer_email=row.get("customer_email") or None,
                )
                cdek_uuid   = cdek_result["uuid"]
                cdek_number = cdek_result.get("cdek_number") or ""
                cdek_tariff = cdek_result.get("tariff_code")
                # Считаем по ПВЗ (Орёл), не по названию села клиента (Звягинки → ложные ~1000₽)
                try:
                    cost = calculate_cost_for_pvz(pvz_code, tariff_code=cdek_tariff) or cost
                except Exception:
                    pass
            else:
                cdek_error = "ПВЗ не найден — заказ СДЭК не создан"
        except Exception as e:
            cdek_error = str(e)
            print(f"[CDEK] approve #{req_id}: {e}")

    db.execute(text("""
        UPDATE reshipment_requests SET
            status = 'approved', matched_srid = :srid, match_notes = :notes,
            moderator_comment = :comment, processed_by = :by, processed_at = NOW(),
            delivery_method = :delivery_method,
            cdek_cost = COALESCE(:cdek_cost, cdek_cost),
            shipping_cost = COALESCE(:yandex_result_cost, shipping_cost),
            ozon_order_number = COALESCE(NULLIF(:ozon_order,''), ozon_order_number),
            ozon_posting_number = COALESCE(NULLIF(:ozon_posting,''), ozon_posting_number),
            cdek_uuid = COALESCE(NULLIF(:uuid,''), cdek_uuid),
            cdek_number = COALESCE(NULLIF(:cdek_num,''), cdek_number),
            cdek_tariff_code = COALESCE(:tariff, cdek_tariff_code),
            client_pvz_code    = CASE
                WHEN :is_yandex THEN NULLIF(:yandex_pvz,'')
                WHEN :is_ozon THEN NULLIF(:ozon_pvz,'')
                ELSE COALESCE(NULLIF(:pvz,''), client_pvz_code)
            END,
            client_pvz_address = CASE
                WHEN :is_yandex THEN NULLIF(:yandex_addr,'')
                WHEN :is_ozon THEN NULLIF(:ozon_addr,'')
                ELSE client_pvz_address
            END,
            track_number = COALESCE(NULLIF(:track,''), track_number)
        WHERE id = :id
    """), {
        "delivery_method": chosen_method,
        "is_yandex": is_yandex,
        "is_ozon": is_ozon,
        "yandex_pvz":  yandex_pvz_code,
        "yandex_addr": yandex_pvz_address,
        "ozon_pvz": row.get("ozon_pvz_code") or "",
        "ozon_addr": row.get("ozon_pvz_address") or "",
        "ozon_order": ozon_order,
        "ozon_posting": ozon_posting,
        "yandex_result_cost": cost if (is_yandex or is_ozon) else None,
        "srid":     payload.matched_srid,
        "notes":    payload.match_notes,
        "comment":  payload.moderator_comment,
        "by":       payload.processed_by,
        "cdek_cost":   cost if (not is_yandex and not is_ozon) else None,
        "uuid":     cdek_uuid,
        "cdek_num": cdek_number or None,
        "tariff":   cdek_tariff,
        "pvz":      pvz_code,
        "track":    ozon_order if is_ozon else yandex_track,
        "id":       req_id,
    })
    db.commit()

    # 2. Автоотклонение рекламации WB — по заказу или чату, не только если srid вручную
    try:
        from ..wb_claims_api import reject_return_for_reshipment
        _comment = (
            "Ваша претензия закрыта — недостающая деталь направлена вам отдельным отправлением. "
            "Ожидайте посылку в ближайшее время."
        )
        _resp = reject_return_for_reshipment(
            db,
            srid=payload.matched_srid or row.get("matched_srid"),
            wb_chat_id=row.get("wb_chat_id"),
            order_number=row.get("order_number"),
            comment=_comment,
        )
        print(f"[WB Claims] auto-reject on approve #{req_id}: {_resp}")
    except Exception as _e:
        print(f"[WB Claims] Ошибка при отклонении рекламации для #{req_id}: {_e}")

    # 2b. ЯМ: при одобрении доотправки отказываем в возврате по заказу/чату
    mp = (row.get("marketplace") or "").lower()
    if mp == "ym" or row.get("ym_chat_id"):
        try:
            from .. import ym_returns_api
            ym_result = ym_returns_api.decline_return_for_reshipment(
                db,
                order_id=row.get("order_number"),
                ym_chat_id=row.get("ym_chat_id"),
            )
            print(f"[YM Returns] decline on reshipment #{req_id}: {ym_result}")
        except Exception as _e:
            print(f"[YM Returns] auto-decline on approve #{req_id}: {_e}")

    if mp == "ozon" or row.get("ozon_chat_id"):
        try:
            from .. import ozon_returns_api
            oz_result = ozon_returns_api.decline_return_for_reshipment(
                db,
                order_id=row.get("order_number"),
                ozon_chat_id=row.get("ozon_chat_id"),
            )
            print(f"[Ozon Returns] decline on reshipment #{req_id}: {oz_result}")
        except Exception as _e:
            print(f"[Ozon Returns] auto-decline on approve #{req_id}: {_e}")

    # 3. Снэпшот отзыва на момент одобрения (чтобы потом видеть "было/стало")
    try:
        db.execute(text("""
            INSERT INTO wb_feedback_snapshots (feedback_id, valuation, text, photos, request_id, snapshot_type)
            SELECT f.id, f.valuation, f.text, f.photos, :req_id, 'before'
            FROM wb_feedbacks f
            JOIN wb_orders o ON (
                o.shk_id = f.last_order_shk_id
                OR (o.dt = f.last_order_created_at + INTERVAL '3 hours'
                    AND o.supplier_article = f.supplier_article)
            )
            WHERE o.srid = (SELECT matched_srid FROM reshipment_requests WHERE id = :req_id AND matched_srid IS NOT NULL)
            ON CONFLICT DO NOTHING
        """), {"req_id": req_id})
        db.commit()
    except Exception:
        pass

    # 3. Уведомить клиента через Salebot
    from ..salebot import notify_approved, is_enabled, format_approved_message
    salebot_notified = False
    salebot_id = row.get("salebot_client_id")
    if salebot_id:
        try:
            if is_enabled():
                salebot_notified = notify_approved(
                    salebot_id, req_id, delivery_method=chosen_method,
                )
        except Exception:
            pass

    # 4. Уведомить в WB чат только если не удалось через Salebot
    if not salebot_notified:
        _wb_notify(row, format_approved_message(chosen_method))

    if chosen_method == "ozon":
        try:
            from .. import reshipment_notify
            guide_row = dict(row)
            guide_row["delivery_method"] = "ozon"
            reshipment_notify.send_ozon_pickup_guide(guide_row, db=db)
        except Exception as e:
            print(f"[approve] ozon pickup guide #{req_id}: {e}")

    result = {
        "status":           "success",
        "message":          f"Заявка #{req_id} одобрена",
        "cdek_uuid":        cdek_uuid,
        "cdek_number":      cdek_number,
        "track_number":     (ozon_order if is_ozon else yandex_track) or None,
        "salebot_notified": salebot_notified,
    }
    if cost is not None:
        result["cdek_cost"] = cost
    if cdek_error:
        result["cdek_warning"] = f"Заказ СДЭК не создан: {cdek_error}"
    if yandex_error:
        result["cdek_warning"] = f"Заказ Яндекс не создан: {yandex_error}"
    if ozon_error:
        result["cdek_warning"] = f"Заказ Ozon не создан: {ozon_error}"
    return result


class YandexCreateRequest(BaseModel):
    processed_by: Optional[str] = None


@router.post("/requests/{req_id}/yandex-create")
def yandex_create(req_id: int, payload: YandexCreateRequest, db: Session = Depends(get_db)):
    """Создать (или пересоздать) заказ Яндекс NDD для уже одобренной заявки.

    Используется когда автосоздание при одобрении упало (невалидный телефон, сетевая ошибка и т.п.).
    """
    row = _check_exists(req_id, db)
    if row["status"] not in ("approved", "packed"):
        raise HTTPException(status_code=400, detail="Заявка должна быть в статусе 'одобрена' или 'собрана'")
    if row.get("delivery_method") != "yandex":
        raise HTTPException(status_code=400, detail="Заявка не помечена как Яндекс-доставка")

    pvz_code = row.get("yandex_pvz_code") or row.get("client_pvz_code") or ""
    if not pvz_code:
        raise HTTPException(status_code=400, detail="У заявки нет подобранного ПВЗ Яндекса")

    from .. import yandex_ndd

    # Если уже есть заказ (например, создан со старой точки сдачи) — отменяем и создаём заново
    old_track = (row.get("track_number") or "").strip()
    if old_track:
        try:
            yandex_ndd.cancel_order(old_track)
        except Exception as e:
            print(f"[yandex-create] cancel old {old_track}: {e}")

    try:
        yd_result = yandex_ndd.create_order(
            req_id=req_id,
            customer_name=row["customer_name"],
            customer_phone=row["customer_phone"],
            destination_pvz_id=pvz_code,
            items_description=row.get("items_to_send", "Комплектующие"),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Яндекс API: {e}")

    yandex_track = yd_result["request_id"]
    cost = yd_result.get("price")

    db.execute(text("""
        UPDATE reshipment_requests SET
            track_number = :track,
            shipping_cost = COALESCE(:cost, shipping_cost),
            processed_by = COALESCE(:by, processed_by)
        WHERE id = :id
    """), {"track": yandex_track, "cost": cost, "by": payload.processed_by, "id": req_id})
    db.commit()

    return {
        "status":       "success",
        "track_number": yandex_track,
        "price":        cost,
        "message":      f"Заказ Яндекс создан: {yandex_track}",
    }


@router.post("/requests/{req_id}/ozon-create")
def ozon_create(req_id: int, payload: YandexCreateRequest, db: Session = Depends(get_db)):
    """Создать заказ Ozon Доставки, если при одобрении это не удалось."""
    row = _check_exists(req_id, db)
    if row["status"] not in ("approved", "packed"):
        raise HTTPException(status_code=400, detail="Заявка должна быть в статусе 'одобрена' или 'собрана'")
    if row.get("delivery_method") != "ozon":
        raise HTTPException(status_code=400, detail="Заявка не помечена как Ozon-доставка")

    pvz_code = row.get("ozon_pvz_code") or row.get("client_pvz_code") or ""
    if not pvz_code:
        raise HTTPException(status_code=400, detail="У заявки нет подобранного ПВЗ Ozon")

    from .. import ozon_delivery
    existing_pn = (row.get("ozon_posting_number") or "").strip()
    existing_on = (row.get("ozon_order_number") or row.get("track_number") or "").strip()
    attempt_key = None
    if existing_pn:
        st = str(ozon_delivery.posting_status(existing_pn) or "").lower()
        if st and st not in ozon_delivery.CANCELED_STATUSES:
            return {
                "status": "success",
                "track_number": existing_on or existing_pn,
                "price": row.get("ozon_cost"),
                "message": f"Заказ Ozon уже есть: {existing_on or existing_pn}",
            }
        attempt_key = existing_on or existing_pn
    try:
        oz_result = ozon_delivery.create_order(
            req_id=req_id,
            customer_name=row["customer_name"],
            customer_phone=row["customer_phone"],
            destination_pvz_id=pvz_code,
            items_description=row.get("items_to_send", "Комплектующие"),
            shipment_method_id=row.get("ozon_shipment_method_id"),
            attempt_key=attempt_key,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ozon API: {e}")

    ozon_order = oz_result.get("order_number") or oz_result.get("posting_number") or ""
    ozon_posting = oz_result.get("posting_number") or ""
    cost = row.get("ozon_cost")

    db.execute(text("""
        UPDATE reshipment_requests SET
            track_number = :track,
            ozon_order_number = :track,
            ozon_posting_number = COALESCE(NULLIF(:posting, ''), ozon_posting_number),
            shipping_cost = COALESCE(:cost, shipping_cost),
            processed_by = COALESCE(:by, processed_by)
        WHERE id = :id
    """), {"track": ozon_order, "posting": ozon_posting, "cost": cost, "by": payload.processed_by, "id": req_id})
    db.commit()

    return {
        "status":       "success",
        "track_number": ozon_order,
        "price":        cost,
        "message":      f"Заказ Ozon создан: {ozon_order}",
    }


class RecreateShipmentRequest(BaseModel):
    processed_by: Optional[str] = None
    notify_client: bool = True


@router.post("/requests/{req_id}/recreate-shipment")
def recreate_shipment(
    req_id: int,
    payload: RecreateShipmentRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Повторная отправка после возврата/отмены перевозчиком.
    Та же заявка и matched_srid (карточка возврата), новый заказ у Яндекса/СДЭК,
    статус → approved для склада.
    """
    row = _check_exists(req_id, db)
    if row["status"] not in ("returned", "shipped", "in_transit", "ready_for_pickup"):
        raise HTTPException(
            status_code=400,
            detail=f"Повторная отправка доступна из статусов «вернулся / в пути / ПВЗ». Сейчас: {row['status']}",
        )

    method = row.get("delivery_method") or ""
    if method not in ("yandex", "cdek", "ozon"):
        raise HTTPException(status_code=400, detail="У заявки не выбран перевозчик")

    old_track = (row.get("track_number") or row.get("cdek_number") or "").strip()
    actor = payload.processed_by or current_user.get("username") or "admin"

    # Пытаемся отменить старый заказ у Яндекса (если ещё можно)
    if method == "yandex" and old_track:
        try:
            from .. import yandex_ndd
            yandex_ndd.cancel_order(old_track)
        except Exception as e:
            print(f"[recreate] cancel old yandex {old_track}: {e}")

    new_track = ""
    new_posting = ""
    new_cost = None
    cdek_uuid = ""
    cdek_number = ""

    if method == "ozon":
        pvz_code = row.get("ozon_pvz_code") or row.get("client_pvz_code") or ""
        if not pvz_code:
            raise HTTPException(status_code=400, detail="Нет ПВЗ Ozon у заявки")
        try:
            from .. import ozon_delivery
            oz = ozon_delivery.create_order(
                req_id=req_id,
                customer_name=row["customer_name"],
                customer_phone=row["customer_phone"],
                destination_pvz_id=pvz_code,
                items_description=row.get("items_to_send", "Комплектующие"),
                shipment_method_id=row.get("ozon_shipment_method_id"),
                attempt_key=old_track or "recreate",
            )
            new_track = oz.get("order_number") or oz.get("posting_number") or ""
            new_posting = oz.get("posting_number") or ""
            if old_track and new_track == old_track:
                raise HTTPException(
                    status_code=502,
                    detail="Ozon вернул старый заказ вместо нового. Повторите создание отправления.",
                )
            new_cost = row.get("ozon_cost")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Ozon API: {e}")
    elif method == "yandex":
        pvz_code = row.get("yandex_pvz_code") or row.get("client_pvz_code") or ""
        if not pvz_code:
            raise HTTPException(status_code=400, detail="Нет ПВЗ Яндекса у заявки")
        try:
            from .. import yandex_ndd
            yd = yandex_ndd.create_order(
                req_id=req_id,
                customer_name=row["customer_name"],
                customer_phone=row["customer_phone"],
                destination_pvz_id=pvz_code,
                items_description=row.get("items_to_send", "Комплектующие"),
            )
            new_track = yd["request_id"]
            new_cost = yd.get("price")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Яндекс API: {e}")
    else:
        pvz_code = row.get("client_pvz_code") or ""
        if not pvz_code:
            raise HTTPException(status_code=400, detail="Нет ПВЗ СДЭК у заявки")
        try:
            from ..cdek import create_order, calculate_cost_for_pvz
            cdek_result = create_order(
                req_id=req_id,
                customer_name=row["customer_name"],
                customer_phone=row["customer_phone"],
                to_pvz_code=pvz_code,
                items_description=row.get("items_to_send", "Комплектующие"),
                customer_email=row.get("customer_email") or None,
            )
            cdek_uuid = cdek_result["uuid"]
            cdek_number = cdek_result.get("cdek_number") or ""
            new_track = cdek_number or cdek_uuid
            try:
                new_cost = calculate_cost_for_pvz(pvz_code, tariff_code=cdek_result.get("tariff_code"))
            except Exception:
                pass
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"СДЭК API: {e}")

    # Сброс полей отправки, matched_srid / клиент / ПВЗ не трогаем
    db.execute(text("""
        UPDATE reshipment_requests SET
            status = 'approved',
            track_number = CASE WHEN :use_track THEN :track ELSE NULL END,
            ozon_order_number = CASE WHEN :is_ozon THEN NULLIF(:track, '') ELSE ozon_order_number END,
            ozon_posting_number = CASE WHEN :is_ozon THEN NULLIF(:posting, '') ELSE ozon_posting_number END,
            cdek_uuid = CASE WHEN :use_track THEN cdek_uuid ELSE NULLIF(:cdek_uuid, '') END,
            cdek_number = CASE WHEN :use_track THEN cdek_number ELSE NULLIF(:cdek_number, '') END,
            shipping_cost = COALESCE(:cost, shipping_cost),
            cdek_cost = CASE WHEN :use_track THEN cdek_cost ELSE COALESCE(:cost, cdek_cost) END,
            yandex_sharing_url = NULL,
            yandex_dispatch_act_id = NULL,
            shipped_at = NULL,
            confirmed_at = NULL,
            confirmation_token = NULL,
            review_requested = false,
            review_step = 0,
            review_last_sent_at = NULL,
            review_msg1_at = NULL,
            review_msg2_at = NULL,
            review_msg3_at = NULL,
            pickup_reminder_sent_at = NULL,
            moderator_comment = CONCAT(
              COALESCE(moderator_comment, ''),
              CASE WHEN coalesce(moderator_comment, '') = '' THEN '' ELSE E'\\n' END,
              :note
            ),
            processed_by = :by,
            processed_at = NOW(),
            updated_at = NOW()
        WHERE id = :id
    """), {
        "id": req_id,
        "use_track": method in ("yandex", "ozon"),
        "is_ozon": method == "ozon",
        "track": new_track,
        "posting": new_posting,
        "cdek_uuid": cdek_uuid,
        "cdek_number": cdek_number,
        "cost": new_cost,
        "by": actor,
        "note": f"[повторная отправка] старый трек {old_track or '—'} → {new_track}",
    })

    # Чтобы снова ушли уведомления по циклу доставки
    db.execute(text("""
        DELETE FROM reshipment_client_notifies
        WHERE request_id = :id
          AND status IN ('shipped', 'in_transit', 'ready_for_pickup', 'delivered', 'returned')
    """), {"id": req_id})
    db.commit()

    notified = False
    if payload.notify_client:
        if method == "ozon":
            msg = (
                "Здравствуйте! Предыдущая посылка с деталями вернулась по техническим причинам. "
                "Мы уже оформили повторную отправку — как только передадим в доставку, "
                "напишем сюда, как забрать её в пункте Ozon."
            )
        else:
            msg = (
                "Здравствуйте! Предыдущая посылка с деталями вернулась по техническим причинам. "
                "Мы уже оформили повторную отправку — как только передадим в доставку, "
                "пришлём новый трек-номер сюда, в чат."
            )
        try:
            reply_sign = row.get("wb_reply_sign")
            if reply_sign:
                from .. import wb_chat
                wb_chat.send_message(reply_sign, msg)
                notified = True
        except Exception as e:
            print(f"[recreate] notify #{req_id}: {e}")

    return {
        "status": "success",
        "request_id": req_id,
        "matched_srid": row.get("matched_srid"),
        "old_track": old_track,
        "track_number": new_track,
        "price": new_cost,
        "delivery_method": method,
        "new_status": "approved",
        "client_notified": notified,
        "message": f"Повторный заказ создан: {new_track}. Заявка #{req_id} → одобрена (склад).",
    }


async def _send_rejection_promo_delayed(req_id: int, delay_sec: int = 10) -> None:
    """Фоновая задача: через delay_sec секунд шлёт одно сообщение об отказе.

    Не отправим деталь + возврат одобрен + промокод. Salebot/Max и чат WB.
    """
    await asyncio.sleep(delay_sec)
    try:
        with db_session() as db:
            row = db.execute(text("""
                SELECT r.id, r.promo_code, r.marketplace, r.salebot_client_id, r.salebot_blocked,
                       r.wb_reply_sign, r.wb_chat_id, r.matched_srid,
                       (SELECT 1 FROM wb_feedbacks f
                        JOIN wb_orders o ON (
                            o.shk_id = f.last_order_shk_id
                            OR (o.dt = f.last_order_created_at + INTERVAL '3 hours'
                                AND o.supplier_article = f.supplier_article)
                        )
                        WHERE o.srid = r.matched_srid LIMIT 1) AS has_review
                FROM reshipment_requests r
                WHERE r.id = :id AND r.promo_code IS NOT NULL AND r.promo_sent_at IS NULL
                  AND (
                        (r.salebot_client_id IS NOT NULL AND COALESCE(r.salebot_blocked, FALSE) = FALSE)
                     OR r.wb_reply_sign IS NOT NULL
                     OR r.wb_chat_id IS NOT NULL
                  )
            """), {"id": req_id}).mappings().first()
            if not row:
                return
            row = dict(row)
            expires_row = db.execute(text(
                "SELECT expires_at FROM reshipment_settings WHERE key = 'promo_code'"
            )).mappings().first()
            expires_at = expires_row["expires_at"] if expires_row else None
            from ..wb_mini_sync import _send_review_msg_to_client, format_rejection_promo_message
            msg = format_rejection_promo_message(
                row["promo_code"],
                expires_at,
                has_review=bool(row.get("has_review")),
                marketplace=row.get("marketplace") or "wb",
            )
            try:
                ok = _send_review_msg_to_client(row, msg, promo_mode=True)
            except Exception as e:
                print(f"[Reject promo] send req#{req_id}: {e}")
                return
            if ok:
                db.execute(text("UPDATE reshipment_requests SET promo_sent_at = NOW() WHERE id = :id"), {"id": req_id})
                db.commit()
    except Exception as e:
        print(f"[Reject promo] req#{req_id}: {e}")


@router.post("/requests/{req_id}/refresh-candidates")
def refresh_candidates(req_id: int, db: Session = Depends(get_db)):
    """Повторный подбор ПВЗ и цен СДЭК/Яндекс для заявки в статусе new/matched."""
    row = _check_exists(req_id, db)
    if row["status"] not in ("new", "matched"):
        raise HTTPException(status_code=400, detail="Обновление кандидатов доступно только для новых заявок")

    city = row.get("address_city") or row.get("client_pvz_city") or ""
    if not city:
        raise HTTPException(status_code=400, detail="У заявки не указан город")

    address = row.get("shipping_address") or city
    client_lat = row.get("client_lat")
    client_lon = row.get("client_lon")

    # Старые заявки без координат клиента — геокодим адрес, иначе расстояние/карта пустые
    if client_lat is None or client_lon is None:
        try:
            from ..pickup_resolver import geocode_address
            geo = geocode_address(address) or geocode_address(city)
            if geo:
                client_lat, client_lon = geo
        except Exception:
            pass

    try:
        from ..pickup_resolver import get_both_candidates, distance_km
        candidates = get_both_candidates(
            city=city, address=address, lat=client_lat, lon=client_lon,
            phone=row.get("customer_phone") or "",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка подбора: {e}")

    cdek_c = candidates.get("cdek")
    yd_c   = candidates.get("yandex")
    oz_c   = candidates.get("ozon")

    updates = {}
    if client_lat is not None and client_lon is not None:
        updates["client_lat"] = client_lat
        updates["client_lon"] = client_lon
    if cdek_c:
        updates["client_pvz_code"]    = cdek_c["pvz_code"]
        updates["client_pvz_address"] = cdek_c["pvz_address"]
        updates["cdek_cost"]          = cdek_c["cost"]
        if cdek_c.get("lat") is not None:
            updates["cdek_pvz_lat"] = cdek_c["lat"]
        if cdek_c.get("lng") is not None:
            updates["cdek_pvz_lng"] = cdek_c["lng"]
    else:
        updates["client_pvz_code"] = None
        updates["cdek_cost"] = None
        updates["cdek_pvz_lat"] = None
        updates["cdek_pvz_lng"] = None
    if yd_c:
        updates["yandex_pvz_code"]    = yd_c["pvz_code"]
        updates["yandex_pvz_address"] = yd_c["pvz_address"]
        updates["yandex_pvz_name"]    = yd_c.get("pvz_name", "")
        updates["yandex_cost"]        = yd_c["cost"]
        if yd_c.get("lat") is not None:
            updates["yandex_pvz_lat"] = yd_c["lat"]
        if yd_c.get("lng") is not None:
            updates["yandex_pvz_lng"] = yd_c["lng"]
    else:
        # Сбрасываем старый «битый» кандидат (например ПВЗ в другом регионе)
        updates["yandex_pvz_code"] = None
        updates["yandex_pvz_address"] = None
        updates["yandex_pvz_name"] = None
        updates["yandex_cost"] = None
        updates["yandex_pvz_lat"] = None
        updates["yandex_pvz_lng"] = None

    if oz_c and oz_c.get("available"):
        updates["ozon_pvz_code"] = oz_c["pvz_code"]
        updates["ozon_pvz_address"] = oz_c.get("pvz_address") or ""
        updates["ozon_pvz_name"] = oz_c.get("pvz_name") or ""
        updates["ozon_cost"] = oz_c.get("cost")
        updates["ozon_client_ok"] = True
        updates["ozon_reason"] = None
        updates["ozon_from_pvz_id"] = oz_c.get("from_pvz_id")
        updates["ozon_from_pvz_address"] = oz_c.get("from_pvz_address")
        updates["ozon_shipment_method_id"] = oz_c.get("shipment_method_id")
        if oz_c.get("lat") is not None:
            updates["ozon_pvz_lat"] = oz_c["lat"]
        if oz_c.get("lng") is not None:
            updates["ozon_pvz_lng"] = oz_c["lng"]
    else:
        updates["ozon_pvz_code"] = None
        updates["ozon_pvz_address"] = None
        updates["ozon_pvz_name"] = None
        updates["ozon_cost"] = None
        updates["ozon_pvz_lat"] = None
        updates["ozon_pvz_lng"] = None
        updates["ozon_from_pvz_id"] = None
        updates["ozon_from_pvz_address"] = None
        updates["ozon_shipment_method_id"] = None
        updates["ozon_client_ok"] = bool((oz_c or {}).get("client_ok")) if oz_c else None
        updates["ozon_reason"] = (oz_c or {}).get("reason") or None

    if updates:
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["req_id"] = req_id
        db.execute(text(f"UPDATE reshipment_requests SET {set_clause} WHERE id = :req_id"), updates)
        db.commit()

    oz_ok = bool(oz_c and oz_c.get("available"))
    if not cdek_c and not yd_c and not oz_ok:
        return {
            "status": "not_found",
            "message": f"Ни один перевозчик не нашёл ПВЗ рядом с «{city}» (возможно, адрес вне РФ)",
            "client": {
                "lat":     float(client_lat) if client_lat is not None else None,
                "lon":     float(client_lon) if client_lon is not None else None,
                "address": address,
            },
            "cdek": {"available": False},
            "yandex": {"available": False},
            "ozon": {
                "available": False,
                "reason": (oz_c or {}).get("reason") or "",
                "client_ok": bool((oz_c or {}).get("client_ok")),
            },
            "updated": [k for k in updates.keys() if k != "req_id"],
        }
    cdek_lat = cdek_c.get("lat") if cdek_c else None
    cdek_lng = cdek_c.get("lng") if cdek_c else None
    yd_lat   = yd_c.get("lat") if yd_c else None
    yd_lng   = yd_c.get("lng") if yd_c else None

    return {
        "status":  "success",
        "client": {
            "lat":     float(client_lat) if client_lat is not None else None,
            "lon":     float(client_lon) if client_lon is not None else None,
            "address": address,
        },
        "cdek": {
            "available":   bool(cdek_c),
            "cost":        cdek_c["cost"] if cdek_c else None,
            "pvz_address": cdek_c["pvz_address"] if cdek_c else None,
            "lat":         cdek_lat,
            "lng":         cdek_lng,
            "distance_km": distance_km(client_lat, client_lon, cdek_lat, cdek_lng),
        },
        "yandex": {
            "available":   bool(yd_c),
            "cost":        yd_c["cost"] if yd_c else None,
            "pvz_address": yd_c["pvz_address"] if yd_c else None,
            "pvz_name":    yd_c.get("pvz_name") if yd_c else None,
            "lat":         yd_lat,
            "lng":         yd_lng,
            "distance_km": distance_km(client_lat, client_lon, yd_lat, yd_lng),
        },
        "ozon": {
            "available":   oz_ok,
            "cost":        oz_c.get("cost") if oz_ok else None,
            "pvz_address": oz_c.get("pvz_address") if oz_ok else None,
            "pvz_name":    oz_c.get("pvz_name") if oz_ok else None,
            "lat":         oz_c.get("lat") if oz_ok else None,
            "lng":         oz_c.get("lng") if oz_ok else None,
            "distance_km": distance_km(client_lat, client_lon, (oz_c or {}).get("lat"), (oz_c or {}).get("lng")) if oz_ok else None,
            "client_ok":   bool((oz_c or {}).get("client_ok")) if oz_c else None,
            "reason":      (oz_c or {}).get("reason") or "",
            "from_pvz_address": (oz_c or {}).get("from_pvz_address") or "",
            "recommended": oz_ok,
        },
        "updated": [k for k in updates.keys() if k != "req_id"],
    }


@router.post("/requests/{req_id}/reject")
def reject_request(req_id: int, payload: RejectRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Отклонение заявки менеджером КС."""
    row = _check_exists(req_id, db)

    # Текущий недельный промокод (обновляется вручную в интерфейсе)
    promo_row = db.execute(text(
        "SELECT value FROM reshipment_settings WHERE key = 'promo_code'"
    )).mappings().first()
    promo_code = ((promo_row["value"] if promo_row else "") or "").strip() or None

    db.execute(text("""
        UPDATE reshipment_requests SET
            status = 'rejected', moderator_comment = :comment,
            processed_by = :by, processed_at = NOW(), rejected_by = 'cs',
            promo_code = :promo_code
        WHERE id = :id
    """), {
        "comment": payload.moderator_comment, "by": payload.processed_by,
        "promo_code": promo_code, "id": req_id,
    })
    db.commit()

    claim_result = None
    if (row.get("marketplace") or "wb").lower() == "wb":
        try:
            from ..wb_claims_api import approve_return_for_reshipment
            claim_result = approve_return_for_reshipment(
                db,
                srid=row.get("matched_srid"),
                wb_chat_id=row.get("wb_chat_id"),
                order_number=row.get("order_number"),
            )
            print(f"[WB Claims] reject reshipment #{req_id}: {claim_result}")
        except Exception as _e:
            claim_result = {"ok": False, "error": str(_e)}
            print(f"[WB Claims] auto-approve on reject #{req_id}: {_e}")

    # Промокод = извинение, шаблон «не можем отправить» рядом с ним путает.
    # Если возврат WB уже есть — тот шаблон тоже не нужен.
    skip_return_template = _skip_reject_stock_template(promo_code, claim_result)

    if not skip_return_template:
        mp_label = "Яндекс Маркета" if (row.get("marketplace") or "").lower() == "ym" else "Wildberries"
        rejection_msg = (
            "К сожалению, мы не можем выполнить вашу заявку на отправку детали — "
            "запрашиваемая позиция отсутствует на нашем складе. "
            f"Рекомендуем оформить возврат в личном кабинете {mp_label}."
        )
        _wb_notify(row, f"❌ {rejection_msg}")
        salebot_id = row.get("salebot_client_id")
        if salebot_id:
            try:
                from ..salebot import notify_rejected, is_enabled
                if is_enabled():
                    notify_rejected(salebot_id, req_id)
            except Exception:
                pass
    if promo_code and not row.get("salebot_blocked") and (
        row.get("salebot_client_id") or row.get("wb_reply_sign") or row.get("wb_chat_id")
    ):
        background_tasks.add_task(_send_rejection_promo_delayed, req_id, 10)
        promo_scheduled = True
    else:
        promo_scheduled = False
    return {
        "status": "success",
        "message": f"Заявка #{req_id} отклонена",
        "promo_scheduled": promo_scheduled,
        "claim": claim_result,
    }


@router.post("/requests/{req_id}/warehouse-reject")
def warehouse_reject(req_id: int, payload: WarehouseRejectRequest, db: Session = Depends(get_db)):
    """Отклонение заявки менеджером склада — с уведомлением клиенту."""
    row = _check_exists(req_id, db)

    db.execute(text("""
        UPDATE reshipment_requests SET
            status = 'rejected', rejection_reason = :reason,
            processed_by = :by, processed_at = NOW(), rejected_by = 'warehouse'
        WHERE id = :id
    """), {"reason": payload.rejection_reason, "by": payload.processed_by, "id": req_id})
    db.commit()

    if (row.get("marketplace") or "wb").lower() == "wb":
        try:
            from ..wb_claims_api import approve_return_for_reshipment
            claim_wh = approve_return_for_reshipment(
                db,
                srid=row.get("matched_srid"),
                wb_chat_id=row.get("wb_chat_id"),
                order_number=row.get("order_number"),
            )
            print(f"[WB Claims] warehouse-reject #{req_id}: {claim_wh}")
        except Exception as _e:
            print(f"[WB Claims] warehouse-reject claim #{req_id}: {_e}")

    # Отправляем email клиенту если есть адрес
    email_sent = False
    if row.get("customer_email"):
        try:
            from ..cdek import send_rejection_email
            email_sent = send_rejection_email(
                to_email=row["customer_email"],
                customer_name=row["customer_name"],
                rejection_reason=payload.rejection_reason,
                req_id=req_id,
            )
        except Exception:
            pass

    # Уведомить покупателя в WB чате (если заявка пришла через чат)
    wb_notified = False
    wb_reply_sign = row.get("wb_reply_sign")
    if wb_reply_sign:
        try:
            from ..wb_chat import send_rejection_notification, is_enabled
            if is_enabled():
                wb_notified = send_rejection_notification(wb_reply_sign, payload.rejection_reason)
        except Exception:
            pass

    # Уведомить клиента через Salebot
    salebot_notified = False
    salebot_id = row.get("salebot_client_id")
    if salebot_id:
        try:
            from ..salebot import notify_rejected, is_enabled
            if is_enabled():
                salebot_notified = notify_rejected(salebot_id, req_id, payload.rejection_reason)
        except Exception:
            pass

    return {
        "status":          "success",
        "message":         f"Заявка #{req_id} отклонена",
        "email_sent":      email_sent,
        "wb_notified":     wb_notified,
        "salebot_notified": salebot_notified,
        "customer_phone":  row.get("customer_phone"),
        "customer_name":   row.get("customer_name"),
    }


@router.post("/requests/{req_id}/warehouse-confirm")
def warehouse_confirm(req_id: int, processed_by: Optional[str] = None, tariff_code: Optional[int] = None, db: Session = Depends(get_db)):
    """Подтверждение одиночной заявки складом — создаёт заказ в СДЭК."""
    return _do_warehouse_confirm(req_id, processed_by, db, tariff_code=tariff_code)


@router.post("/requests/warehouse-confirm-bulk")
def warehouse_confirm_bulk(payload: BulkConfirmRequest, db: Session = Depends(get_db)):
    """Массовое подтверждение заявок складом — создаёт заказы в СДЭК."""
    results = []
    for req_id in payload.ids:
        try:
            r = _do_warehouse_confirm(req_id, payload.processed_by, db, tariff_code=payload.tariff_code)
            results.append({"id": req_id, "ok": True, "cdek_number": r.get("cdek_number", "")})
        except HTTPException as e:
            results.append({"id": req_id, "ok": False, "error": e.detail})
        except Exception as e:
            results.append({"id": req_id, "ok": False, "error": str(e)})
    return {"status": "success", "results": results}


def _do_warehouse_confirm(req_id: int, processed_by: Optional[str], db: Session, tariff_code: Optional[int] = None) -> dict:
    """Внутренняя функция: подтвердить заявку и создать заказ в СДЭК."""
    row = _check_exists(req_id, db)

    if row["status"] not in ("approved", "packed"):
        raise HTTPException(status_code=400, detail="Заявка должна быть в статусе 'одобрена' или 'собрана'")

    if row.get("delivery_method") in ("yandex", "ozon"):
        raise HTTPException(
            status_code=400,
            detail="Эта заявка не на СДЭК — сдайте в пункт выбранной службы, не создавайте заказ СДЭК",
        )

    # Определяем город и находим ближайший ПВЗ автоматически
    city = row.get("address_city") or row.get("client_pvz_city")
    if not city:
        raise HTTPException(status_code=400, detail="У заявки не указан город клиента — невозможно найти ПВЗ СДЭК")

    try:
        from ..cdek import create_order, calculate_cost_for_pvz, find_nearest_pvz
        pvz_code = row.get("client_pvz_code") or find_nearest_pvz(city)
        if not pvz_code:
            raise HTTPException(status_code=502, detail=f"Не найдены ПВЗ СДЭК в городе {city}")

        cdek_result = create_order(
            req_id=req_id,
            customer_name=row["customer_name"],
            customer_phone=row["customer_phone"],
            to_pvz_code=pvz_code,
            items_description=row.get("items_to_send", "Комплектующие"),
            tariff_code=tariff_code,
            customer_email=row.get("customer_email") or None,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка СДЭК API: {e}")

    # Пересчитываем стоимость по ПВЗ (не по названию села)
    cost = row.get("cdek_cost")
    try:
        cost = calculate_cost_for_pvz(pvz_code, tariff_code=cdek_result.get("tariff_code")) or cost
    except Exception:
        pass

    token = secrets.token_hex(32)
    db.execute(text("""
        UPDATE reshipment_requests SET
            status = 'shipped',
            delivery_method = 'cdek',
            cdek_uuid = :uuid,
            cdek_number = :cdek_num,
            cdek_cost = COALESCE(:cost, cdek_cost),
            cdek_tariff_code = :tariff_code,
            client_pvz_code = COALESCE(client_pvz_code, :pvz),
            track_number = COALESCE(:cdek_num, track_number),
            shipped_at = NOW(),
            confirmation_token = :token,
            processed_by = COALESCE(:by, processed_by)
        WHERE id = :id
    """), {
        "uuid":         cdek_result["uuid"],
        "cdek_num":     cdek_result["cdek_number"] or None,
        "cost":         cost,
        "tariff_code":  cdek_result.get("tariff_code"),
        "pvz":          pvz_code,
        "token":        token,
        "by":           processed_by,
        "id":           req_id,
    })
    db.commit()

    confirm_url = f"https://box.vidovito.com/reshipment/confirm/{token}"

    # Уведомить покупателя в WB чате (если заявка пришла через чат)
    wb_notified = False
    wb_reply_sign = row.get("wb_reply_sign")
    if wb_reply_sign and cdek_result.get("cdek_number"):
        try:
            from ..wb_chat import send_tracking_notification, is_enabled
            if is_enabled():
                wb_notified = send_tracking_notification(
                    reply_sign=wb_reply_sign,
                    cdek_number=cdek_result["cdek_number"],
                    confirm_url=confirm_url,
                )
        except Exception:
            pass

    # Уведомить клиента через Salebot
    salebot_notified = False
    salebot_id = row.get("salebot_client_id")
    if salebot_id and cdek_result.get("cdek_number"):
        try:
            from ..salebot import notify_shipped, is_enabled
            if is_enabled():
                salebot_notified = notify_shipped(
                    salebot_id, req_id,
                    track_number=cdek_result["cdek_number"],
                    delivery_method="cdek",
                    confirm_url=confirm_url,
                    pvz_address=row.get("client_pvz_address") or "",
                )
        except Exception:
            pass

    return {
        "status":            "success",
        "message":           f"Заказ СДЭК создан для заявки #{req_id}",
        "cdek_number":       cdek_result["cdek_number"],
        "cdek_uuid":         cdek_result["uuid"],
        "cdek_cost":         cost,
        "pvz_code":          pvz_code,
        "confirmation_url":  confirm_url,
        "wb_notified":       wb_notified,
        "salebot_notified":  salebot_notified,
    }


@router.post("/requests/{req_id}/ship-yandex")
def ship_yandex(req_id: int, payload: ShipYandexRequest, db: Session = Depends(get_db)):
    """Отправка через Яндекс Доставку — ручной ввод трека и стоимости."""
    row = _check_exists(req_id, db)
    token = secrets.token_hex(32)
    db.execute(text("""
        UPDATE reshipment_requests SET
            status = 'shipped',
            delivery_method = 'yandex',
            track_number = :track,
            shipping_cost = :cost,
            shipped_at = NOW(),
            confirmation_token = :token,
            processed_by = COALESCE(:by, processed_by)
        WHERE id = :id
    """), {
        "track": payload.track_number.strip(),
        "cost":  payload.shipping_cost,
        "token": token,
        "by":    payload.processed_by,
        "id":    req_id,
    })
    db.commit()
    confirm_url = f"https://box.vidovito.com/reshipment/confirm/{token}"
    track = payload.track_number.strip()
    sharing_url = ""
    eta_date = ""
    try:
        from .. import yandex_ndd
        info = yandex_ndd.get_order_status(track)
        sharing_url = (info.get("sharing_url") or "").strip()
        eta_date = info.get("eta_raw") or ""
        if sharing_url:
            db.execute(text("""
                UPDATE reshipment_requests SET yandex_sharing_url = :url WHERE id = :id
            """), {"url": sharing_url, "id": req_id})
            db.commit()
    except Exception:
        pass

    # Уведомить клиента через Salebot
    salebot_notified = False
    salebot_id = row.get("salebot_client_id")
    if salebot_id:
        try:
            from ..salebot import notify_shipped, is_enabled
            if is_enabled():
                salebot_notified = notify_shipped(
                    salebot_id, req_id,
                    track_number=track,
                    delivery_method="yandex",
                    confirm_url=confirm_url,
                    sharing_url=sharing_url,
                    items_to_send=row.get("items_to_send") or "",
                    pvz_address=(row.get("yandex_pvz_address") or row.get("client_pvz_address") or ""),
                    pvz_name=row.get("yandex_pvz_name") or "",
                    eta_date=eta_date,
                )
        except Exception:
            pass

    if not salebot_notified:
        from ..salebot import format_delivery_status_message
        _wb_notify(row, format_delivery_status_message(
            "shipped",
            track_number=track,
            delivery_method="yandex",
            sharing_url=sharing_url,
            items_to_send=row.get("items_to_send") or "",
            pvz_address=(row.get("yandex_pvz_address") or row.get("client_pvz_address") or ""),
            pvz_name=row.get("yandex_pvz_name") or "",
            eta_date=eta_date,
        ))

    return {
        "status":           "success",
        "message":          f"Заявка #{req_id} отправлена через Яндекс",
        "confirmation_url": confirm_url,
        "salebot_notified": salebot_notified,
    }


@router.post("/requests/{req_id}/ship-yandex-auto")
def ship_yandex_auto(req_id: int, processed_by: Optional[str] = None, db: Session = Depends(get_db)):
    """Создать заявку Яндекс автоматически через API ПВЗ→ПВЗ."""
    row = _check_exists(req_id, db)

    if row["status"] != "approved":
        raise HTTPException(status_code=400, detail="Заявка должна быть в статусе 'одобрена'")

    pvz_code = row.get("yandex_pvz_code") or row.get("client_pvz_code") or ""
    if not pvz_code:
        raise HTTPException(status_code=400, detail="У заявки нет подобранного ПВЗ Яндекса")

    cost = None
    claim_status = "created"
    try:
        from .. import yandex_ndd
        if not yandex_ndd.is_enabled():
            raise HTTPException(status_code=503, detail="YANDEX_DELIVERY_API_KEY не настроен")
        result = yandex_ndd.create_order(
            req_id=req_id,
            customer_name=row["customer_name"],
            customer_phone=row["customer_phone"],
            destination_pvz_id=pvz_code,
            items_description=row.get("items_to_send", "Комплектующие"),
        )
        claim_id = result["request_id"]
        cost = result.get("price")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка Яндекс API: {e}")

    token = secrets.token_hex(32)

    db.execute(text("""
        UPDATE reshipment_requests SET
            status = 'shipped',
            delivery_method = 'yandex',
            track_number = :track,
            shipping_cost = COALESCE(:cost, shipping_cost),
            shipped_at = NOW(),
            confirmation_token = :token,
            processed_by = COALESCE(:by, processed_by)
        WHERE id = :id
    """), {
        "track": claim_id,
        "cost":  cost,
        "token": token,
        "by":    processed_by,
        "id":    req_id,
    })
    db.commit()

    confirm_url = f"https://box.vidovito.com/reshipment/confirm/{token}"

    # Уведомить клиента через Salebot
    salebot_notified = False
    salebot_id = row.get("salebot_client_id")
    if salebot_id:
        try:
            from ..salebot import notify_shipped, is_enabled as sb_enabled
            if sb_enabled():
                salebot_notified = notify_shipped(
                    salebot_id, req_id,
                    track_number=claim_id,
                    delivery_method="yandex",
                    confirm_url=confirm_url,
                    pvz_address=(row.get("yandex_pvz_address") or row.get("client_pvz_address") or ""),
                    pvz_name=row.get("yandex_pvz_name") or "",
                )
        except Exception:
            pass

    return {
        "status":           "success",
        "claim_id":         claim_id,
        "claim_status":     claim_status,
        "confirmation_url": confirm_url,
        "salebot_notified": salebot_notified,
    }


@router.post("/requests/{req_id}/mark-returned")
def mark_returned(req_id: int, processed_by: Optional[str] = None, db: Session = Depends(get_db)):
    """Отмечает что посылка вернулась на склад (статус shipped → returned)."""
    row = _check_exists(req_id, db)
    if row["status"] != "shipped":
        raise HTTPException(status_code=400, detail="Заявка должна быть в статусе 'отправлена'")

    db.execute(text("""
        UPDATE reshipment_requests SET
            status = 'returned',
            processed_by = COALESCE(:by, processed_by)
        WHERE id = :id
    """), {"by": processed_by, "id": req_id})
    db.commit()
    return {"status": "success", "message": f"Заявка #{req_id} отмечена как возвращённая"}


ALLOWED_MANUAL_STATUSES = {"new", "matched", "approved", "packed", "rejected", "shipped", "delivered", "returned"}


class SetStatusRequest(BaseModel):
    status: str
    comment: Optional[str] = None
    processed_by: Optional[str] = None


@router.post("/requests/{req_id}/set-status")
def set_status(req_id: int, payload: SetStatusRequest, db: Session = Depends(get_db),
               current_user: dict = Depends(get_current_user)):
    """Ручная смена статуса заявки — только для admin и moderator."""
    if current_user.get("role") not in ("admin", "moderator"):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Только для администратора")
    if payload.status not in ALLOWED_MANUAL_STATUSES:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Недопустимый статус: {payload.status}")
    _check_exists(req_id, db)
    actor = payload.processed_by or current_user.get("username") or "admin"
    db.execute(text("""
        UPDATE reshipment_requests SET
            status = :status,
            moderator_comment = COALESCE(:comment, moderator_comment),
            processed_by = COALESCE(:by, processed_by),
            processed_at = NOW()
        WHERE id = :id
    """), {"status": payload.status, "comment": payload.comment, "by": actor, "id": req_id})
    # Проставляем автора в только что созданной триггером записи лога
    db.execute(text("""
        UPDATE reshipment_status_log SET changed_by = :actor
        WHERE id = (SELECT id FROM reshipment_status_log WHERE request_id = :rid ORDER BY changed_at DESC LIMIT 1)
    """), {"actor": actor, "rid": req_id})
    db.commit()
    return {"status": "success", "message": f"Статус заявки #{req_id} изменён на '{payload.status}'"}


@router.post("/requests/{req_id}/request-review")
def request_review(req_id: int, db: Session = Depends(get_db)):
    _check_exists(req_id, db)
    row = db.execute(text("""
        SELECT wb_reply_sign, salebot_client_id, customer_name
        FROM reshipment_requests WHERE id = :id
    """), {"id": req_id}).mappings().first()
    db.execute(text("UPDATE reshipment_requests SET review_requested = TRUE WHERE id = :id"), {"id": req_id})
    db.commit()

    sent = False
    msg = "Здравствуйте! Если ваша проблема решена после получения детали, мы будем рады, если вы обновите отзыв в личном кабинете Wildberries. Спасибо!"
    if row:
        if row["wb_reply_sign"]:
            try:
                from .. import wb_chat as _wbc
                _wbc.send_message(row["wb_reply_sign"], msg)
                sent = True
            except Exception:
                pass
        if not sent and row["salebot_client_id"]:
            try:
                from ..salebot import send_message, is_enabled
                if is_enabled():
                    send_message(row["salebot_client_id"], msg)
                    sent = True
            except Exception:
                pass

    return {"status": "success", "message_sent": sent}


@router.post("/register-cdek-webhook")
def register_cdek_webhook(db: Session = Depends(get_db)):
    """Регистрирует webhook в СДЭК (вызвать один раз после деплоя)."""
    try:
        from ..cdek import register_webhook
        result = register_webhook("https://cxvo.ru/api/v1/reshipment/cdek-webhook")
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/requests/{req_id}/wb-context")
def get_wb_context(req_id: int, db: Session = Depends(get_db)):
    """Данные WB по заявке: заказ, отзыв (с историей изменений), претензия."""
    req = db.execute(
        text("SELECT wb_chat_id, wb_nm_id, matched_srid FROM reshipment_requests WHERE id = :id"),
        {"id": req_id},
    ).mappings().first()
    if not req:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    result: dict = {"order": None, "claim": None}

    # ── 1. Заказ: ищем по rid из wb_chat_pending или matched_srid ───────────────
    srid_to_find = req.get("matched_srid")
    if req["wb_chat_id"] and not srid_to_find:
        pending = db.execute(
            text("SELECT rid FROM wb_chat_pending WHERE chat_id = :cid"),
            {"cid": req["wb_chat_id"]},
        ).mappings().first()
        if pending and pending["rid"]:
            srid_to_find = pending["rid"]

    if srid_to_find:
        # Сначала ищем в wb_logistics (детальная логистика)
        order = db.execute(text("""
            SELECT l.srid, l.dt, l.supplier_article, l.nm_id,
                   l.finish_price, l.warehouse_name, l.doc_type, l.is_cancel
            FROM wb_logistics l
            WHERE l.srid = :srid
            LIMIT 1
        """), {"srid": srid_to_find}).mappings().first()
        if order:
            result["order"] = dict(order)
        else:
            # Fallback: ищем в wb_orders (без цены — данные из wb_logistics точнее)
            order2 = db.execute(text("""
                SELECT srid, dt, supplier_article, shk_id,
                       NULL::numeric AS finish_price,
                       NULL::text AS warehouse_name,
                       NULL::text AS doc_type,
                       CASE WHEN cancel_dt IS NOT NULL THEN true ELSE false END AS is_cancel
                FROM wb_orders
                WHERE srid = :srid
                LIMIT 1
            """), {"srid": srid_to_find}).mappings().first()
            if order2:
                result["order"] = dict(order2)

    # ── 2. Претензия/возврат ─────────────────────────────────────────────────
    nm_id_for_claims = str(req["wb_nm_id"]) if req["wb_nm_id"] else None

    # Поиск претензии по явно установленному matched_srid
    claim_srid_to_use = req["matched_srid"]

    # Автосвязка: srid из wb_logistics = srid в wb_claims (одинаковый ID заказа у WB)
    if not claim_srid_to_use and result.get("order"):
        order_srid = result["order"].get("srid")
        if order_srid:
            auto_found = db.execute(text(
                "SELECT srid FROM wb_claims WHERE srid = :srid LIMIT 1"
            ), {"srid": order_srid}).mappings().first()
            if auto_found:
                claim_srid_to_use = order_srid
                # Сохраняем matched_srid для будущих запросов
                db.execute(text(
                    "UPDATE reshipment_requests SET matched_srid = :srid WHERE id = :id"
                ), {"srid": order_srid, "id": req_id})
                db.commit()

    CLAIM_TYPE_MAP = {"1": "Брак", "2": "Не то, что заказывали", "3": "Недостача", "4": "Другое"}
    # Коды returns-api: 0=на рассмотрении, 1=отказ, 2=одобрено
    CLAIM_STATUS_MAP = {
        "0": "На рассмотрении", "1": "Отказ", "2": "Одобрено", "3": "Отказ",
        "Отклонено": "Отказ", "Закрыта": "Закрыта",
    }

    if claim_srid_to_use:
        claim = db.execute(text("""
            SELECT srid, created_dt, supplier_article, nm_id, user_comment,
                   status, status_ex, claim_type, photos, order_date, claim_date, is_archive
            FROM wb_claims WHERE srid = :srid LIMIT 1
        """), {"srid": claim_srid_to_use}).mappings().first()
        if claim:
            c = dict(claim)
            st = CLAIM_STATUS_MAP.get(str(c.get("status", "")), c.get("status") or "На рассмотрении")
            if st in ("На рассмотрении", "0", "0.0", "Открыта") and c.get("is_archive"):
                st = "Закрыта"
            c["status"] = st
            c["claim_type"] = CLAIM_TYPE_MAP.get(str(c.get("claim_type", "")), c.get("claim_type") or "Претензия")
            result["claim"] = c

    # ── 3. Отзыв: прямая привязка через shk_id ──────────────────────────────────
    # wb_feedbacks.last_order_shk_id = wb_orders.shk_id (sticker) → wb_orders.srid
    # Точная связь: конкретный отзыв от конкретного покупателя по конкретному заказу
    order_srid = result["order"].get("srid") if result.get("order") else (claim_srid_to_use or req["matched_srid"])

    if order_srid:
        feedback = db.execute(text("""
            SELECT f.id, f.nm_id, f.created_date, f.valuation, f.text, f.pros, f.cons,
                   f.answer_text, f.photos, f.last_order_shk_id, f.order_status
            FROM wb_feedbacks f
            JOIN wb_orders o ON (
                o.shk_id = f.last_order_shk_id
                OR (o.dt = f.last_order_created_at + INTERVAL '3 hours'
                    AND o.supplier_article = f.supplier_article)
            )
            WHERE o.srid = :srid
            ORDER BY f.created_date DESC
            LIMIT 1
        """), {"srid": order_srid}).mappings().first()
        if feedback:
            result["feedback"] = dict(feedback)

    # Снепшоты: отзыв ДО отправки и ПОСЛЕ получения
    before_snap = db.execute(text("""
        SELECT valuation, text, captured_at FROM wb_feedback_snapshots
        WHERE request_id = :rid AND snapshot_type IN ('before', 'on_approve')
        ORDER BY captured_at ASC LIMIT 1
    """), {"rid": req_id}).mappings().first()
    after_snap = db.execute(text("""
        SELECT valuation, text, captured_at FROM wb_feedback_snapshots
        WHERE request_id = :rid AND snapshot_type = 'after'
        ORDER BY captured_at DESC LIMIT 1
    """), {"rid": req_id}).mappings().first()

    if before_snap:
        result["feedback_before"] = dict(before_snap)
    if after_snap:
        result["feedback_after"] = dict(after_snap)

    return {"status": "success", "data": result}


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    # ⚠️ cdek_cost теперь заполняется у ЛЮБОЙ заявки как справочная цена кандидата СДЭК
    # (см. submit_reshipment/pickup_resolver) — даже если в итоге выбран Яндекс. Поэтому
    # НЕЛЬЗЯ брать "cdek_cost, если есть, иначе shipping_cost": нужно смотреть на
    # фактический delivery_method и брать именно его цену; пока перевозчик не выбран
    # (delivery_method IS NULL, заявка не одобрена) — реальных расходов ещё нет, 0.
    rows = db.execute(text("""
        SELECT status, COUNT(*) as cnt,
               SUM(CASE
                     WHEN delivery_method = 'ozon' THEN COALESCE(ozon_cost_actual, shipping_cost, 0)
                     WHEN delivery_method = 'yandex' THEN COALESCE(shipping_cost, 0)
                     WHEN delivery_method = 'cdek'   THEN COALESCE(cdek_cost_actual, cdek_cost, 0)
                     WHEN delivery_method IS NULL AND status NOT IN ('new','matched') THEN COALESCE(cdek_cost_actual, cdek_cost, 0)
                     ELSE 0
                   END) as total_cost
        FROM reshipment_requests GROUP BY status
    """)).mappings().all()

    # Соотношение по перевозчику: одобренные и дальше, без отклонённых.
    # Старые заявки (delivery_method IS NULL, но уже отправлены) считаются как СДЭК.
    carrier_rows = db.execute(text("""
        SELECT COALESCE(delivery_method, 'cdek') AS delivery_method,
               COUNT(*) as cnt,
               SUM(CASE
                     WHEN delivery_method = 'ozon' THEN COALESCE(ozon_cost_actual, shipping_cost, 0)
                     WHEN delivery_method = 'yandex' THEN COALESCE(shipping_cost, 0)
                     ELSE COALESCE(cdek_cost_actual, cdek_cost, 0)
                   END) as total_cost
        FROM reshipment_requests
        WHERE status NOT IN ('new', 'matched', 'rejected')
        GROUP BY COALESCE(delivery_method, 'cdek')
    """)).mappings().all()

    return {
        "status": "success",
        "data": [dict(r) for r in rows],
        "by_carrier": [dict(r) for r in carrier_rows],
    }


# ── Хелпер ───────────────────────────────────────────────────────────────────


import io
import datetime
from fastapi.responses import StreamingResponse


# ── Склад: акты приёма-передачи Яндекс ──────────────────────────────────────

@router.get("/warehouse/yandex-acts")
def get_yandex_dispatch_acts(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    _require_yandex_act_access(current_user)
    acts = db.execute(text("""
        SELECT id, act_number, created_at, created_by, requests_count, total_shipping_cost
        FROM yandex_dispatch_acts
        ORDER BY created_at DESC, id DESC
        LIMIT 30
    """)).mappings().all()
    pending = db.execute(text("""
        SELECT COUNT(*) AS cnt
        FROM reshipment_requests
        WHERE delivery_method = 'yandex'
          AND status = 'packed'
          AND track_number IS NOT NULL
          AND COALESCE(yandex_dispatch_act_id, 0) = 0
    """)).mappings().first()
    return {
        "status": "success",
        "pending_count": int((pending or {}).get("cnt") or 0),
        "has_auto_stamp": bool(_get_yandex_act_stamp_data_uri()),
        "data": [dict(r) for r in acts],
    }


@router.post("/warehouse/yandex-acts/generate")
def generate_yandex_dispatch_act(
    processed_by: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_yandex_act_access(current_user)
    rows = db.execute(text("""
        SELECT id, updated_at, customer_name, customer_phone,
               address_city, client_pvz_city, client_pvz_address,
               yandex_pvz_name, yandex_pvz_address,
               items_to_send, track_number, shipping_cost,
               chat_token, confirmation_token
        FROM reshipment_requests
        WHERE delivery_method = 'yandex'
          AND status = 'packed'
          AND track_number IS NOT NULL
          AND COALESCE(yandex_dispatch_act_id, 0) = 0
        ORDER BY updated_at ASC, id ASC
    """)).mappings().all()

    if not rows:
        raise HTTPException(status_code=400, detail="Нет собранных Яндекс-заявок без акта")

    payload = _serialize_yandex_dispatch_rows(rows)
    act_number = _next_yandex_act_number(db)
    total_shipping_cost = round(sum(float(r["shipping_cost"] or 0) for r in rows), 2)
    actor = processed_by or "system"

    act_row = db.execute(text("""
        INSERT INTO yandex_dispatch_acts (act_number, created_by, requests_count, total_shipping_cost, payload)
        VALUES (:act_number, :created_by, :requests_count, :total_shipping_cost, CAST(:payload AS jsonb))
        RETURNING id, created_at
    """), {
        "act_number": act_number,
        "created_by": actor,
        "requests_count": len(payload),
        "total_shipping_cost": total_shipping_cost,
        "payload": json.dumps(payload, ensure_ascii=False),
    }).mappings().first()

    act_id = act_row["id"]
    req_ids = [r["id"] for r in rows]
    db.execute(text("""
        UPDATE reshipment_requests
        SET yandex_dispatch_act_id = :act_id
        WHERE id = ANY(:ids)
    """), {"act_id": act_id, "ids": req_ids})
    db.commit()

    return {
        "status": "success",
        "data": {
            "id": act_id,
            "act_number": act_number,
            "requests_count": len(payload),
            "total_shipping_cost": total_shipping_cost,
            "download_pdf_url": f"/api/v1/reshipment/warehouse/yandex-acts/{act_id}/download?format=pdf",
            "download_html_url": f"/api/v1/reshipment/warehouse/yandex-acts/{act_id}/download?format=html",
        },
    }


@router.get("/warehouse/yandex-acts/{act_id}/download")
def download_yandex_dispatch_act(
    act_id: int,
    format: str = Query("pdf", pattern="^(pdf|html)$"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_yandex_act_access(current_user)
    act = db.execute(text("""
        SELECT id, act_number, created_at, created_by, total_shipping_cost, payload
        FROM yandex_dispatch_acts
        WHERE id = :id
    """), {"id": act_id}).mappings().first()
    if not act:
        raise HTTPException(status_code=404, detail="Акт не найден")

    payload = act["payload"] or []
    if isinstance(payload, str):
        payload = json.loads(payload)
    html = _render_yandex_dispatch_act_html(
        act_number=act["act_number"],
        created_at=act["created_at"],
        payload=payload,
        total_shipping_cost=float(act["total_shipping_cost"] or 0),
        created_by=act.get("created_by") or "",
    )
    safe_name = act["act_number"].replace("/", "-")

    if format == "html":
        return HTMLResponse(
            content=html,
            headers={"Content-Disposition": f'inline; filename="{safe_name}.html"'},
        )

    font_config = FontConfiguration()
    pdf_bytes = HTML(string=html).write_pdf(font_config=font_config)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe_name}.pdf"'},
    )


@router.delete("/warehouse/yandex-acts/{act_id}")
def delete_yandex_dispatch_act(
    act_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    _require_yandex_act_delete_access(current_user)
    act = db.execute(text("SELECT id FROM yandex_dispatch_acts WHERE id = :id"), {"id": act_id}).mappings().first()
    if not act:
        raise HTTPException(status_code=404, detail="Акт не найден")

    db.execute(text("""
        UPDATE reshipment_requests
        SET yandex_dispatch_act_id = NULL
        WHERE yandex_dispatch_act_id = :id
    """), {"id": act_id})
    db.execute(text("DELETE FROM yandex_dispatch_acts WHERE id = :id"), {"id": act_id})
    db.commit()
    return {"status": "success"}


# ── Склад: очередь заявок к отправке ────────────────────────────────────────

@router.get("/warehouse/queue")
def warehouse_queue(db: Session = Depends(get_db)):
    """Список одобренных заявок для менеджера склада."""
    rows = db.execute(text("""
        SELECT id, customer_name, customer_phone,
               address_city, address_street, address_house,
               client_pvz_code, client_pvz_address,
               items_to_send, cdek_uuid, cdek_number, cdek_cost,
               moderator_comment, processed_at, processed_by,
               status, matched_srid, shipped_at
        FROM reshipment_requests
        WHERE status = 'approved'
        ORDER BY processed_at ASC
    """)).mappings().all()
    return {"status": "success", "count": len(rows), "data": [dict(r) for r in rows]}


# ── Склад: список к упаковке (Excel) ────────────────────────────────────────

@router.get("/warehouse/packlist")
def warehouse_packlist(db: Session = Depends(get_db)):
    """Скачать список к отправке в формате Excel."""
    rows = db.execute(text("""
        SELECT id, customer_name, customer_phone,
               address_city, client_pvz_code, client_pvz_address, shipping_address,
               items_to_send, cdek_number, cdek_uuid, cdek_cost,
               delivery_method, track_number, shipping_cost,
               moderator_comment, processed_at
        FROM reshipment_requests
        WHERE status IN ('approved', 'packed')
        ORDER BY processed_at ASC
    """)).mappings().all()

    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.worksheet.page import PageMargins

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "К отправке"

    # Печать на одном листе A4 альбомно
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToPage   = True
    ws.page_setup.fitToWidth  = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins = PageMargins(left=0.5, right=0.5, top=0.75, bottom=0.75)

    header_fill = PatternFill("solid", fgColor="4F46E5")
    hdr_font    = Font(bold=True, color="FFFFFF", size=10)
    thin        = Border(
        left=Side(style="thin", color="CCCCCC"),
        right=Side(style="thin", color="CCCCCC"),
        top=Side(style="thin", color="CCCCCC"),
        bottom=Side(style="thin", color="CCCCCC"),
    )

    # Ширины подобраны под альбомный A4 (~270мм рабочего поля)
    headers = [
        ("№",           4),
        ("Заявка",      7),
        ("Клиент",      20),
        ("Телефон",     13),
        ("Город",       14),
        ("ПВЗ",         10),
        ("Адрес ПВЗ",   30),
        ("Что отправить", 35),
        ("ТК",          9),
        ("Стоимость",   10),
        ("Трек",        14),
        ("Одобрено",    13),
    ]
    for col, (title, width) in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=title)
        cell.font      = hdr_font
        cell.fill      = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = thin
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = width

    ws.row_dimensions[1].height = 22

    alt_fill = PatternFill("solid", fgColor="F0F0FF")
    for i, row in enumerate(rows, 1):
        fill = alt_fill if i % 2 == 0 else None
        dt = row["processed_at"]
        dt_str = dt.strftime("%d.%m %H:%M") if dt else ""
        pvz_addr = row["client_pvz_address"] or row["shipping_address"] or ""
        is_yandex = row["delivery_method"] == "yandex"
        is_ozon = row["delivery_method"] == "ozon"
        cost = row["shipping_cost"] if (is_yandex or is_ozon) else row["cdek_cost"]
        track = row["track_number"] or row["cdek_number"] or row.get("ozon_order_number") or ""
        if is_ozon:
            tk = "Ozon"
        elif is_yandex:
            tk = "Яндекс"
        else:
            tk = "СДЭК"
        vals = [
            i,
            row["id"],
            row["customer_name"] or "",
            row["customer_phone"] or "",
            row["address_city"] or "",
            row["client_pvz_code"] or "",
            pvz_addr,
            row["items_to_send"] or "",
            tk,
            float(cost) if cost else "",
            track,
            dt_str,
        ]
        # Колонки с переносом текста: адрес (7), что отправить (8)
        wrap_cols = {7, 8}
        for col, val in enumerate(vals, 1):
            cell = ws.cell(row=i+1, column=col, value=val)
            cell.border    = thin
            cell.alignment = Alignment(vertical="center", wrap_text=(col in wrap_cols))
            if fill:
                cell.fill = fill
        ws.row_dimensions[i+1].height = 30  # чуть выше чтобы перенос был виден

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    today = datetime.date.today().strftime("%Y-%m-%d")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="packlist_{today}.xlsx"'},
    )


# ── Склад: PDF этикетки 80×58 мм (горизонталь 80) ─────────────────────────────

LABEL_W_MM = 80  # горизонталь
LABEL_H_MM = 58  # вертикаль


def _label_barcode_value(order: dict) -> str:
    """Штрих-код для этикетки: posting_number Ozon, place_barcode Яндекса или номер СДЭК."""
    req_id = order["id"]
    if order.get("delivery_method") == "ozon":
        from .. import ozon_delivery
        return ozon_delivery.resolve_label_barcode(
            req_id=req_id,
            order_number=order.get("track_number") or order.get("ozon_order_number") or "",
            posting_number=order.get("ozon_posting_number") or "",
        )
    if order.get("delivery_method") == "yandex":
        yd_track = order.get("track_number") or ""
        if yd_track:
            try:
                from .. import yandex_ndd as _yndd
                place_bc = _yndd.get_order_place_barcode(yd_track)
                if place_bc:
                    return place_bc
            except Exception:
                pass
            return yd_track.replace("-", "")[-20:]
        return f"REQ{req_id:06d}"
    cdek_no = order.get("cdek_number") or ""
    return cdek_no if cdek_no else f"REQ{req_id:06d}"


def _ozon_dropoff_payload() -> dict:
    from .. import ozon_delivery
    info = ozon_delivery.dropoff_barcode_info()
    info["svg"] = ozon_delivery.barcode_svg(info.get("barcode") or "")
    return info


@router.get("/ozon-dropoff")
def ozon_dropoff_barcode(current_user: dict = Depends(get_current_user)):
    """Штрихкод метода сдачи Ozon — для админа и склада."""
    role = (current_user.get("role") or "").strip()
    if role not in ("admin", "warehouse_manager"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return _ozon_dropoff_payload()


@router.get("/ozon-dropoff/label")
def ozon_dropoff_label(current_user: dict = Depends(get_current_user)):
    """Этикетка 80×58 со штрихкодом сдачи в ПВЗ Ozon."""
    role = (current_user.get("role") or "").strip()
    if role not in ("admin", "warehouse_manager"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    from .. import ozon_delivery
    info = ozon_delivery.dropoff_barcode_info()
    barcode = (info.get("barcode") or "").strip()
    if not barcode:
        raise HTTPException(status_code=404, detail="Нет штрихкода сдачи Ozon")
    fake = {
        "id": 0,
        "customer_name": info.get("address") or "Сдача в ПВЗ Ozon",
        "address_city": "Ozon",
        "client_pvz_city": "Сдача в ПВЗ",
        "delivery_method": "ozon",
        "track_number": barcode,
        "ozon_order_number": barcode,
        "ozon_posting_number": barcode,
        "print_barcode": True,
    }
    buf = _build_labels_pdf_80x58([fake])
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="ozon_dropoff_barcode.pdf"'},
    )


def _build_labels_pdf_80x58(rows) -> io.BytesIO:
    """Единый PDF: одна страница = одна этикетка 80×58 мм (СДЭК и Яндекс)."""
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.units import mm
    from reportlab.graphics.barcode import code128
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    _DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    _DEJAVU_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    try:
        pdfmetrics.registerFont(TTFont("DejaVu", _DEJAVU))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", _DEJAVU_BOLD))
        _F, _F_BOLD = "DejaVu", "DejaVu-Bold"
    except Exception:
        _F = _F_BOLD = "Helvetica"

    W = LABEL_W_MM * mm
    H = LABEL_H_MM * mm
    MARGIN = 2.5 * mm

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(W, H))

    for order in rows:
        if order.get("delivery_method") == "ozon" and not order.get("print_barcode"):
            continue
        req_id = order["id"]
        name = (order.get("customer_name") or "").strip()
        city = (order.get("client_pvz_city") or order.get("address_city") or "—").strip()
        is_yandex = order.get("delivery_method") == "yandex"
        is_ozon = order.get("delivery_method") == "ozon"
        barcode_val = _label_barcode_value(order)
        if is_ozon:
            carrier_label = "Ozon Доставка"
            carrier_color = colors.HexColor("#005BFF")
        elif is_yandex:
            carrier_label = "Яндекс Доставка"
            carrier_color = colors.HexColor("#D97706")
        else:
            carrier_label = "СДЭК"
            carrier_color = colors.HexColor("#1D4ED8")

        c.setPageSize((W, H))

        # Рамка
        c.setStrokeColor(colors.HexColor("#CCCCCC"))
        c.setLineWidth(0.4)
        c.rect(0.8 * mm, 0.8 * mm, W - 1.6 * mm, H - 1.6 * mm)

        # Верх: перевозчик
        c.setFont(_F_BOLD, 8)
        c.setFillColor(carrier_color)
        c.drawCentredString(W / 2, H - 5.5 * mm, carrier_label)

        # Штрих-код на всю ширину 80 мм; чёрный (Code128 берёт fillColor)
        c.setFillColor(colors.black)
        c.setStrokeColor(colors.black)
        FOOTER_H = 18 * mm
        HEADER_H = 8 * mm
        bc_height = H - HEADER_H - FOOTER_H
        max_bc_w = W - 2 * MARGIN

        try:
            probe = code128.Code128(barcode_val, barWidth=1.0, quiet=is_ozon)
            natural_w = probe.width or 1.0
            bar_w = max_bc_w / natural_w
            if is_ozon:
                bar_w = max(bar_w, 0.28 * mm)
            bc = code128.Code128(barcode_val, barWidth=bar_w, barHeight=bc_height, quiet=is_ozon)
            bc.fillColor = colors.black
            x = MARGIN + max(0, (max_bc_w - bc.width) / 2)
            if bc.width > max_bc_w:
                x = (W - bc.width) / 2
            bc.drawOn(c, x, FOOTER_H)
        except Exception:
            c.setFont(_F_BOLD, 9)
            c.setFillColor(colors.black)
            c.drawCentredString(W / 2, H / 2, barcode_val)

        # Под штрих-кодом: значение + заявка / город / ФИО
        c.setFillColor(colors.black)
        c.setFont(_F_BOLD, 7)
        c.drawCentredString(W / 2, 13 * mm, barcode_val[:32])

        c.setFont(_F_BOLD, 8)
        if req_id:
            c.drawCentredString(W / 2, 8 * mm, f"#{req_id:06d}  {city[:22]}")
        else:
            c.drawCentredString(W / 2, 8 * mm, city[:28] or "Сдача в ПВЗ Ozon")

        c.setFont(_F, 7)
        c.drawCentredString(W / 2, 3.5 * mm, name[:30] if name else "—")

        c.showPage()

    c.save()
    buf.seek(0)
    return buf


def _fetch_label_orders(db: Session, ids: Optional[str], *, yandex_only: bool = False):
    if ids:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        where = "WHERE id = ANY(:ids) AND status IN ('approved','packed','shipped')"
        params: dict = {"ids": id_list}
    else:
        where = "WHERE status = 'approved'"
        params = {}
    if yandex_only:
        where += " AND delivery_method = 'yandex'"

    rows = db.execute(text(f"""
        SELECT id, customer_name, address_city, client_pvz_city,
               items_to_send, cdek_uuid, cdek_number, client_pvz_code,
               delivery_method, track_number, client_pvz_address,
               yandex_pvz_name, yandex_pvz_address, ozon_order_number, ozon_posting_number
        FROM reshipment_requests {where}
        ORDER BY id ASC
    """), params).mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail="Нет заявок для печати")
    return rows


def _persist_ozon_postings(db: Session, rows: list) -> None:
    """Если нашли posting_number при печати — запоминаем, чтобы этикетка не искала снова."""
    from .. import ozon_delivery
    changed = False
    for order in rows:
        if order.get("delivery_method") != "ozon":
            continue
        existing = (order.get("ozon_posting_number") or "").strip()
        if existing:
            continue
        barcode = ozon_delivery.resolve_label_barcode(
            req_id=int(order["id"]),
            order_number=order.get("track_number") or order.get("ozon_order_number") or "",
            posting_number="",
        )
        order_no = (order.get("track_number") or order.get("ozon_order_number") or "").strip()
        if not barcode or barcode == order_no or str(barcode).upper().startswith("REQ"):
            continue
        db.execute(text("""
            UPDATE reshipment_requests
               SET ozon_posting_number = :posting
             WHERE id = :id AND COALESCE(ozon_posting_number, '') = ''
        """), {"posting": barcode, "id": order["id"]})
        order["ozon_posting_number"] = barcode
        changed = True
    if changed:
        db.commit()


def _merge_pdfs(blobs: list) -> io.BytesIO:
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for blob in blobs:
        reader = PdfReader(io.BytesIO(blob))
        for page in reader.pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out


def _ozon_official_label_pdfs(rows: list) -> list:
    """Только официальные этикетки Ozon с QR — самодельный штрихкод ПВЗ не читает."""
    from .. import ozon_delivery
    pdfs = []
    errors = []
    for order in rows:
        if order.get("delivery_method") != "ozon":
            continue
        req_id = order.get("id")
        pn = (order.get("ozon_posting_number") or "").strip()
        if not pn:
            errors.append(f"#{req_id}: нет номера отправления Ozon")
            continue
        ozon_delivery.ensure_ready_for_shipping(pn, wait_s=25)
        raw = ozon_delivery.fetch_label_pdf(pn)
        if not raw or not raw.startswith(b"%PDF"):
            errors.append(f"#{req_id}: Ozon ещё не отдал этикетку с QR")
            continue
        pdfs.append(raw)
    if errors:
        raise HTTPException(status_code=502, detail="; ".join(errors))
    return pdfs


def _labels_pdf_response(rows: list):
    ozon_rows = [r for r in rows if r.get("delivery_method") == "ozon"]
    other_rows = [r for r in rows if r.get("delivery_method") != "ozon"]
    parts = []
    if ozon_rows:
        parts.extend(_ozon_official_label_pdfs(ozon_rows))
    if other_rows:
        parts.append(_build_labels_pdf_80x58(other_rows).read())
    if not parts:
        raise HTTPException(status_code=404, detail="Нет заявок для печати")
    today = datetime.date.today().strftime("%Y-%m-%d")
    buf = io.BytesIO(parts[0]) if len(parts) == 1 else _merge_pdfs(parts)
    buf.seek(0)
    name = "ozon_label" if ozon_rows and not other_rows else "labels"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{name}_{today}.pdf"'},
    )


@router.get("/warehouse/labels")
def warehouse_labels(ids: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Ozon — официальная этикетка с QR. СДЭК и Яндекс — 80×58."""
    rows = [dict(r) for r in _fetch_label_orders(db, ids)]
    _persist_ozon_postings(db, rows)
    return _labels_pdf_response(rows)


@router.get("/warehouse/labels/thermal")
def warehouse_labels_thermal(ids: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Тот же набор этикеток, что /warehouse/labels."""
    rows = [dict(r) for r in _fetch_label_orders(db, ids)]
    _persist_ozon_postings(db, rows)
    return _labels_pdf_response(rows)


# ── Склад: отметить как физически отправленную ────────────────────────────────

@router.post("/requests/{req_id}/pack")
def pack_request(req_id: int, processed_by: Optional[str] = None, db: Session = Depends(get_db),
                 current_user: dict = Depends(get_current_user)):
    """Отметить заявку как 'Собрана' — физически собрана на складе и готова к сдаче."""
    if current_user.get("role") not in ("warehouse_manager", "admin", "moderator"):
        raise HTTPException(status_code=403, detail="Только для менеджера склада")
    row = _check_exists(req_id, db)
    if row["status"] not in ("approved", "packed"):
        raise HTTPException(status_code=400, detail="Заявка должна быть в статусе 'одобрена'")
    db.execute(text("""
        UPDATE reshipment_requests SET status = 'packed', processed_by = COALESCE(:by, processed_by)
        WHERE id = :id
    """), {"by": processed_by, "id": req_id})
    db.commit()
    return {"status": "success", "message": f"Заявка #{req_id} отмечена как собранная"}


@router.post("/requests/pack-bulk")
def pack_bulk(payload: BulkConfirmRequest, db: Session = Depends(get_db),
              current_user: dict = Depends(get_current_user)):
    """Массово отметить заявки как 'Собраны'."""
    if current_user.get("role") not in ("warehouse_manager", "admin", "moderator"):
        raise HTTPException(status_code=403, detail="Только для менеджера склада")
    results = []
    for req_id in payload.ids:
        try:
            row = _check_exists(req_id, db)
            if row["status"] not in ("approved", "packed"):
                results.append({"id": req_id, "ok": False, "error": "Неверный статус"})
                continue
            db.execute(text("""
                UPDATE reshipment_requests SET status = 'packed',
                    processed_by = COALESCE(:by, processed_by)
                WHERE id = :id
            """), {"by": payload.processed_by, "id": req_id})
            db.commit()
            results.append({"id": req_id, "ok": True})
        except Exception as e:
            results.append({"id": req_id, "ok": False, "error": str(e)})
    return {"status": "success", "results": results}


@router.post("/requests/{req_id}/mark-shipped")
def mark_shipped(req_id: int, processed_by: Optional[str] = None, db: Session = Depends(get_db)):
    """Отмечает что посылка физически сдана в ПВЗ (СДЭК или Яндекс)."""
    row = _check_exists(req_id, db)
    if row["status"] not in ("approved", "packed"):
        raise HTTPException(status_code=400, detail="Заявка должна быть в статусе 'одобрена' или 'собрана'")

    is_yandex = row.get("delivery_method") == "yandex"
    is_ozon = row.get("delivery_method") == "ozon"
    method = row.get("delivery_method") or "cdek"
    eta_date = row.get("eta_date") or ""

    # Фиксируем фактическую стоимость из СДЭК (если уже доступна) — у Яндекса цена уже точная
    actual_cost = None
    if not is_yandex and not is_ozon and row.get("cdek_uuid"):
        try:
            from ..cdek import get_order_by_uuid
            info = get_order_by_uuid(row["cdek_uuid"])
            actual_cost = info.get("actual_cost")
            eta_date = info.get("planned_delivery_date") or eta_date
        except Exception:
            pass

    token = secrets.token_hex(32)
    db.execute(text("""
        UPDATE reshipment_requests SET
            status = 'shipped',
            shipped_at = NOW(),
            cdek_cost_actual = COALESCE(:actual_cost, cdek_cost_actual),
            confirmation_token = COALESCE(confirmation_token, :token),
            processed_by = COALESCE(:by, processed_by)
        WHERE id = :id
    """), {"actual_cost": actual_cost, "token": token, "by": processed_by, "id": req_id})
    db.commit()

    track_number = (row.get("track_number") or row.get("ozon_order_number") or row.get("cdek_number") or "")
    confirm_url = f"https://box.vidovito.com/reshipment/confirm/{token}"
    sharing_url = (row.get("yandex_sharing_url") or "").strip()
    if is_yandex and track_number and not sharing_url:
        try:
            from .. import yandex_ndd
            info = yandex_ndd.get_order_status(track_number)
            sharing_url = (info.get("sharing_url") or "").strip()
            eta_date = info.get("eta_raw") or eta_date
            if sharing_url:
                db.execute(text("""
                    UPDATE reshipment_requests SET yandex_sharing_url = :url
                    WHERE id = :id AND (yandex_sharing_url IS NULL OR yandex_sharing_url = '')
                """), {"url": sharing_url, "id": req_id})
                db.commit()
        except Exception:
            pass

    if is_ozon:
        try:
            from .. import ozon_delivery
            info = ozon_delivery.get_order_status(
                row.get("ozon_posting_number") or row.get("ozon_order_number") or track_number
            )
            eta_date = info.get("eta_raw") or eta_date
        except Exception:
            pass

    notify_kwargs = {
        "track_number": track_number,
        "delivery_method": method if method in ("cdek", "yandex", "ozon") else "cdek",
        "sharing_url": sharing_url,
        "items_to_send": row.get("items_to_send") or "",
        "pvz_address": (row.get("ozon_pvz_address") if is_ozon else None)
            or (row.get("yandex_pvz_address") or row.get("client_pvz_address") or ""),
        "pvz_name": (row.get("ozon_pvz_name") if is_ozon else None) or row.get("yandex_pvz_name") or "",
        "eta_date": eta_date,
    }

    salebot_notified = False
    salebot_id = row.get("salebot_client_id")
    if salebot_id and track_number:
        try:
            from ..salebot import notify_shipped, is_enabled
            if is_enabled():
                salebot_notified = notify_shipped(
                    salebot_id, req_id,
                    confirm_url=confirm_url,
                    **notify_kwargs,
                )
        except Exception:
            pass

    if not salebot_notified:
        from ..salebot import format_delivery_status_message
        if track_number:
            _wb_notify(row, format_delivery_status_message("shipped", **notify_kwargs))
        else:
            _wb_notify(row, "📦 Ваша посылка передана в доставку! Трек-номер для отслеживания пришлём отдельно.")

    return {"status": "success", "message": f"Заявка #{req_id} отмечена как отправленная", "salebot_notified": salebot_notified}


def _wb_notify(req: dict, msg: str) -> None:
    """Уведомить клиента в чат площадки (WB, Яндекс Маркет или Ozon)."""
    from .. import reshipment_notify
    reshipment_notify.send_marketplace_message(req, msg)


class SendWbMessageRequest(BaseModel):
    text: str

@router.post("/requests/{req_id}/send-wb-message")
def send_wb_message(req_id: int, payload: SendWbMessageRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """Отправить произвольное сообщение клиенту в чат WB, ЯМ или Ozon."""
    req = _check_exists(req_id, db)
    text_msg = payload.text.strip()
    if not text_msg:
        raise HTTPException(status_code=400, detail="Пустое сообщение")

    ozon_chat_id = (req.get("ozon_chat_id") or "").strip()
    if ozon_chat_id:
        try:
            from .. import ozon_chat_sync
            ozon_chat_sync.send_message(ozon_chat_id, text_msg)
            return {"status": "ok", "channel": "ozon"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка отправки в чат Ozon: {e}")

    ym_chat_id = (req.get("ym_chat_id") or "").strip()
    if ym_chat_id:
        try:
            from .. import ym_chat_sync
            ym_chat_sync.send_message(ym_chat_id, text_msg)
            return {"status": "ok", "channel": "ym"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка отправки в чат ЯМ: {e}")

    reply_sign = req.get("wb_reply_sign")
    if not reply_sign:
        raise HTTPException(status_code=400, detail="У заявки нет привязанного чата WB/ЯМ/Ozon")
    try:
        from .. import wb_chat
        wb_chat.send_message(reply_sign, text_msg)
        return {"status": "ok", "channel": "wb"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка отправки в WB чат: {e}")


def _check_exists(req_id: int, db: Session) -> dict:
    row = db.execute(
        text("SELECT * FROM reshipment_requests WHERE id = :id"), {"id": req_id}
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return dict(row)
