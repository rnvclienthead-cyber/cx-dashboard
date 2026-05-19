# backend/database.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Берем URL базы данных из переменных окружения
DB_URL = os.getenv("DB_URL") 

# Создаем движок (engine)
engine = create_engine(DB_URL)

# Фабрика сессий для работы с БД
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Базовый класс для будущих ORM-моделей (если понадобятся)
Base = declarative_base()

# Зависимость для получения сессии БД в маршрутах FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
