import json
import time
import pandas as pd
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from ..database import get_db

router = APIRouter(prefix="/api/v1/analytics", tags=["Production Analytics / Отчет производства"])

CATEGORIES = {
    1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
    4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
    7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
    10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
    13: "Следы использования / Б/У"
}

# Локальный оперативный кэш
_PROD_CACHE = {}

def get_from_cache(key: str, ttl: int = 60):
    now = time.time()
    if key in _PROD_CACHE:
        data, ts = _PROD_CACHE[key]
        if now - ts < ttl:
            print(f"⚡ [PRODUCTION CACHE] Данные '{key}' выданы из памяти VPS")
            return data
    return None

def save_to_cache(key: str, data: any):
    _PROD_CACHE[key] = (data, time.time())

def load_hybrid_data(db: Session) -> pd.DataFrame:
    query = text("""
        SELECT v.*, COALESCE(inv.invoice_num, 'Не указан') AS "Инвойс"
        FROM view_cx_dashboard v
        LEFT JOIN wb_invoices inv 
            ON TRIM(v."Номер поставки") = inv.supply_id 
            AND TRIM(v."Артикул продавца") = inv.supplier_article
    """)
    try:
        with db.bind.connect() as conn:
            df = pd.read_sql(query, conn)
        if df.empty: return df
        date_col = next((c for c in df.columns if 'оформления заявки' in str(c).lower()), None)
        if date_col: 
            df['Дата_ДТ'] = pd.to_datetime(df[date_col], dayfirst=True, errors='coerce')
        return df
    except Exception as e:
        print(f"❌ Ошибка загрузки hybrid_data: {e}")
        return pd.DataFrame()

@router.get("/production-claims")
def get_production_claims(db: Session = Depends(get_db)):
    cached = get_from_cache("production-claims")
    if cached: return cached

    try:
        query = text("""
            SELECT v.*, COALESCE(inv.invoice_num, 'Не указан') AS "Инвойс"
            FROM view_cx_dashboard v
            LEFT JOIN wb_invoices inv 
                ON TRIM(v."Номер поставки") = inv.supply_id 
                AND TRIM(v."Артикул продавца") = inv.supplier_article
        """)
        
        with db.bind.connect() as conn:
            df = pd.read_sql(query, conn)
            
        if df.empty: return {"status": "success", "data": []}
        
        valid_statuses = ['одобрено', '2', '2.0', 'да', 'true']
        df = df[
            df['Решение по возврату покупателю'].astype(str).str.strip().str.lower().isin(valid_statuses) |
            df['Статус товара'].astype(str).str.strip().str.lower().isin(valid_statuses)
        ]
        df['Артикул продавца'] = df['Артикул продавца'].astype(str).str.strip()
        df = df[~df['Артикул продавца'].str.lower().isin(['nan', 'none', '', 'null'])]
        
        df = df.replace({np.nan: None})
        res = {"status": "success", "data": json.loads(df.to_json(orient="records", date_format="iso", force_ascii=False))}
        save_to_cache("production-claims", res)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sku-trend/{sku}")
def get_sku_trend(sku: str, db: Session = Depends(get_db)):
    try:
        df_sys = load_hybrid_data(db)
        df_sys = df_sys[df_sys['Артикул продавца'] == sku]
        trend_data = []
        
        if not df_sys.empty:
            df_sys['Месяц'] = df_sys['Дата_ДТ'].dt.to_period('M').dt.to_timestamp()
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