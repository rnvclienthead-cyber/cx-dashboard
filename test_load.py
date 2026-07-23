import sys
import os
import pandas as pd

# Подключаем пути проекта
sys.path.append('/root/cx-dashboard')

print("\n" + "="*60)
print("   ЗАПУСК СКРИПТА ДЕБАГА ВНУТРЕННЕЙ ЛОГИКИ BACKEND")
print("="*60)

try:
    from backend.database import SessionLocal
    from backend.routers.ppm import load_hybrid_data
    print("✅ Импорт функции load_hybrid_data прошёл успешно.")
except Exception as e:
    print("❌ КРИТИЧЕСКАЯ ОШИБКА ИМПОРТА:", e)
    sys.exit(1)

db = SessionLocal()
try:
    print("\nВызываем функцию load_hybrid_data(db)...")
    df = load_hybrid_data(db)
    
    print("\n--- РЕЗУЛЬТАТЫ АНАЛИЗА ДАТАФРЕЙМА ---")
    print("1. Все доступные колонки в ответе:")
    print(df.columns.tolist())
    
    print(f"\n2. Общее количество строк в выгрузке: {len(df)}")
    
    # Ищем наш тестовый артикул
    test_sku = 'BO-White-2'
    df_filtered = df[df['Артикул продавца'] == test_sku]
    
    print(f"\n3. Анализ артикула [{test_sku}]:")
    print(f"   Найдено строк: {len(df_filtered)}")
    
    if not df_filtered.empty:
        first_row = df_filtered.iloc[0].to_dict()
        print("\n4. Содержимое ПЕРВОЙ строки для этого артикула:")
        for key, value in first_row.items():
            print(f"   ➔ [{key}]: {value}")
    else:
        print(f"   ❌ Ошибка: В финальном датафрейме нет артикула {test_sku}!")

except Exception as err:
    print("\n❌ ПРОИЗОШЕЛ КРАШ ПРИ ВЫПОЛНЕНИИ ФУНКЦИИ:")
    import traceback
    traceback.print_exc()
finally:
    db.close()

print("\n" + "="*60)
