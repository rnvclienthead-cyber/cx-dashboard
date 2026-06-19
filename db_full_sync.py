import sys
import time
import pandas as pd
from sqlalchemy import create_engine, text

# =========================================================================
# ⚙️ НАСТРОЙКИ ПОДКЛЮЧЕНИЯ
# =========================================================================
# Источник (Облако Supabase)
URL_SUPABASE = "postgresql://postgres.wdcrihtjabrkzgsxezjb:RDB[r6o&BA0qSlVVGjb-@aws-1-eu-central-1.pooler.supabase.com:6543/postgres"

# Назначение (VPS Beget)
# 💡 Впишите сюда тот же внешний IP-адрес вашего сервера, что и в прошлых скриптах
URL_LOCAL_VPS = "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@72.56.240.22:5432/cx_dashboard"

# Список всех таблиц, которые нужно держать в 100% идентичном состоянии
TABLES_TO_SYNC = [
    "wb_claims",              # Рекламации и ИИ-теги
    "wb_invoices",            # Инвойсы из Google Таблиц
    "wb_assortment",          # Ассортиментная матрица
    "wb_ratings",             # Рейтинги и отзывы (парсер)
    "wb_logistics",           # Логистика и продажи (тяжелая)
    "product_classification", # ABC/XYZ классификация
    "historical_ppm",         # Исторические данные по браку
    "wb_orders"               # Заказы (самая большая, 220к строк)
]

def main():
    print("🚀 ЗАПУСК ТОТАЛЬНОЙ СИНХРОНИЗАЦИИ БАЗ ДАННЫХ")
    print(f"⏱️ Старт: {pd.Timestamp.now().strftime('%H:%M:%S')}\n")
    
    start_time = time.time()
    
    try:
        engine_source = create_engine(URL_SUPABASE)
        engine_target = create_engine(URL_LOCAL_VPS)
    except Exception as e:
        print(f"❌ Ошибка инициализации движков БД: {e}")
        sys.exit(1)
        
    for table in TABLES_TO_SYNC:
        print(f"📦 Обработка таблицы [{table}]...")
        t_start = time.time()
        
        try:
            # Шаг 1: Скачиваем абсолютно все данные из Supabase
            df = pd.read_sql(f"SELECT * FROM {table}", engine_source)
            rows_count = len(df)
            print(f"  📥 Успешно скачано из Supabase: {rows_count} строк")
            
            if rows_count == 0:
                print(f"  ⚠️ Таблица [{table}] пуста в облаке. Пропускаем очистку на VPS.")
                continue
                
            # Шаг 2: Очищаем таблицу на VPS и заливаем новые данные
            # Использование TRUNCATE критически важно — оно сохраняет индексы базы данных!
            with engine_target.begin() as conn:
                conn.execute(text(f"TRUNCATE TABLE {table} CASCADE;"))
                
                # Загружаем данные пачками (chunksize), чтобы не перегружать оперативную память
                df.to_sql(
                    name=table, 
                    con=conn, 
                    if_exists='append', 
                    index=False, 
                    chunksize=10000
                )
                
            t_end = time.time()
            print(f"  ✅ Синхронизировано на VPS за {t_end - t_start:.1f} сек.\n")
            
        except Exception as e:
            print(f"  ❌ Ошибка при синхронизации таблицы {table}: {e}\n")
            continue

    total_time = time.time() - start_time
    print("🏁 ==================================================")
    print(f"🎉 ВСЕ ТАБЛИЦЫ СИНХРОНИЗИРОВАНЫ НА 100%!")
    print(f"Общее время выполнения: {total_time // 60:.0f} мин {total_time % 60:.0f} сек")
    print("=====================================================")

if __name__ == "__main__":
    main()