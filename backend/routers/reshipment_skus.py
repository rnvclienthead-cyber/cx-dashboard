"""
Управление списком товаров, по которым доступна доотправка деталей.

Правило Рустама (2026-07-22): форма на доотправку отправляется всегда, когда
клиент заинтересован и соглашается, НО только по тем SKU, для которых у нас
физически есть запас деталей. Сейчас это вешалки 2-СБ; модельный ряд будет
расширяться, поэтому список редактируется из интерфейса, а не правится в коде.

Потребители:
  - ИИ-бот чатов: перед тем как предложить доотправку, проверяет is_allowed().
  - Раздел «Доотправки»: вкладка «Товары» для включения/выключения SKU.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/v1/reshipment-skus", tags=["reshipment-skus"])

# Артикулы, по которым доотправка работала на момент запуска функции.
DEFAULT_ENABLED = ["2-СБ-Black", "2-СБ-Green", "2-СБ-Grey", "2-СБ-White"]


def _norm(article: str) -> str:
    """Нормализация артикула — как в cx_kb_articles (lower, без пробелов)."""
    return (article or "").strip().lower().replace(" ", "")


# ── Инициализация ────────────────────────────────────────────────────────────

def init_sku_table(engine):
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cx_reshipment_skus (
                supplier_article TEXT PRIMARY KEY,
                enabled          BOOLEAN     DEFAULT FALSE,
                note             TEXT        DEFAULT '',
                updated_at       TIMESTAMP   DEFAULT now(),
                updated_by       TEXT        DEFAULT ''
            )"""))
        # Засеваем стартовый список только один раз — дальше список живёт в UI.
        empty = conn.execute(text("SELECT count(*) FROM cx_reshipment_skus")).scalar()
        if not empty:
            for art in DEFAULT_ENABLED:
                conn.execute(text("""
                    INSERT INTO cx_reshipment_skus (supplier_article, enabled, note, updated_by)
                    VALUES (:a, TRUE, 'Стартовый список', 'system')
                    ON CONFLICT (supplier_article) DO NOTHING
                """), {"a": art})
        conn.commit()


# ── Проверка для бота ────────────────────────────────────────────────────────

def is_allowed(db: Session, supplier_article: Optional[str]) -> bool:
    """Можно ли предлагать доотправку по этому артикулу."""
    if not supplier_article:
        return False
    row = db.execute(text("""
        SELECT enabled FROM cx_reshipment_skus
        WHERE lower(replace(supplier_article, ' ', '')) = :a
    """), {"a": _norm(supplier_article)}).fetchone()
    return bool(row and row[0])


def allowed_list(db: Session) -> List[str]:
    rows = db.execute(text(
        "SELECT supplier_article FROM cx_reshipment_skus WHERE enabled ORDER BY supplier_article"
    )).fetchall()
    return [r[0] for r in rows]


# ── API ──────────────────────────────────────────────────────────────────────

class SkuToggle(BaseModel):
    supplier_article: str
    enabled: bool
    note: Optional[str] = None


class BulkToggle(BaseModel):
    items: List[SkuToggle]


@router.get("")
def list_skus(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """
    Все артикулы, которые встречаются в отзывах, с отметкой «доотправка разрешена».
    Отсортированы по объёму отзывов — сверху то, что реально продаётся.
    """
    rows = db.execute(text("""
        WITH arts AS (
            SELECT supplier_article, count(*) AS reviews
            FROM wb_feedbacks
            WHERE supplier_article IS NOT NULL AND supplier_article <> ''
            GROUP BY supplier_article
        )
        SELECT
            a.supplier_article,
            a.reviews,
            COALESCE(s.enabled, FALSE)          AS enabled,
            COALESCE(s.note, '')                AS note,
            COALESCE(g.name, '')                AS group_name
        FROM arts a
        LEFT JOIN cx_reshipment_skus s
               ON lower(replace(s.supplier_article, ' ', ''))
                = lower(replace(a.supplier_article, ' ', ''))
        LEFT JOIN cx_kb_articles ka
               ON ka.article = lower(replace(a.supplier_article, ' ', ''))
        LEFT JOIN cx_kb_groups g ON g.id = ka.group_id
        ORDER BY COALESCE(s.enabled, FALSE) DESC, a.reviews DESC
    """)).mappings().all()

    items = [dict(r) for r in rows]
    return {"items": items, "enabled_count": sum(1 for i in items if i["enabled"])}


@router.put("")
def set_sku(payload: SkuToggle, db: Session = Depends(get_db),
            current_user=Depends(get_current_user)):
    db.execute(text("""
        INSERT INTO cx_reshipment_skus (supplier_article, enabled, note, updated_at, updated_by)
        VALUES (:a, :e, COALESCE(:n, ''), now(), :u)
        ON CONFLICT (supplier_article) DO UPDATE
           SET enabled    = EXCLUDED.enabled,
               note       = COALESCE(:n, cx_reshipment_skus.note),
               updated_at = now(),
               updated_by = EXCLUDED.updated_by
    """), {"a": payload.supplier_article, "e": payload.enabled,
           "n": payload.note, "u": current_user["username"]})
    db.commit()
    return {"status": "ok", "supplier_article": payload.supplier_article,
            "enabled": payload.enabled}


@router.post("/bulk")
def set_skus_bulk(payload: BulkToggle, db: Session = Depends(get_db),
                  current_user=Depends(get_current_user)):
    for it in payload.items:
        db.execute(text("""
            INSERT INTO cx_reshipment_skus (supplier_article, enabled, note, updated_at, updated_by)
            VALUES (:a, :e, COALESCE(:n, ''), now(), :u)
            ON CONFLICT (supplier_article) DO UPDATE
               SET enabled    = EXCLUDED.enabled,
                   note       = COALESCE(:n, cx_reshipment_skus.note),
                   updated_at = now(),
                   updated_by = EXCLUDED.updated_by
        """), {"a": it.supplier_article, "e": it.enabled,
               "n": it.note, "u": current_user["username"]})
    db.commit()
    return {"status": "ok", "updated": len(payload.items)}
