import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# =========================================================================
# 🎛️ БЭКДОР: УПРАВЛЕНИЕ БАЗАМИ ДАННЫХ
# True  - проект работает через внешнее облако Supabase (Германия)
# False - проект работает через ЛОКАЛЬНЫЙ PostgreSQL на вашем сервере (РФ)
# =========================================================================
USE_SUPABASE = False 

# 1. Конфигурация строк подключения
SUPABASE_URL = "postgresql://postgres.wdcrihtjabrkzgsxezjb:RDB[r6o&BA0qSlVVGjb-@aws-1-eu-central-1.pooler.supabase.com:6543/postgres"
LOCAL_VPS_URL = "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"

# 2. Автоматический выбор движка на основе бэкдора
if USE_SUPABASE:
    print("📡 [DB CONFIG] ВНИМАНИЕ: Активирован бэкдор! Работаем через внешнюю СУБД Supabase.")
    DATABASE_URL = SUPABASE_URL
    # Для облака включаем пул соединений, чтобы не забивать лимиты коннектов
    engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=10, pool_recycle=1800)
else:
    print("⚡ [DB CONFIG] Работаем в продакшн-режиме через ЛОКАЛЬНЫЙ PostgreSQL (Пинг ~0мс).")
    DATABASE_URL = LOCAL_VPS_URL
    engine = create_engine(DATABASE_URL)

# 3. Инициализация сессий SQLAlchemy
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()