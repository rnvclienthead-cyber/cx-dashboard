from passlib.context import CryptContext
from sqlalchemy import create_engine, text

# Подключаемся к твоей локальной базе
URL_LOCAL = "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard"
engine = create_engine(URL_LOCAL)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

print("👑 СОЗДАНИЕ ГЛАВНОГО АДМИНИСТРАТОРА")
email = input("Введите ваш email (он же логин): ").strip()
password = input("Придумайте надежный пароль: ").strip()

hashed_password = pwd_context.hash(password)

try:
    with engine.begin() as conn:
        # Вставляем админа в таблицу users
        conn.execute(text("""
            INSERT INTO users (username, hashed_password, role) 
            VALUES (:u, :p, 'admin')
        """), {"u": email, "p": hashed_password})
    print(f"✅ Успех! Админ {email} создан. Теперь вы можете войти на сайт.")
except Exception as e:
    print(f"❌ Ошибка (возможно такой email уже есть): {e}")
