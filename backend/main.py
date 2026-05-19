# backend/main.py
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
from .database import get_db

# Инициализируем приложение
app = FastAPI(
    title="CX Dashboard API",
    description="API для работы с рекламациями и аналитикой",
    version="1.0.0"
)

# Настройка CORS (разрешаем запросы с любых доменов - потом можно будет сузить до cxvo.ru)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ТЕСТОВЫЕ МАРШРУТЫ ---

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Бэкенд успешно запущен"}

@app.get("/api/v1/health/db")
def check_db(db: Session = Depends(get_db)):
    """Проверка подключения к базе данных"""
    try:
        # Простой запрос для проверки соединения
        result = db.execute(text("SELECT 1")).scalar()
        return {"db_status": "ok", "value": result}
    except Exception as e:
        return {"db_status": "error", "details": str(e)}
