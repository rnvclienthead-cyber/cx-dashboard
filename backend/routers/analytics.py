import io
import json
import time
import pandas as pd
import numpy as np
import openpyxl
import requests
from openpyxl.drawing.image import Image as OpenpyxlImage
from openpyxl.styles import Alignment
from PIL import Image as PILImage
from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Dict, Any, List

from ..database import get_db

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics / Аналитика и Отчеты"])

CATEGORIES = {
    1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
    4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
    7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
    10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
    13: "Следы использования / Б/У"
}

# --- СИСТЕМА ИН-МЕМОРИ КЭШИРОВАНИЯ (АНАЛОГ СТРИМЛИТА) ---
_GLOBAL_CACHE = {}

def get_from_cache(key: str, ttl_seconds: int = 120):
    now = time.time()
    if key in _GLOBAL_CACHE:
        data, timestamp = _GLOBAL_CACHE[key]
        if now - timestamp < ttl_seconds:
            print(f"⚡ [КЭШ ЗАДЕЙСТВОВАН] Данные для '{key}' отданы из ОЗУ бэкенда за 0.001 сек.")
            return data
    return None

def save_to_cache(key: str, data: any):
    _GLOBAL_CACHE[key] = (data, time.time())


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

ClaimExportRequest.model_rebuild()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ С ПОЭТАПНЫМ ТАЙМИНГОМ ---
def load_hybrid_data(db: Session) -> pd.DataFrame:
    print("\n⏱️ [Этап 1.1] Запуск load_hybrid_data...")
    t_start = time.perf_counter()
    
    query = text("""
        SELECT 
            v."SRID", v."Дата и время оформления заявки на возврат", v."Дата заказа", 
            v."Артикул продавца", v."Комментарий покупателя", v."Решение по возврату покупателю", v."Статус товара",
            v."1", v."2", v."3", v."4", v."5", v."6", v."7", v."8", v."9", v."10", v."11", v."12", v."13",
            v."Корректировка", v."Номер поставки",
            COALESCE(inv.invoice_num, 'Не указан') AS "Инвойс"
        FROM view_cx_dashboard v
        LEFT JOIN wb_invoices inv 
            ON TRIM(v."Номер поставки") = inv.supply_id 
            AND TRIM(v."Артикул продавца") = inv.supplier_article
    """)
    try:
        print("🔗 Подключение к пулу Supabase...")
        with db.bind.connect() as connection:
            print("📡 SQL запрос отправлен во Франкфурт, ожидание скачивания строк...")
            df = pd.read_sql(query, connection)
            
        t_sql = time.perf_counter() - t_start
        print(f"📥 Скачано строк возвратов: {len(df)}. Время загрузки сети: {t_sql:.4f} сек.")
        
        if df.empty: return df
        date_col = next((c for c in df.columns if 'оформления заявки' in str(c).lower()), None)
        if date_col: 
            df['Дата_ДТ'] = pd.to_datetime(df[date_col], dayfirst=True, errors='coerce')
        return df
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА НА ЭТАПЕ SQL СКАЧИВАНИЯ: {e}")
        return pd.DataFrame()

def load_orders_data(db: Session) -> pd.DataFrame:
    print("\n⏱️ [Этап 1.2] Запуск load_orders_data...")
    t_start = time.perf_counter()
    query = text("""
        SELECT 
            TRIM(supplier_article) AS "Артикул продавца", 
            DATE_TRUNC('month', dt) AS "Месяц_ДТ", 
            COUNT(*) AS "Чистые_заказы"
        FROM wb_orders 
        WHERE cancel_dt IS NULL 
        GROUP BY 1, 2
    """)
    try: 
        with db.bind.connect() as connection:
            df = pd.read_sql(query, connection)
        t_sql = time.perf_counter() - t_start
        print(f"📥 Скачано строк агрегации заказов: {len(df)}. Время загрузки сети: {t_sql:.4f} сек.")
        
        if not df.empty:
            df['Месяц_ДТ'] = pd.to_datetime(df['Месяц_ДТ'])
        return df
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА НА ЭТАПЕ ЗАКАЗОВ: {e}")
        return pd.DataFrame()

# --- РОУТЫ ---

@router.get("/production-claims")
def get_production_claims(db: Session = Depends(get_db)):
    # Проверяем кэш
    cached = get_from_cache("production-claims")
    if cached: return cached

    print("\n🚨 === ЗАПУСК ЭНДПОИНТА /production-claims ===")
    t_total_start = time.perf_counter()
    try:
        query = text("""
            SELECT 
                v."SRID", v."Дата и время оформления заявки на возврат", v."Дата заказа", 
                v."Артикул продавца", v."Комментарий покупателя", v."Решение по возврату покупателю", v."Статус товара",
                v."1", v."2", v."3", v."4", v."5", v."6", v."7", v."8", v."9", v."10", v."11", v."12", v."13",
                v."Корректировка", v."Номер поставки",
                COALESCE(inv.invoice_num, 'Не указан') AS "Инвойс"
            FROM view_cx_dashboard v
            LEFT JOIN wb_invoices inv 
                ON TRIM(v."Номер поставки") = inv.supply_id 
                AND TRIM(v."Артикул продавца") = inv.supplier_article
        """)
        
        print("⏳ Шаг 1: Чтение view_cx_dashboard...")
        t_step = time.perf_counter()
        with db.bind.connect() as connection:
            df = pd.read_sql(query, connection)
        print(f"✅ Шаг 1 завершен за {time.perf_counter() - t_step:.4f} сек. Загружено {len(df)} строк.")
        
        print("⏳ Шаг 2: Фильтрация и очистка в Pandas...")
        t_step = time.perf_counter()
        valid_statuses = ['одобрено', '2', '2.0', 'да', 'true']
        df = df[
            df['Решение по возврату покупателю'].astype(str).str.strip().str.lower().isin(valid_statuses) |
            df['Статус товара'].astype(str).str.strip().str.lower().isin(valid_statuses)
        ]
        df['Артикул продавца'] = df['Артикул продавца'].astype(str).str.strip()
        df = df[~df['Артикул продавца'].str.lower().isin(['nan', 'none', '', 'null'])]
        df['Номер поставки_ОРИГИНАЛ'] = df['Номер поставки'].astype(str).replace(['nan', 'None', ''], 'Не указан').str.strip()
        df = df.replace({np.nan: None})
        print(f"✅ Шаг 2 завершен за {time.perf_counter() - t_step:.4f} сек. Осталось {len(df)} одобренных строк.")
        
        print("⏳ Шаг 3: Сериализация DataFrame в JSON строку...")
        t_step = time.perf_counter()
        json_str = df.to_json(orient="records", date_format="iso", force_ascii=False)
        response_data = {"status": "success", "data": json.loads(json_str)}
        print(f"✅ Шаг 3 завершен за {time.perf_counter() - t_step:.4f} сек.")
        
        print(f"🎉 ИТОГОП ОПЕРАЦИЯ ЗАВЕРШЕНА ЗА: {time.perf_counter() - t_total_start:.4f} сек.")
        
        # Сохраняем результат в кэш
        save_to_cache("production-claims", response_data)
        return response_data
    except Exception as e:
        print(f"💥 ПОЛНЫЙ КРАШ ЭНДПОИНТА: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка бэкенда production-claims: {str(e)}")

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
                        
        with db.bind.connect() as connection:
            hist = pd.read_sql(text("SELECT month_date, defects FROM historical_ppm WHERE article = :sku"), connection, params={"sku": sku})
            
        if not hist.empty:
            hist['Месяц'] = pd.to_datetime(hist['month_date']).dt.to_period('M').dt.to_timestamp()
            hist_grouped = hist.groupby('Месяц')['defects'].sum().reset_index(name='Количество')
            hist_grouped['Источник'] = "Общий брак"
            trend_data.extend(hist_grouped.to_dict('records'))

        for row in trend_data: 
            row['Месяц'] = row['Месяц'].strftime('%Y-%m-%d')
            
        return {"status": "success", "data": trend_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка тренда SKU: {str(e)}")

@router.get("/ppm-dataset")
def get_ppm_dataset(db: Session = Depends(get_db)):
    # Проверяем кэш
    cached = get_from_cache("ppm-dataset")
    if cached: return cached

    print("\n🚨 === ЗАПУСК ТЯЖЕЛОГО РАСЧЕТА /ppm-dataset ===")
    t_total_start = time.perf_counter()
    try:
        df_sys = load_hybrid_data(db)
        df_orders_sys = load_orders_data(db)
        
        print("⏳ Шаг 1: Загрузка истории и ABC классификации...")
        t_step = time.perf_counter()
        with db.bind.connect() as connection:
            df_hist = pd.read_sql(text("SELECT article as \"Артикул\", month_date as \"Месяц_ДТ\", defects as \"Брак\", orders as \"Заказы\", source as \"Source\" FROM historical_ppm"), connection)
            df_abc = pd.read_sql(text("SELECT article as \"Артикул\", class_abc as \"ABC_Группа\", class_xyz as \"Класс XYZ\" FROM product_classification"), connection)
        print(f"✅ Шаг 1 завершен за {time.perf_counter() - t_step:.4f} сек.")
            
        if not df_hist.empty: 
            df_hist['Месяц_ДТ'] = pd.to_datetime(df_hist['Месяц_ДТ'])
        
        if not df_orders_sys.empty and not df_sys.empty:
            print("⏳ Шаг 2: Математический обсчет векторов и группировка PPM в Pandas...")
            t_step = time.perf_counter()
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
            print(f"✅ Шаг 2 (аналитика Pandas) завершен за {time.perf_counter() - t_step:.4f} сек.")
            
            response_data = {"status": "success", "data": df_total.to_dict(orient="records")}
            print(f"🎉 ИТОГОП ОБСЧЕТ PPM ЗАВЕРШЕН ЗА: {time.perf_counter() - t_total_start:.4f} сек.")
            
            # Сохраняем в кэш
            save_to_cache("ppm-dataset", response_data)
            return response_data
            
        return {"status": "success", "data": []}
    except Exception as e:
        print(f"💥 ПОЛНЫЙ КРАШ ЕНДПОИНТА PPM: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка расчета датасета PPM: {str(e)}")

@router.post("/export-claim")
def export_claim_excel(payload: ClaimExportRequest = Body(...)):
    template_path = "template_ra.xlsx"
    try:
        wb = openpyxl.load_workbook(template_path)
        sheet = wb.active
    except: 
        raise HTTPException(status_code=500, detail="Шаблон template_ra.xlsx не найден на сервере бэкенда")

    def safe_write(sheet, coord, value):
        try: sheet[coord].value = value
        except:
            for mr in sheet.merged_cells.ranges:
                if coord in mr: sheet[str(mr).split(':')[0]].value = value; break

    data = payload.model_dump()
    safe_write(sheet, 'G1', f"Рекламационный акт № {data['number']}")
    safe_write(sheet, 'C3', data['date'])
    
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")