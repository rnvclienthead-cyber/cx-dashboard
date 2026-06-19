import sys
import os
from sqlalchemy import text

# Подключаем твою базу данных
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from backend.database import SessionLocal

db = SessionLocal()

print("\n=== СТРУКТУРА ТАБЛИЦ ===")
try:
    query = text("""
    SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name IN (
        'wb_assortment', 'wb_claims', 'wb_orders', 'wb_logistics', 
        'wb_invoices', 'wb_ratings', 'wb_ratings_raw', 'system_logs', 'view_cx_dashboard'
      )
    ORDER BY table_name, ordinal_position;
    """)
    for row in db.execute(query).fetchall():
        print(f"{row[0]} | {row[1]} | {row[2]}")
except Exception as e:
    print(f"Ошибка: {e}")

db.close()
print("\nГотово!")
