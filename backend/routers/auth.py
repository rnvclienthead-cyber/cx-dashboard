from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
import os
from ..database import get_db

# --- НАСТРОЙКИ БЕЗОПАСНОСТИ ---
# СЕКРЕТНЫЙ КЛЮЧ: в идеале его нужно вынести в .env, но пока оставим тут сложную строку
SECRET_KEY = os.getenv("JWT_SECRET", "super-secret-key-12345-change-me-later") 
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 # Токен живет 24 часа

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
router = APIRouter(prefix="/api/auth", tags=["Authentication"])

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def verify_password(plain_password, hashed_password):
    """Сверяет введенный пароль с хэшем из базы"""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    """Шифрует пароль (понадобится, когда будешь создавать юзеров через питон)"""
    return pwd_context.hash(password)

def create_access_token(data: dict):
    """Генерирует JWT токен"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# --- МАРШРУТ АВТОРИЗАЦИИ (ЛОГИН) ---
@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """
    Эндпоинт для входа. Принимает логин (username) и пароль (password).
    Если всё верно — выдает токен и роль.
    """
    # 1. Ищем пользователя в базе Local VPS (используем сырой SQL)
    query = text("SELECT id, username, hashed_password, role FROM users WHERE username = :username")
    user = db.execute(query, {"username": form_data.username}).fetchone()
    
    # 2. Если юзера нет или пароль не совпадает — гоним прочь
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    # 3. Если всё Ок, создаем пропуск (токен) с зашитым именем и ролью
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role}
    )
    
    return {
        "access_token": access_token, 
        "token_type": "bearer",
        "username": user.username,
        "role": user.role
    }

# --- ФУНКЦИЯ ДЛЯ ЗАЩИТЫ ДРУГИХ РОУТОВ ---
def get_current_user(token: str = Depends(oauth2_scheme)):
    """
    Эту функцию мы будем вешать на другие роуты как замок (Depends).
    Она проверяет токен и возвращает роль пользователя.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не удалось проверить учетные данные (Токен устарел или неверен)",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        role: str = payload.get("role")
        if username is None:
            raise credentials_exception
        return {"username": username, "role": role}
    except JWTError:
        raise credentials_exception

class RegisterRequest(BaseModel):
    username: str # Это email
    password: str

@router.post("/register")
def register_user(user_data: RegisterRequest, db: Session = Depends(get_db)):
    # 1. Проверяем, есть ли этот email в белом списке
    check_invite = text("SELECT role FROM allowed_emails WHERE email = :email")
    invite = db.execute(check_invite, {"email": user_data.username}).fetchone()
    
    if not invite:
        raise HTTPException(status_code=403, detail="Этот email не найден в списке приглашенных.")
    
    # 2. Проверяем, не зарегистрирован ли он уже
    check_user = text("SELECT id FROM users WHERE username = :email")
    if db.execute(check_user, {"email": user_data.username}).fetchone():
        raise HTTPException(status_code=400, detail="Пользователь уже зарегистрирован.")
        
    # 3. Регистрируем пользователя
    hashed_pw = get_password_hash(user_data.password)
    insert_user = text("INSERT INTO users (username, hashed_password, role) VALUES (:u, :p, :r)")
    db.execute(insert_user, {"u": user_data.username, "p": hashed_pw, "r": invite.role})
    
    # 4. Удаляем email из списка приглашенных (чтобы ссылку не использовали дважды)
    db.execute(text("DELETE FROM allowed_emails WHERE email = :email"), {"email": user_data.username})
    db.commit()
    
    return {"message": "Успешная регистрация! Теперь вы можете войти."}

# --- ДОБАВЬ ЭТО В КОНЕЦ ФАЙЛА auth.py ---

class RoleUpdate(BaseModel):
    role: str

class InviteCreate(BaseModel):
    email: str
    role: str = "viewer"

# 1. Получить всех пользователей
@router.get("/users")
def get_all_users(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен. Только для администраторов.")
    users = db.execute(text("SELECT id, username, role FROM users ORDER BY id")).fetchall()
    return [{"id": u.id, "email": u.username, "role": u.role} for u in users]

# 2. Изменить роль пользователя
@router.put("/users/{user_id}/role")
def update_user_role(user_id: int, data: RoleUpdate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    db.execute(text("UPDATE users SET role = :r WHERE id = :id"), {"r": data.role, "id": user_id})
    db.commit()
    return {"message": "Роль успешно обновлена"}

# 3. Получить белый список (приглашения)
@router.get("/invites")
def get_invites(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    invites = db.execute(text("SELECT email, role FROM allowed_emails ORDER BY email")).fetchall()
    return [{"email": i.email, "role": i.role} for i in invites]

# 4. Добавить email в белый список
@router.post("/invites")
def add_invite(data: InviteCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    try:
        db.execute(text("INSERT INTO allowed_emails (email, role) VALUES (:e, :r)"), {"e": data.email, "r": data.role})
        db.commit()
        return {"message": f"Email {data.email} добавлен в белый список"}
    except Exception:
        raise HTTPException(status_code=400, detail="Этот email уже есть в списке приглашений")

# 5. Удалить email из белого списка
@router.delete("/invites/{email}")
def delete_invite(email: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    db.execute(text("DELETE FROM allowed_emails WHERE email = :e"), {"e": email})
    db.commit()
    return {"message": "Приглашение удалено"}


# --- СМЕНА И СБРОС ПАРОЛЯ ---

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AdminResetPasswordRequest(BaseModel):
    user_id: int
    new_password: str


@router.post("/change-password")
def change_password(
    data: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="Новый пароль должен быть минимум 6 символов.")

    user = db.execute(
        text("SELECT hashed_password FROM users WHERE username = :u"),
        {"u": current_user["username"]}
    ).fetchone()

    if not user or not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Текущий пароль неверный.")

    hashed = get_password_hash(data.new_password)
    db.execute(text("UPDATE users SET hashed_password = :p WHERE username = :u"),
               {"p": hashed, "u": current_user["username"]})
    db.commit()
    return {"message": "Пароль успешно изменён."}


@router.post("/admin/reset-password")
def admin_reset_password(
    data: AdminResetPasswordRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещён.")
    if len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="Пароль должен быть минимум 6 символов.")

    user = db.execute(text("SELECT id FROM users WHERE id = :id"), {"id": data.user_id}).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден.")

    hashed = get_password_hash(data.new_password)
    db.execute(text("UPDATE users SET hashed_password = :p WHERE id = :id"),
               {"p": hashed, "id": data.user_id})
    db.commit()
    return {"message": "Пароль сброшен."}