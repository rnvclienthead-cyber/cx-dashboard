import io
import json
import time
import pandas as pd
import numpy as np
import openpyxl
from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Dict, Any, List
from ..database import get_db

router = APIRouter(prefix="/api/v1/analytics", tags=["PPM Analytics / Коэффициенты качества"])

class ClaimExportRequest(BaseModel):
    number: str
    supplier: str
    period: str
    invoice: str
    sku: str
    name: str
    name_cn: str
    defects: int
    ppm_pct: float
    desc_ru: str
    desc_cn: str
    cause_ru: str
    cause_cn: str
    photo_groups: Dict[str, List[str]]
    chart_data: List[Dict[str, Any]] = []

_PPM_CACHE = {}

def get_from_cache(key: str, ttl: int = 60):
    now = time.time()
    if key in _PPM_CACHE:
        data, ts = _PPM_CACHE[key]
        if now - ts < ttl:
            print(f"⚡ [PPM CACHE] Данные '{key}' выданы из памяти VPS")
            return data
    return None

def save_to_cache(key: str, data: any):
    _PPM_CACHE[key] = (data, time.time())

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
    except:
        return pd.DataFrame()

def load_orders_data(db: Session) -> pd.DataFrame:
    query = text("""
        SELECT TRIM(supplier_article) AS "Артикул продавца", 
               DATE_TRUNC('month', dt) AS "Месяц_ДТ", COUNT(*) AS "Чистые_заказы"
        FROM wb_orders WHERE cancel_dt IS NULL GROUP BY 1, 2
    """)
    try: 
        with db.bind.connect() as conn:
            df = pd.read_sql(query, conn)
        if not df.empty:
            df['Месяц_ДТ'] = pd.to_datetime(df['Месяц_ДТ'])
        return df
    except: 
        return pd.DataFrame()

@router.get("/ppm-dataset")
def get_ppm_dataset(db: Session = Depends(get_db)):
    cached = get_from_cache("ppm-dataset")
    if cached: return cached

    try:
        df_sys = load_hybrid_data(db)
        df_orders_sys = load_orders_data(db)
        
        with db.bind.connect() as conn:
            df_hist = pd.read_sql(text("SELECT article as \"Артикул\", month_date as \"Месяц_ДТ\", defects as \"Брак\", orders as \"Заказы\", source as \"Source\" FROM historical_ppm"), conn)
            df_abc = pd.read_sql(text("SELECT article as \"Артикул\", class_abc as \"ABC_Группа\", class_xyz as \"Класс XYZ\" FROM product_classification"), conn)
            
        if not df_hist.empty: 
            df_hist['Месяц_ДТ'] = pd.to_datetime(df_hist['Месяц_ДТ'])
        
        if not df_orders_sys.empty and not df_sys.empty:
            valid_tag_vals = ['1', '1.0', '+', 'true', 'да']
            tag_cols = [str(i) for i in range(1, 14) if str(i) in df_sys.columns]
            if tag_cols:
                df_tags = df_sys[tag_cols].fillna('').astype(str).apply(lambda x: x.str.strip().str.lower())
                df_sys['Размечено'] = df_tags.isin(valid_tag_vals).any(axis=1)
            else:
                df_sys['Размечено'] = False
                
            df_app_sys = df_sys[df_sys['Размечено'] == True].copy()
            if not df_app_sys.empty:
                df_app_sys['Месяц_ДТ'] = df_app_sys['Дата_ДТ'].dt.to_period('M').dt.to_timestamp()
            
            sys_metrics = df_app_sys.groupby(['Артикул продавца', 'Месяц_ДТ']).size().reset_index(name='Брак') if not df_app_sys.empty else pd.DataFrame(columns=['Артикул продавца', 'Месяц_ДТ', 'Брак'])
            sys_metrics = pd.merge(df_orders_sys, sys_metrics, on=['Артикул продавца', 'Месяц_ДТ'], how='left').fillna(0)
            sys_metrics.rename(columns={'Артикул продавца':'Артикул', 'Чистые_заказы':'Заказы'}, inplace=True)
            sys_metrics['Source'] = 'System'

            df_total = pd.concat([df_hist, sys_metrics], ignore_index=True)
            if not df_abc.empty:
                df_total = pd.merge(df_total, df_abc, on='Артикул', how='left')
            
            df_total['ABC_Группа'] = df_total['ABC_Группа'].fillna('C')
            df_total['Класс XYZ'] = df_total['Класс XYZ'].fillna('-')

            months_ru = {1:'Янв', 2:'Фев', 3:'Мар', 4:'Апр', 5:'Май', 6:'Июн', 7:'Июл', 8:'Авг', 9:'Сен', 10:'Окт', 11:'Ноя', 12:'Дек'}
            df_total['Месяц_Стр'] = df_total['Месяц_ДТ'].dt.month.map(months_ru) + " " + df_total['Месяц_ДТ'].dt.year.astype(str)
            df_total['Месяц_ДТ'] = df_total['Месяц_ДТ'].dt.strftime('%Y-%m-%d')
            
            df_total = df_total.replace({np.nan: None})
            res = {"status": "success", "data": df_total.to_dict(orient="records")}
            save_to_cache("ppm-dataset", res)
            return res
            
        return {"status": "success", "data": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/export-claim")
def export_claim_excel(payload: ClaimExportRequest = Body(...)):
    template_path = "template_ra.xlsx"
    try:
        wb = openpyxl.load_workbook(template_path)
        sheet = wb.active
    except: 
        raise HTTPException(status_code=500, detail="Шаблон template_ra.xlsx не найден")
    
    # (Здесь остается твоя стандартная логика заполнения ячеек акта)
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")