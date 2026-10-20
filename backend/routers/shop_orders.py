"""Заказы сайта Vidovito: приём из CS-Cart и складской раздел."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import shop_delivery
from .. import shop_orders
from ..database import get_db
from .auth import get_current_user

public_router = APIRouter(prefix="/api/v1/shop/orders", tags=["Shop orders ingest"])
router = APIRouter(prefix="/api/v1/shop/orders", tags=["Shop orders"])


class StatusPayload(BaseModel):
    status: str
    processed_by: str = ""
    comment: str = ""


class TrackPayload(BaseModel):
    track_number: str = ""
    processed_by: str = ""


def _shop_token(x_shop_token: str = Header("", alias="X-Shop-Token")):
    if not shop_delivery.token_ok(x_shop_token):
        raise HTTPException(status_code=403, detail="Нет доступа")


def _who(user: dict) -> str:
    return (user or {}).get("username") or ""


@public_router.post("/ingest")
def ingest_order(payload: dict, db: Session = Depends(get_db), _: None = Depends(_shop_token)):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Ожидался JSON заказа")
    try:
        order = shop_orders.upsert_from_shop(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "data": order}


@router.get("")
@router.get("/")
def list_orders(status: str = "", q: str = "", db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return {"status": "ok", "data": shop_orders.list_orders(db, status=status, q=q)}


@router.get("/stats")
def order_stats(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return {"status": "ok", "data": shop_orders.stats(db)}


@router.get("/ozon-dropoff")
def ozon_dropoff_barcode(current_user: dict = Depends(get_current_user)):
    from .. import ozon_delivery
    info = ozon_delivery.dropoff_barcode_info()
    info["svg"] = ozon_delivery.barcode_svg(info.get("barcode") or "")
    return info


@router.get("/ozon-dropoff/label")
def ozon_dropoff_label(current_user: dict = Depends(get_current_user)):
    from .. import ozon_delivery
    from .reshipment import _build_labels_pdf_80x58
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
        "print_barcode": True,
    }
    buf = _build_labels_pdf_80x58([fake])
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="ozon_dropoff_barcode.pdf"'},
    )


def _labels_for(db: Session, order_id: int, item_id: Optional[int] = None):
    order = shop_orders.get_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    rows = shop_orders.label_rows_for_order(order, item_id=item_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Сначала создайте отправление в службе — без трека этикетки нет")
    shop_orders.persist_ozon_postings(db, order_id, rows)
    from .reshipment import _labels_pdf_response
    return _labels_pdf_response(rows)


@router.get("/{order_id}/status-history")
def order_status_history(order_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if not shop_orders.get_order(db, order_id):
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return {"status": "ok", "data": shop_orders.status_history(db, order_id)}


@router.get("/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    order = shop_orders.get_order(db, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return {"status": "ok", "data": order}


@router.get("/{order_id}/labels")
def order_labels(order_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return _labels_for(db, order_id)


@router.get("/{order_id}/items/{item_id}/labels")
def item_labels(order_id: int, item_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return _labels_for(db, order_id, item_id)


@router.post("/{order_id}/status")
def set_status(order_id: int, payload: StatusPayload, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if (current_user or {}).get("role") != "admin":
        raise HTTPException(status_code=403, detail="Менять статус вручную может только администратор")
    try:
        order = shop_orders.set_status(
            db, order_id, payload.status,
            payload.processed_by or _who(current_user),
            payload.comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "data": order}


@router.post("/{order_id}/items/{item_id}/pack")
def pack_item(order_id: int, item_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    try:
        order = shop_orders.pack_item(db, order_id, item_id, _who(current_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "data": order}


@router.post("/{order_id}/items/{item_id}/create-shipment")
def create_item_shipment(order_id: int, item_id: int, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    try:
        order = shop_orders.create_item_shipment(db, order_id, item_id, _who(current_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:400]) from exc
    return {"status": "ok", "data": order}


@router.post("/{order_id}/items/{item_id}/ship")
def mark_item_shipped(
    order_id: int,
    item_id: int,
    payload: TrackPayload = TrackPayload(),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        order = shop_orders.mark_item_shipped(
            db, order_id, item_id, payload.track_number, payload.processed_by or _who(current_user),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "data": order}
