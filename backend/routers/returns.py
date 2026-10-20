from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..return_scan import build_wb_card, fetch_wb_batch, fetch_wb_claim, lookup_scan
from ..sticker_ocr import MAX_BYTES, parse_sticker_ocr, read_sticker_from_image
from ..qwen_client import QwenError
from ..warehouse_intake import attach_intake, is_linear_sticker, is_sticker_qr, list_today, record_intake

router = APIRouter(
    prefix="/api/return",
    tags=["Returns / Возвраты склад"],
)


QR_SCAN_HINT = "Пробит QR. Нужны чёрные полоски штрихкода сверху или снизу наклейки — не квадрат."


@router.get("/lookup")
def lookup_return(code: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    """
    Ищет возврат по коду с этикетки: SRID, стикер WB, партия, штрихкод Ozon, заказ ЯМ.
    Без авторизации — склад сканирует с телефона.
    В учёт приёмки пишем только линейный штрихкод, не QR.
    """
    if is_sticker_qr(code):
        raise HTTPException(status_code=422, detail=QR_SCAN_HINT)
    found = lookup_scan(db, code)
    if not found:
        if is_linear_sticker(code):
            record_intake(db, code, None)
        raise HTTPException(status_code=404, detail="Возврат не найден")
    if found.get("kind") == "claim" and found.get("item"):
        if is_linear_sticker(code):
            intake = record_intake(db, code, found["item"])
            found["intake"] = intake
            if intake:
                found["item"]["intake"] = intake
                found["item"]["received_at"] = intake.get("received_at")
    return found


@router.get("/intake")
def warehouse_intake_today(
    days: int = Query(1, ge=1, le=31),
    db: Session = Depends(get_db),
):
    """Что пробили на складе. Сканер берёт сегодня, раздел «Приёмка» — несколько дней."""
    return list_today(db, days=days)


@router.get("/income/{income_id}")
def get_returns_by_income(income_id: int, db: Session = Depends(get_db)):
    """
    Список всех претензий из одной партии возврата (по QR логистической наклейки = income_id = gi_id).
    Без авторизации — используется со склада.
    """
    rows = fetch_wb_batch(db, str(income_id))
    if not rows:
        raise HTTPException(status_code=404, detail="Партия возвратов не найдена")

    return {
        "income_id": income_id,
        "count": len(rows),
        "match_by": "income_id",
        "marketplace": "wb",
        "items": [build_wb_card(r, match_by="income_id") for r in rows],
    }


@router.post("/read-sticker")
async def read_sticker_photo(file: UploadFile = File(...)):
    """По фото этикетки достаёт два печатных числа. Камера живая сюда не ходит."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Пустое фото")
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="Фото слишком большое")
    try:
        code = read_sticker_from_image(raw, file.content_type or "")
    except ValueError:
        raise HTTPException(status_code=400, detail="Это не фото")
    except QwenError as exc:
        raise HTTPException(status_code=502, detail="Не удалось прочитать цифры с фото. Введите номер.") from exc
    if not code:
        raise HTTPException(status_code=422, detail="Цифры на фото не нашлись. Введите номер с наклейки.")
    return {"code": parse_sticker_ocr(code) or code}


@router.get("/{srid}")
def get_return_by_srid(srid: str, db: Session = Depends(get_db)):
    """
    Публичная карточка возврата. Сначала точный SRID, иначе тот же поиск, что у сканера.
    """
    row = fetch_wb_claim(db, "LOWER(c.srid) = LOWER(:code)", {"code": srid})
    if row:
        return attach_intake(db, build_wb_card(row, match_by="srid"))

    found = lookup_scan(db, srid)
    if found and found.get("kind") == "claim":
        return attach_intake(db, found["item"])
    if found and found.get("kind") == "batch":
        return found

    raise HTTPException(status_code=404, detail="Возврат не найден")
