from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv

load_dotenv("/root/cx-dashboard/.env")

URL_SUPABASE = "postgresql://postgres.wdcrihtjabrkzgsxezjb:RDB[r6o&BA0qSlVVGjb-@aws-1-eu-central-1.pooler.supabase.com:6543/postgres"
URL_LOCAL_VPS = "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"

engine_supabase = create_engine(URL_SUPABASE)
engine_local = create_engine(URL_LOCAL_VPS)

# Удаляем все записи начиная с 18 мая 2026 года
delete_query = text("DELETE FROM wb_ratings WHERE date >= '2026-05-18'")

for name, eng in [("Supabase", engine_supabase), ("Local VPS", engine_local)]:
    try:
        with eng.begin() as conn:
            result = conn.execute(delete_query)
            print(f"✅ Успешно очищено в {name}. Удалено строк: {result.rowcount}")
    except Exception as e:
        print(f"❌ Ошибка в {name}: {e}")
