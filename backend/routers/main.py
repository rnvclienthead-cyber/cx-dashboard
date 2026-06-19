from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
from .database import get_db
from .routers import claims, ai, system, production, ppm, ratings, auth, finances, voc, dashboard, ym, reshipment

# Инициализируем приложение
app = FastAPI(
    title="CX Dashboard API",
    description="API для работы с рекламациями и аналитикой",
    version="1.0.0"
)

# Настройка CORS для стабильного общения фронтенда и бэкенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Подключаем маршруты к серверу
app.include_router(claims.router)
app.include_router(ai.router)
app.include_router(system.router)
app.include_router(production.router)  
app.include_router(ppm.router)         
app.include_router(ratings.router)
app.include_router(auth.router)
app.include_router(finances.router)
app.include_router(voc.router)
app.include_router(dashboard.router)
app.include_router(ym.router)
app.include_router(reshipment.public_router)
app.include_router(reshipment.router)
app.mount("/uploads", StaticFiles(directory="/root/cx-dashboard/uploads"), name="uploads")

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