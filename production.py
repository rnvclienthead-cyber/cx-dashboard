import json
import time
import pandas as pd
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from ..database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/v1/analytics", tags=["Production Analytics / Отчет производства"], dependencies=[Depends(get_current_user)])

CATEGORIES = {
    1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
    4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
    7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
    10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
    13: "Следы использования / Б/У"
}

_PROD_CACHE = {}

def get_from_cache(key: str, ttl: int = 30):
    now = time.time()
    if key in _PROD_CACHE:
        data, ts = _PROD_CACHE[key]
        if now - ts < ttl:
            return data
    return None

def save_to_cache(key: str, data: any):
    _PROD_CACHE[key] = (data, time.time())

# Маппинг refundStatus ЯМ → читаемый статус (дублируем из ym_worker для автономности)
_YM_STATUS_MAP = {
    "WAITING_FOR_DECISION":  "На рассмотрении",
    "REFUND_IN_PROGRESS":    "Одобрено",
    "REFUNDED":              "Одобрено",
    "WAITING_FOR_RETURN":    "На рассмотрении",
    "RETURNED":              "На рассмотрении",
    "NOT_RETURNED":          "Отказ",
    "FAILED":                "Отказ",
}

# Причины возврата ЯМ (reasonType) → русские метки. Фоллбэк — сырое значение.
_YM_REASON_RU = {
    "BAD_QUALITY":   "Брак / качество",
    "DOES_NOT_FIT":  "Не подошёл",
    "WRONG_ITEM":    "Привезли не то",
    "DELIVERY_FAIL": "Проблема с доставкой",
    "DO_NOT_NEED":   "Передумал / не нужен",
}
# Субпричины (subreasonType) → русские метки.
_YM_SUBREASON_RU = {
    "UNKNOWN":                  "Не указана",
    "WRONG_COLOR":              "Не тот цвет",
    "WRONG_ITEM":               "Прислали не тот товар",
    "INCOMPLETENESS":           "Некомплект",
    "NOT_WORKING":              "Не работает",
    "DAMAGED":                  "Повреждён",
    "DID_NOT_MATCH_DESCRIPTION":"Не соответствует описанию",
    "USER_DID_NOT_LIKE":        "Не понравился",
    "USER_CHANGED_MIND":        "Передумал",
    "WRONG_SIZE":               "Не тот размер",
}

def _ym_reason_ru(code: str) -> str:
    if not code:
        return "Без причины"
    return _YM_REASON_RU.get(code, code)

def _ym_subreason_ru(code: str) -> str:
    if not code or code == "UNKNOWN":
        return ""
    return _YM_SUBREASON_RU.get(code, code)

@router.get("/production-claims")
def get_production_claims(
    platform: str = Query("wb", pattern="^(wb|ym|all)$"),
    db: Session = Depends(get_db),
):
    cache_key = f"production-claims-{platform}"
    cached = get_from_cache(cache_key)
    if cached: return cached

    try:
        all_rows = []

        # ── WB ───────────────────────────────────────────────────────────
        if platform in ("wb", "all"):
            query = text("""
                SELECT
                    v.*,
                    v."Номер поставки" AS "Номер поставки_ОРИГИНАЛ",
                    COALESCE(inv.invoice_num, 'Не указан') AS "Инвойс"
                FROM view_cx_dashboard v
                LEFT JOIN (
                    SELECT DISTINCT ON (supply_id, supplier_article)
                        supply_id, supplier_article, invoice_num
                    FROM wb_invoices
                ) inv
                    ON TRIM(v."Номер поставки") = TRIM(inv.supply_id)
                   AND TRIM(v."Артикул продавца") = TRIM(inv.supplier_article)
            """)
            with db.bind.connect() as conn:
                df = pd.read_sql(query, conn)

            if not df.empty:
                valid = ['одобрено', '2', '2.0', 'да', 'true']
                df = df[
                    df['Решение по возврату покупателю'].astype(str).str.strip().str.lower().isin(valid) |
                    df['Статус товара'].astype(str).str.strip().str.lower().isin(valid)
                ]
                df['Артикул продавца'] = df['Артикул продавца'].astype(str).str.strip()
                df = df[~df['Артикул продавца'].str.lower().isin(['nan', 'none', '', 'null'])]
                date_col = next((c for c in df.columns if 'оформления заявки' in str(c).lower()), None)
                df['claim_date_iso'] = (
                    pd.to_datetime(df[date_col], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d')
                    if date_col
                    else pd.to_datetime(df['Дата заказа'], errors='coerce').dt.strftime('%Y-%m-%d')
                )
                df = df.replace({np.nan: None})
                all_rows.extend(json.loads(df.to_json(orient="records", force_ascii=False)))

        # ── YM ───────────────────────────────────────────────────────────
        # Читаем напрямую из новой схемы ym_returns (v2):
        #   • cat_1..cat_13 — NULL изначально, заполняет ИИ-тегирование
        #   • return_comment — текст покупателя
        #   • photos         — пробел-разделённые URL статичных фото (/static/ym_returns/…)
        if platform in ("ym", "all"):
            with db.bind.connect() as conn:
                # ── Эвристический матч возврат → инвойс ──────────────────────────
                # Алгоритм: берём поставку с тем же SKU на тот же склад,
                # дата которой < даты заказа → берём самую последнюю такую.
                # Форматы supply_id в wb_invoices:
                #   "30725002"     = marketplaceRequestId
                #   "ВРЦ-8299161"  = ВРЦ- + request_id (VDC-родительская)
                # parent_request_id покрывает VDC-дочерние через родителя.
                try:
                    inv_rows = conn.execute(text("""
                        SELECT DISTINCT ON (r.return_id)
                            r.return_id,
                            COALESCE(inv.invoice_num, 'ЯМ') AS invoice_num
                        FROM ym_returns r
                        JOIN ym_orders  o  ON o.order_id = r.order_id
                                          AND o.supplier_article = r.supplier_article
                        JOIN ym_supply_items si ON si.offer_id = r.supplier_article
                        JOIN ym_supplies     s  ON s.request_id = si.request_id
                                               AND s.requested_date < o.created_at
                        LEFT JOIN wb_invoices inv ON (
                            inv.supply_id = s.marketplace_request_id
                            OR inv.supply_id = 'ВРЦ-' || s.request_id::text
                            OR (s.parent_request_id IS NOT NULL
                                AND inv.supply_id = 'ВРЦ-' || s.parent_request_id::text)
                        ) AND inv.marketplace = 'ym'
                        WHERE r.supplier_article IS NOT NULL
                          AND r.supplier_article != ''
                        ORDER BY r.return_id, s.requested_date DESC
                    """)).mappings().all()
                    ym_invoice_map = {row["return_id"]: row["invoice_num"] for row in inv_rows}
                except Exception:
                    ym_invoice_map = {}
                ym_rows = conn.execute(text("""
                    SELECT
                        return_id, order_id, supplier_article,
                        status, status_ru, created_at, updated_at,
                        return_comment, photos,
                        reason_type, subreason_type,
                        cat_1,  cat_2,  cat_3,  cat_4,  cat_5,
                        cat_6,  cat_7,  cat_8,  cat_9,  cat_10,
                        cat_11, cat_12, cat_13
                    FROM ym_returns
                    WHERE supplier_article IS NOT NULL
                      AND supplier_article != ''
                """)).mappings().all()

                # Похожий отзыв по тому же SKU (последний негативный, 1-3★).
                # Жёсткой связи отзыв↔возврат нет — это справочно «по этому SKU».
                fb_rows = conn.execute(text("""
                    SELECT DISTINCT ON (supplier_article)
                        supplier_article, valuation, pro_text, contra_text, comment
                    FROM ym_feedbacks
                    WHERE supplier_article IS NOT NULL AND supplier_article != ''
                      AND valuation IS NOT NULL AND valuation <= 3
                    ORDER BY supplier_article, created_date DESC
                """)).mappings().all()
            fb_by_article = {}
            for fb in fb_rows:
                parts = []
                if fb.get("comment"):     parts.append(fb["comment"].strip())
                if fb.get("contra_text"): parts.append(f"➖ {fb['contra_text'].strip()}")
                if fb.get("pro_text"):    parts.append(f"➕ {fb['pro_text'].strip()}")
                text_joined = "\n".join(p for p in parts if p)
                if text_joined:
                    fb_by_article[fb["supplier_article"]] = {
                        "valuation": fb.get("valuation"),
                        "text": text_joined,
                    }

            for r in ym_rows:
                status_ru = (
                    r.get("status_ru")
                    or _YM_STATUS_MAP.get(r.get("status") or "", "На рассмотрении")
                )
                row = {
                    "Артикул продавца":               r["supplier_article"],
                    "Инвойс":                         ym_invoice_map.get(r["return_id"], "ЯМ"),
                    "claim_date_iso":                  r["created_at"].strftime("%Y-%m-%d") if r["created_at"] else None,
                    "Решение по возврату покупателю":  status_ru,
                    "Статус товара":                   status_ru,
                    "SRID":                            str(r["return_id"]),
                    "Дата заказа":                     r["created_at"].isoformat() if r["created_at"] else None,
                    "Номер поставки":                  "ЯМ",
                    "Комментарий покупателя":          r["return_comment"] or "",
                    # photos уже включены — фронтенд не будет вызывать claim-media
                    "photos":                          r["photos"] or "",
                    "platform":                        "ym",
                    # Причины Маркета (для 2-й карты и обогащения карточки)
                    "reason_type":                     r.get("reason_type"),
                    "subreason_type":                  r.get("subreason_type"),
                    "Причина ЯМ":                      _ym_reason_ru(r.get("reason_type")),
                    "Субпричина ЯМ":                   _ym_subreason_ru(r.get("subreason_type")),
                }
                # Похожий отзыв по этому SKU (если есть)
                fb = fb_by_article.get(r["supplier_article"])
                if fb:
                    row["Похожий отзыв"]       = fb["text"]
                    row["Похожий отзыв оценка"] = fb["valuation"]
                # Читаем cat_1..cat_13 напрямую (ИИ-тегирование заполнит позже)
                for i in range(1, 14):
                    val = r.get(f"cat_{i}")
                    row[str(i)] = 1 if val else 0
                all_rows.append(row)

        res = {"status": "success", "data": all_rows}
        save_to_cache(cache_key, res)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/claim-media/{srid}")
def get_claim_media(srid: str, db: Session = Depends(get_db)):
    """Отдельный быстрый эндпоинт для ленивой загрузки фото и видео (Пункт 7)"""
    query = text("SELECT photos, video_paths FROM wb_claims WHERE srid = :srid")
    try:
        with db.bind.connect() as conn:
            result = conn.execute(query, {"srid": str(srid)}).fetchone()
        if result:
            return {
                "status": "success",
                "photos": result[0] or "",
                "video_paths": result[1] or ""
            }
        return {"status": "success", "photos": "", "video_paths": ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sku-trend/{sku}")
def get_sku_trend(sku: str, db: Session = Depends(get_db)):
    try:
        query = text("""
            SELECT v.*, COALESCE(inv.invoice_num, 'Не указан') AS "Инвойс"
            FROM view_cx_dashboard v
            LEFT JOIN wb_invoices inv 
                ON TRIM(v."Номер поставки") = inv.supply_id 
                AND TRIM(v."Артикул продавца") = inv.supplier_article
            WHERE v."Артикул продавца" = :sku
        """)
        with db.bind.connect() as conn:
            df_sys = pd.read_sql(query, conn, params={"sku": sku})
            
        trend_data = []
        if not df_sys.empty:
            date_col = next((c for c in df_sys.columns if 'оформления заявки' in str(c).lower()), None)
            df_sys['Месяц_ДТ'] = pd.to_datetime(df_sys[date_col], dayfirst=True, errors='coerce') if date_col else pd.to_datetime(df_sys['Дата заказа'], errors='coerce')
            df_sys['Месяц'] = df_sys['Месяц_ДТ'].dt.to_period('M').dt.to_timestamp()
            
            for i in range(1, 14):
                cat_col = str(i)
                if cat_col in df_sys.columns:
                    mask = df_sys[cat_col].fillna('').astype(str).str.strip().str.lower().isin(['1', '1.0', '+', 'true', 'да'])
                    temp = df_sys[mask]
                    if not temp.empty:
                        monthly = temp.groupby('Месяц').size().reset_index(name='Количество')
                        monthly['Источник'] = f"{i}. {CATEGORIES.get(i)}"
                        trend_data.extend(monthly.to_dict('records'))
                        
        with db.bind.connect() as conn:
            hist = pd.read_sql(text("SELECT month_date, defects FROM historical_ppm WHERE article = :sku"), conn, params={"sku": sku})
            
        if not hist.empty:
            hist['Месяц'] = pd.to_datetime(hist['month_date']).dt.to_period('M').dt.to_timestamp()
            hist_grouped = hist.groupby('Месяц')['defects'].sum().reset_index(name='Количество')
            hist_grouped['Источник'] = "Общий брак"
            trend_data.extend(hist_grouped.to_dict('records'))

        for row in trend_data: 
            row['Месяц'] = row['Месяц'].strftime('%Y-%m-%d')
            
        return {"status": "success", "data": trend_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))