import asyncio
import os
from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text
from contextlib import asynccontextmanager
from .database import get_db, engine
from .routers import claims, ai, system, production, ppm, ratings, auth, finances, voc, dashboard, ym, reshipment, roles
from . import wb_chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token VARCHAR(128) PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used BOOLEAN DEFAULT FALSE
            )
        """))
        # WB чат: таблица ожидающих заявок
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wb_chat_pending (
                id          SERIAL PRIMARY KEY,
                chat_id     VARCHAR(100) UNIQUE NOT NULL,
                reply_sign  TEXT,
                client_name VARCHAR(255),
                client_id   VARCHAR(100),
                nm_id       BIGINT,
                rid         VARCHAR(100),
                status      VARCHAR(50) DEFAULT 'waiting_form',
                request_id  INTEGER,
                created_at  TIMESTAMP DEFAULT NOW(),
                updated_at  TIMESTAMP DEFAULT NOW()
            )
        """))
        # WB чат: новые колонки в заявках доотправки
        conn.execute(text("""
            ALTER TABLE reshipment_requests
                ADD COLUMN IF NOT EXISTS wb_chat_id     VARCHAR(100),
                ADD COLUMN IF NOT EXISTS wb_reply_sign  TEXT,
                ADD COLUMN IF NOT EXISTS wb_nm_id       BIGINT,
                ADD COLUMN IF NOT EXISTS wb_client_id   VARCHAR(100)
        """))
        conn.commit()

    # Инициализируем курсор WB событий (пропускаем старые)
    wb_chat.init_event_cursor()

    # Запускаем WB чат воркер
    wb_task = asyncio.create_task(wb_chat.start_worker(get_db))

    yield

    wb_task.cancel()
    try:
        await wb_task
    except asyncio.CancelledError:
        pass


# Инициализируем приложение
app = FastAPI(
    title="CX Dashboard API",
    description="API для работы с рекламациями и аналитикой",
    version="1.0.0",
    lifespan=lifespan
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
app.include_router(roles.router)
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