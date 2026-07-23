from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from ..database import get_db

router = APIRouter(
    prefix="/api/return",
    tags=["Returns / Возвраты склад"],
)

CATEGORIES = {
    1: "Некомплект: Фурнитура",
    2: "Некомплект: Несущие детали",
    3: "Состояние упаковки",
    4: "Производственный дефект",
    5: "Механические повреждения",
    6: "Инструкция и сборка",
    7: "Хлипкость / Устойчивость",
    8: "Пересорт / Ошибка склада",
    9: "Качество материалов",
    10: "Габариты и Размер",
    11: "Несоответствие описанию",
    12: "Субъективное 'Не подошло'",
    13: "Следы использования / Б/У",
}


def _normalize_photo_url(url: str) -> str:
    url = url.strip()
    # Формат "preview_url|original_url" — берём оригинал
    if "|" in url:
        url = url.split("|")[-1].strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def _build_return_card(row: dict) -> dict:
    raw_photos = (row.get("photos") or "").strip()
    photos = [_normalize_photo_url(u) for u in raw_photos.split() if u.strip()] if raw_photos else []

    active_cats = [CATEGORIES[i] for i in range(1, 14) if row.get(f"cat_{i}")]

    return {
        "srid": row["srid"],
        "claim_id": row["claim_id"],
        "supplier_article": row.get("supplier_article") or "",
        "product_name": row.get("name_ru") or "",
        "nm_id": row.get("nm_id") or "",
        "status": row.get("status") or "",
        "claim_type": row.get("claim_type"),
        "user_comment": row.get("user_comment") or "",
        "claim_date": row["claim_date"].isoformat() if row.get("claim_date") else None,
        "categories": active_cats,
        "photos": photos,
    }


@router.get("/income/{income_id}")
def get_returns_by_income(income_id: int, db: Session = Depends(get_db)):
    """
    Список всех претензий из одной партии возврата (по QR логистической наклейки = income_id = gi_id).
    Без авторизации — используется со склада.
    """
    rows = db.execute(
        text("""
            SELECT c.srid, c.claim_id, c.supplier_article, c.user_comment,
                   c.status, c.claim_type, c.photos, c.nm_id, c.claim_date,
                   c.cat_1, c.cat_2, c.cat_3, c.cat_4, c.cat_5, c.cat_6,
                   c.cat_7, c.cat_8, c.cat_9, c.cat_10, c.cat_11, c.cat_12, c.cat_13,
                   a.name_ru
            FROM wb_claims c
            LEFT JOIN wb_assortment a ON a.supplier_article = c.supplier_article
            WHERE c.income_id = CAST(:income_id AS TEXT)
            ORDER BY c.claim_date DESC
            LIMIT 200
        """),
        {"income_id": income_id},
    ).mappings().all()

    if not rows:
        raise HTTPException(status_code=404, detail="Партия возвратов не найдена")

    return {
        "income_id": income_id,
        "count": len(rows),
        "items": [_build_return_card(dict(r)) for r in rows],
    }


@router.get("/{srid}")
def get_return_by_srid(srid: str, db: Session = Depends(get_db)):
    """
    Публичная карточка возврата по SRID (со штрихкода/QR на наклейке WB).
    Без авторизации — используется со склада.
    """
    row = db.execute(
        text("""
            SELECT c.srid, c.claim_id, c.supplier_article, c.user_comment,
                   c.status, c.claim_type, c.photos, c.nm_id, c.claim_date,
                   c.cat_1, c.cat_2, c.cat_3, c.cat_4, c.cat_5, c.cat_6,
                   c.cat_7, c.cat_8, c.cat_9, c.cat_10, c.cat_11, c.cat_12, c.cat_13,
                   a.name_ru
            FROM wb_claims c
            LEFT JOIN wb_assortment a ON a.supplier_article = c.supplier_article
            WHERE c.srid = :srid
            LIMIT 1
        """),
        {"srid": srid},
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Возврат не найден")

    return _build_return_card(dict(row))
