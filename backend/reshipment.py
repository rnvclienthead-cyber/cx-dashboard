import secrets
import os
import json
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional
from ..database import get_db
from .auth import get_current_user

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    _limiter = Limiter(key_func=get_remote_address)
    _rate_limit = _limiter.limit("10/hour")
    HAS_LIMITER = True
except ImportError:
    HAS_LIMITER = False
    _rate_limit = lambda f: f

UPLOAD_DIR = Path("/root/cx-dashboard/uploads/reshipment")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
MAX_FILE_SIZE_MB = 15

# ── Публичный роутер (без авторизации) ──────────────────────────────────────
public_router = APIRouter(prefix="/api/v1/reshipment", tags=["Reshipment / Доотправка"])

# ── Внутренний роутер (с авторизацией) ──────────────────────────────────────
router = APIRouter(
    prefix="/api/v1/reshipment",
    tags=["Reshipment / Доотправка"],
    dependencies=[Depends(get_current_user)]
)


# ── Схемы ────────────────────────────────────────────────────────────────────

class ApproveRequest(BaseModel):
    matched_srid: Optional[str] = None
    match_notes: Optional[str] = None
    moderator_comment: Optional[str] = None
    processed_by: Optional[str] = None


class RejectRequest(BaseModel):
    moderator_comment: str
    processed_by: Optional[str] = None


class WarehouseRejectRequest(BaseModel):
    rejection_reason: str
    processed_by: Optional[str] = None


class BulkConfirmRequest(BaseModel):
    ids: list[int]
    processed_by: Optional[str] = None


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

    shipping_address = client_pvz_address or ", ".join(filter(None, [
        address_postal, address_region, address_city, address_street, address_house
    ])) or None

    # Если форма пришла из WB чата — достаём reply_sign и nmId из pending
    wb_reply_sign  = None
    wb_nm_id       = None
    wb_client_id   = None
    clean_chat_id  = (wb_chat_id or "").strip() or None

    if clean_chat_id:
        pending = db.execute(text("""
            SELECT reply_sign, nm_id, client_id FROM wb_chat_pending WHERE chat_id = :cid
        """), {"cid": clean_chat_id}).mappings().first()
        if pending:
            wb_reply_sign = pending["reply_sign"]
            wb_nm_id      = pending["nm_id"]
            wb_client_id  = pending["client_id"]

    try:
        result = db.execute(text("""
            INSERT INTO reshipment_requests
                (customer_name, customer_email, customer_phone, order_number,
                 product_category, problem_type, items_to_send,
                 shipping_address, address_postal, address_region,
                 address_city, address_street, address_house,
                 client_pvz_code, client_pvz_address, client_pvz_city,
                 photo_files, privacy_consent,
                 wb_chat_id, wb_reply_sign, wb_nm_id, wb_client_id)
            VALUES
                (:name, :email, :phone, :order_num,
                 :category, :problem_type, :items,
                 :address, :postal, :region,
                 :city, :street, :house,
                 :pvz_code, :pvz_address, :pvz_city,
                 :photos, :consent,
                 :wb_chat_id, :wb_reply_sign, :wb_nm_id, :wb_client_id)
            RETURNING id
        """), {
            "name":          customer_name.strip(),
            "email":         customer_email,
            "phone":         customer_phone.strip(),
            "order_num":     order_number,
            "category":      product_category,
            "problem_type":  problem_type,
            "items":         items_to_send.strip(),
            "address":       shipping_address,
            "postal":        address_postal,
            "region":        address_region,
            "city":          address_city or client_pvz_city,
            "street":        address_street,
            "house":         address_house,
            "pvz_code":      client_pvz_code,
            "pvz_address":   client_pvz_address,
            "pvz_city":      client_pvz_city,
            "photos":        photo_files,
            "consent":       personal_data_consent,
            "wb_chat_id":    clean_chat_id,
            "wb_reply_sign": wb_reply_sign,
            "wb_nm_id":      wb_nm_id,
            "wb_client_id":  wb_client_id,
        })
        db.commit()
        new_id = result.scalar()

        # Обновляем статус pending записи
        if clean_chat_id:
            db.execute(text("""
                UPDATE wb_chat_pending
                SET status = 'form_submitted', request_id = :req_id, updated_at = NOW()
                WHERE chat_id = :cid
            """), {"req_id": new_id, "cid": clean_chat_id})
            db.commit()

        return {"status": "success", "id": new_id, "message": "Заявка принята. Мы свяжемся с вами."}
    except Exception as e:
        db.rollback()
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


@public_router.post("/cdek-webhook")
def cdek_webhook(payload: dict, db: Session = Depends(get_db)):
    """Webhook от СДЭК: обновление статуса доставки."""
    attributes = payload.get("attributes", {})
    cdek_number = attributes.get("cdek_number", "")
    status_code = attributes.get("status_code", "")

    if not cdek_number:
        return {"ok": True}

    # Статус DELIVERED — клиент получил посылку
    if status_code in ("DELIVERED", "ACCEPTED_AT_PICK_UP_POINT"):
        db.execute(text("""
            UPDATE reshipment_requests
            SET status = 'delivered', confirmed_at = NOW()
            WHERE cdek_number = :num AND status = 'shipped'
        """), {"num": cdek_number})
        db.commit()

    return {"ok": True}


# ── Внутренние эндпоинты ──────────────────────────────────────────────────────

@router.get("/requests")
def get_requests(status: Optional[str] = Query(None), db: Session = Depends(get_db)):
    where = "WHERE 1=1"
    params: dict = {}
    if status:
        where += " AND status = :status"
        params["status"] = status

    rows = db.execute(text(f"""
        SELECT id, created_at, updated_at, customer_name, customer_email, customer_phone,
               order_number, product_category, problem_type, items_to_send,
               shipping_address, address_postal, address_region,
               address_city, address_street, address_house,
               client_pvz_code, client_pvz_address, client_pvz_city,
               photo_files, status,
               matched_srid, match_notes, moderator_comment, processed_by, processed_at,
               rejected_by, rejection_reason,
               track_number, shipping_cost, cdek_uuid, cdek_number, cdek_cost,
               shipped_at, confirmed_at, review_requested,
               wb_chat_id, wb_nm_id, wb_client_id, delivery_method
        FROM reshipment_requests {where}
        ORDER BY created_at DESC
    """), params).mappings().all()

    return {"status": "success", "count": len(rows), "data": [dict(r) for r in rows]}


@router.get("/requests/{req_id}")
def get_request(req_id: int, db: Session = Depends(get_db)):
    row = db.execute(text("SELECT * FROM reshipment_requests WHERE id = :id"), {"id": req_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return {"status": "success", "data": dict(row)}


@router.get("/requests/{req_id}/cost")
def get_shipping_cost(req_id: int, db: Session = Depends(get_db)):
    """Рассчитать стоимость доставки через СДЭК (для показа КС при одобрении)."""
    row = db.execute(text("""
        SELECT address_city, client_pvz_city, cdek_cost FROM reshipment_requests WHERE id = :id
    """), {"id": req_id}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    if row["cdek_cost"]:
        return {"status": "success", "cost": float(row["cdek_cost"]), "cached": True}

    city = row["address_city"] or row["client_pvz_city"]
    if not city:
        return {"status": "no_city", "cost": None, "message": "Город клиента не указан"}

    try:
        from ..cdek import calculate_cost_by_city
        cost = calculate_cost_by_city(city)
        return {"status": "success", "cost": cost, "city": city, "cached": False}
    except Exception as e:
        return {"status": "error", "cost": None, "message": str(e)}


@router.post("/requests/{req_id}/approve")
def approve_request(req_id: int, payload: ApproveRequest, db: Session = Depends(get_db)):
    row = _check_exists(req_id, db)

    # Рассчитываем стоимость СДЭК по городу клиента
    cost = None
    city = row.get("address_city") or row.get("client_pvz_city")
    if city:
        try:
            from ..cdek import calculate_cost_by_city
            cost = calculate_cost_by_city(city)
        except Exception:
            pass  # не блокируем одобрение если СДЭК недоступен

    db.execute(text("""
        UPDATE reshipment_requests SET
            status = 'approved', matched_srid = :srid, match_notes = :notes,
            moderator_comment = :comment, processed_by = :by, processed_at = NOW(),
            cdek_cost = COALESCE(:cost, cdek_cost)
        WHERE id = :id
    """), {
        "srid": payload.matched_srid,
        "notes": payload.match_notes,
        "comment": payload.moderator_comment,
        "by": payload.processed_by,
        "cost": cost,
        "id": req_id,
    })
    db.commit()

    result = {"status": "success", "message": f"Заявка #{req_id} одобрена"}
    if cost is not None:
        result["cdek_cost"] = cost
    return result


@router.post("/requests/{req_id}/reject")
def reject_request(req_id: int, payload: RejectRequest, db: Session = Depends(get_db)):
    """Отклонение заявки менеджером КС (без уведомления клиенту)."""
    _check_exists(req_id, db)
    db.execute(text("""
        UPDATE reshipment_requests SET
            status = 'rejected', moderator_comment = :comment,
            processed_by = :by, processed_at = NOW(), rejected_by = 'cs'
        WHERE id = :id
    """), {"comment": payload.moderator_comment, "by": payload.processed_by, "id": req_id})
    db.commit()
    return {"status": "success", "message": f"Заявка #{req_id} отклонена"}


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

    return {
        "status":        "success",
        "message":       f"Заявка #{req_id} отклонена",
        "email_sent":    email_sent,
        "wb_notified":   wb_notified,
        "customer_phone": row.get("customer_phone"),
        "customer_name":  row.get("customer_name"),
    }


@router.post("/requests/{req_id}/warehouse-confirm")
def warehouse_confirm(req_id: int, processed_by: Optional[str] = None, db: Session = Depends(get_db)):
    """Подтверждение одиночной заявки складом — создаёт заказ в СДЭК."""
    return _do_warehouse_confirm(req_id, processed_by, db)


@router.post("/requests/warehouse-confirm-bulk")
def warehouse_confirm_bulk(payload: BulkConfirmRequest, db: Session = Depends(get_db)):
    """Массовое подтверждение заявок складом — создаёт заказы в СДЭК."""
    results = []
    for req_id in payload.ids:
        try:
            r = _do_warehouse_confirm(req_id, payload.processed_by, db)
            results.append({"id": req_id, "ok": True, "cdek_number": r.get("cdek_number", "")})
        except HTTPException as e:
            results.append({"id": req_id, "ok": False, "error": e.detail})
        except Exception as e:
            results.append({"id": req_id, "ok": False, "error": str(e)})
    return {"status": "success", "results": results}


def _do_warehouse_confirm(req_id: int, processed_by: Optional[str], db: Session) -> dict:
    """Внутренняя функция: подтвердить заявку и создать заказ в СДЭК."""
    row = _check_exists(req_id, db)

    if row["status"] != "approved":
        raise HTTPException(status_code=400, detail="Заявка должна быть в статусе 'одобрена'")

    # Определяем город и находим ближайший ПВЗ автоматически
    city = row.get("address_city") or row.get("client_pvz_city")
    if not city:
        raise HTTPException(status_code=400, detail="У заявки не указан город клиента — невозможно найти ПВЗ СДЭК")

    try:
        from ..cdek import create_order, calculate_cost_by_city, find_nearest_pvz
        pvz_code = row.get("client_pvz_code") or find_nearest_pvz(city)
        if not pvz_code:
            raise HTTPException(status_code=502, detail=f"Не найдены ПВЗ СДЭК в городе {city}")

        cdek_result = create_order(
            req_id=req_id,
            customer_name=row["customer_name"],
            customer_phone=row["customer_phone"],
            to_pvz_code=pvz_code,
            items_description=row.get("items_to_send", "Комплектующие"),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Ошибка СДЭК API: {e}")

    # Пересчитываем стоимость если не сохранена
    cost = row.get("cdek_cost")
    if not cost:
        try:
            cost = calculate_cost_by_city(city)
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
            client_pvz_code = COALESCE(client_pvz_code, :pvz),
            track_number = COALESCE(:cdek_num, track_number),
            shipped_at = NOW(),
            confirmation_token = :token,
            processed_by = COALESCE(:by, processed_by)
        WHERE id = :id
    """), {
        "uuid":    cdek_result["uuid"],
        "cdek_num": cdek_result["cdek_number"] or None,
        "cost":    cost,
        "pvz":     pvz_code,
        "token":   token,
        "by":      processed_by,
        "id":      req_id,
    })
    db.commit()

    confirm_url = f"https://cxvo.ru/reshipment/confirm/{token}"

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

    return {
        "status":           "success",
        "message":          f"Заказ СДЭК создан для заявки #{req_id}",
        "cdek_number":      cdek_result["cdek_number"],
        "cdek_uuid":        cdek_result["uuid"],
        "cdek_cost":        cost,
        "pvz_code":         pvz_code,
        "confirmation_url": confirm_url,
        "wb_notified":      wb_notified,
    }


@router.post("/requests/{req_id}/ship-yandex")
def ship_yandex(req_id: int, payload: ShipYandexRequest, db: Session = Depends(get_db)):
    """Отправка через Яндекс Доставку — ручной ввод трека и стоимости."""
    _check_exists(req_id, db)
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
    confirm_url = f"https://cxvo.ru/reshipment/confirm/{token}"
    return {
        "status": "success",
        "message": f"Заявка #{req_id} отправлена через Яндекс",
        "confirmation_url": confirm_url,
    }


@router.post("/requests/{req_id}/request-review")
def request_review(req_id: int, db: Session = Depends(get_db)):
    _check_exists(req_id, db)
    db.execute(text("UPDATE reshipment_requests SET review_requested = TRUE WHERE id = :id"), {"id": req_id})
    db.commit()
    return {"status": "success"}


@router.post("/register-cdek-webhook")
def register_cdek_webhook(db: Session = Depends(get_db)):
    """Регистрирует webhook в СДЭК (вызвать один раз после деплоя)."""
    try:
        from ..cdek import register_webhook
        result = register_webhook("https://cxvo.ru/api/v1/reshipment/cdek-webhook")
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT status, COUNT(*) as cnt,
               COALESCE(SUM(cdek_cost), SUM(shipping_cost), 0) as total_cost
        FROM reshipment_requests GROUP BY status
    """)).mappings().all()
    return {"status": "success", "data": [dict(r) for r in rows]}


# ── Хелпер ───────────────────────────────────────────────────────────────────

def _check_exists(req_id: int, db: Session) -> dict:
    row = db.execute(
        text("SELECT * FROM reshipment_requests WHERE id = :id"), {"id": req_id}
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return dict(row)
