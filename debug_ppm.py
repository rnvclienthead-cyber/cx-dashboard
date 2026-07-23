import sys
import os

# Подключаем папки проекта, чтобы взять оригинальные настройки БД
sys.path.append('/root/cx-dashboard')
try:
    from backend.database import engine
except Exception as e:
    print("Не удалось импортировать engine:", e)
    sys.exit(1)

from sqlalchemy import text

print("\n" + "="*50)
print("       СТАРТ ЖЕСТКОЙ ДИАГНОСТИКИ ДАННЫХ PPM")
print("="*50)

TARGET_SKU = 'BO-White-2'

with engine.connect() as conn:
    # 1. Смотрим, как артикул записан в возвратах WB
    print(f"\n1. Ищем '{TARGET_SKU}' в таблице wb_claims (Возвраты)...")
    claims = conn.execute(text(
        "SELECT supplier_article, income_id, COUNT(*) "
        "FROM wb_claims WHERE supplier_article ILIKE :sku GROUP BY 1, 2 LIMIT 5"
    ), {"sku": f"%{TARGET_SKU}%"}).mappings().all()
    
    if not claims:
        print("   ❌ Артикул ВООБЩЕ НЕ НАЙДЕН в wb_claims!")
    for c in claims:
        print(f"   ➔ Артикул в БД: [{c['supplier_article']}] | Поставка (income_id): [{c['income_id']}] | Строк: {c['count']}")

    # 2. Смотрим, как артикул записан в твоей таблице заводов
    print(f"\n2. Ищем '{TARGET_SKU}' в таблице wb_factories (Заводы)...")
    factories = conn.execute(text(
        "SELECT supplier_article, invoice_num, factory_name "
        "FROM wb_factories WHERE supplier_article ILIKE :sku LIMIT 5"
    ), {"sku": f"%{TARGET_SKU}%"}).mappings().all()
    
    if not factories:
        print("   ❌ Артикул ВООБЩЕ НЕ НАЙДЕН в wb_factories!")
    for f in factories:
        print(f"   ➔ Артикул в БД: [{f['supplier_article']}] | Инвойс: [{f['invoice_num']}] | Завод: [{f['factory_name']}]")

    # 3. Смотрим, как артикул записан в контейнерах
    print(f"\n3. Ищем '{TARGET_SKU}' в таблице wb_cogs (Контейнеры)...")
    cogs = conn.execute(text(
        "SELECT supplier_article, invoice_num, container_num, shipment_date "
        "FROM wb_cogs WHERE supplier_article ILIKE :sku LIMIT 5"
    ), {"sku": f"%{TARGET_SKU}%"}).mappings().all()
    
    if not cogs:
        print("   ❌ Артикул ВООБЩЕ НЕ НАЙДЕН в wb_cogs!")
    for cg in cogs:
        print(f"   ➔ Артикул в БД: [{cg['supplier_article']}] | Инвойс: [{cg['invoice_num']}] | Контейнер: [{cg['container_num']}]")

print("\n" + "="*50)
