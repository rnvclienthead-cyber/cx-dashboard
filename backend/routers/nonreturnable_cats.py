"""
Категории ассортимента с невозвратными товарами (правило WB).

По истории чатов (ответ менеджеров «товар невозвратный») 100% случаев —
категория «Сушилки для белья». Список редактируется, не хардкодится навсегда.

Потребитель: ИИ-бот — если тема «возврат» и товар из такой категории,
подставляет черновик темы nevozvratny. Только для mp=wb (на Ozon/ЯМ не применять).
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/v1/nonreturnable-cats", tags=["nonreturnable-cats"])

# По разбору 190 чатов с ответом про невозвратный (2026-07-24).
DEFAULT_ENABLED = ["Сушилки для белья"]


def _norm_art(article: str) -> str:
    return (article or "").strip().lower().replace(" ", "")


def init_nonreturnable_cats(engine):
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_nonreturnable_categories (
                category_1  TEXT PRIMARY KEY,
                enabled     BOOLEAN   DEFAULT TRUE,
                note        TEXT      DEFAULT '',
                updated_at  TIMESTAMP DEFAULT now(),
                updated_by  TEXT      DEFAULT ''
            )
        """))
        empty = conn.execute(text(
            "SELECT count(*) FROM cx_nonreturnable_categories"
        )).scalar()
        if not empty:
            for cat in DEFAULT_ENABLED:
                conn.execute(text("""
                    INSERT INTO cx_nonreturnable_categories
                        (category_1, enabled, note, updated_by)
                    VALUES (:c, TRUE, 'По истории ответов менеджеров', 'system')
                    ON CONFLICT (category_1) DO NOTHING
                """), {"c": cat})
        conn.commit()


def get_category(db: Session, *, article: Optional[str] = None,
                 nm_id=None) -> str:
    """Категория товара из ассортимента (category_1) или логистики."""
    if article:
        row = db.execute(text("""
            SELECT category_1 FROM wb_assortment
            WHERE lower(replace(supplier_article, ' ', '')) = :a
              AND nullif(trim(category_1), '') IS NOT NULL
            LIMIT 1
        """), {"a": _norm_art(article)}).fetchone()
        if row and row[0]:
            return str(row[0]).strip()

    if nm_id is not None:
        try:
            n = int(nm_id)
        except (TypeError, ValueError):
            n = None
        if n is not None:
            # артикул → ассортимент
            art = db.execute(text("""
                SELECT supplier_article FROM wb_claims
                WHERE nm_id = :n AND nullif(trim(supplier_article),'') IS NOT NULL
                LIMIT 1
            """), {"n": n}).fetchone()
            if not art:
                art = db.execute(text("""
                    SELECT supplier_article FROM wb_logistics
                    WHERE nm_id = :n AND nullif(trim(supplier_article),'') IS NOT NULL
                    LIMIT 1
                """), {"n": n}).fetchone()
            if art and art[0]:
                row = db.execute(text("""
                    SELECT category_1 FROM wb_assortment
                    WHERE lower(replace(supplier_article, ' ', '')) = :a
                      AND nullif(trim(category_1), '') IS NOT NULL
                    LIMIT 1
                """), {"a": _norm_art(art[0])}).fetchone()
                if row and row[0]:
                    return str(row[0]).strip()
            row = db.execute(text("""
                SELECT category FROM wb_logistics
                WHERE nm_id = :n AND nullif(trim(category), '') IS NOT NULL
                LIMIT 1
            """), {"n": n}).fetchone()
            if row and row[0]:
                return str(row[0]).strip()
    return ""


def is_nonreturnable(db: Session, *, article: Optional[str] = None,
                     nm_id=None) -> bool:
    cat = get_category(db, article=article, nm_id=nm_id)
    if not cat:
        return False
    row = db.execute(text("""
        SELECT enabled FROM cx_nonreturnable_categories
        WHERE lower(category_1) = lower(:c)
    """), {"c": cat}).fetchone()
    return bool(row and row[0])


def enabled_list(db: Session) -> List[str]:
    rows = db.execute(text("""
        SELECT category_1 FROM cx_nonreturnable_categories
        WHERE enabled ORDER BY category_1
    """)).fetchall()
    return [r[0] for r in rows]


class CatToggle(BaseModel):
    category_1: str
    enabled: bool
    note: str = ""


@router.get("")
def list_cats(db: Session = Depends(get_db),
              current_user=Depends(get_current_user)):
    rows = db.execute(text("""
        SELECT category_1, enabled, note, updated_at, updated_by
        FROM cx_nonreturnable_categories
        ORDER BY enabled DESC, category_1
    """)).mappings().all()
    # подсказка: все категории ассортимента
    all_cats = db.execute(text("""
        SELECT DISTINCT category_1 AS c FROM wb_assortment
        WHERE nullif(trim(category_1),'') IS NOT NULL
        ORDER BY 1
    """)).fetchall()
    return {
        "items": [dict(r) for r in rows],
        "all_assortment_categories": [r[0] for r in all_cats],
    }


@router.post("")
def upsert_cat(body: CatToggle, db: Session = Depends(get_db),
               current_user=Depends(get_current_user)):
    who = (current_user or {}).get("email") or (current_user or {}).get("username") or "user"
    db.execute(text("""
        INSERT INTO cx_nonreturnable_categories (category_1, enabled, note, updated_by, updated_at)
        VALUES (:c, :e, :n, :u, now())
        ON CONFLICT (category_1) DO UPDATE SET
            enabled = EXCLUDED.enabled,
            note = EXCLUDED.note,
            updated_by = EXCLUDED.updated_by,
            updated_at = now()
    """), {"c": body.category_1.strip(), "e": body.enabled,
           "n": body.note or "", "u": who})
    db.commit()
    return {"ok": True}
