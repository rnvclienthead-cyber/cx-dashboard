from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import List, Optional
from ..database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/v1/admin/roles", tags=["Roles / Роли"])

# Список всех доступных модулей системы
MODULES = [
    {"key": "dashboard",    "label": "Главный дашборд"},
    {"key": "sync",         "label": "Статус автоматизации"},
    {"key": "ai_tagging",   "label": "ИИ Тегирование"},
    {"key": "moderation",   "label": "Модерация тегов"},
    {"key": "ai_training",  "label": "Обучение ИИ"},
    {"key": "logs",         "label": "Журнал событий"},
    {"key": "production",   "label": "Карта проблем"},
    {"key": "ppm",          "label": "PPM и Акты"},
    {"key": "voc",          "label": "Отзывы и аналитика"},
    {"key": "ratings",      "label": "Рейтинг товаров"},
    {"key": "finances",     "label": "Финансовые потери"},
    {"key": "reshipment",   "label": "Доотправки"},
    {"key": "registry",     "label": "Реестр"},
    {"key": "admin_panel",  "label": "Панель управления"},
]

MARKETPLACES = ["all", "wb", "ym"]


class RoleCreate(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = ""

class PermissionSet(BaseModel):
    permissions: List[dict]   # [{"module": "dashboard", "marketplace": "all"}, ...]


def _admin_only(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Только для администраторов")
    return current_user


@router.get("/modules")
def get_modules():
    return {"modules": MODULES, "marketplaces": MARKETPLACES}


@router.get("")
def get_roles(db: Session = Depends(get_db), _=Depends(get_current_user)):
    roles = db.execute(text("""
        SELECT r.name, r.display_name, r.description, r.is_system, r.created_at,
               COALESCE(
                   json_agg(json_build_object('module', rp.module, 'marketplace', rp.marketplace))
                   FILTER (WHERE rp.module IS NOT NULL), '[]'
               ) AS permissions
        FROM roles r
        LEFT JOIN role_permissions rp ON rp.role_name = r.name
        GROUP BY r.name, r.display_name, r.description, r.is_system, r.created_at
        ORDER BY r.is_system DESC, r.created_at
    """)).mappings().all()
    return {"data": [dict(r) for r in roles]}


@router.post("")
def create_role(payload: RoleCreate, db: Session = Depends(get_db), _=Depends(_admin_only)):
    if not payload.name.replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="Имя роли — только буквы, цифры и _")
    existing = db.execute(text("SELECT name FROM roles WHERE name = :n"), {"n": payload.name}).first()
    if existing:
        raise HTTPException(status_code=400, detail="Роль с таким именем уже существует")
    db.execute(text("""
        INSERT INTO roles (name, display_name, description, is_system)
        VALUES (:name, :display_name, :description, false)
    """), {"name": payload.name, "display_name": payload.display_name, "description": payload.description})
    db.commit()
    return {"status": "success", "name": payload.name}


@router.put("/{role_name}/permissions")
def set_permissions(role_name: str, payload: PermissionSet, db: Session = Depends(get_db), _=Depends(_admin_only)):
    role = db.execute(text("SELECT name FROM roles WHERE name = :n"), {"n": role_name}).first()
    if not role:
        raise HTTPException(status_code=404, detail="Роль не найдена")

    db.execute(text("DELETE FROM role_permissions WHERE role_name = :rn"), {"rn": role_name})
    for perm in payload.permissions:
        module = perm.get("module", "")
        marketplace = perm.get("marketplace", "all")
        if module and marketplace in MARKETPLACES:
            db.execute(text("""
                INSERT INTO role_permissions (role_name, module, marketplace)
                VALUES (:rn, :mod, :mp)
                ON CONFLICT (role_name, module) DO UPDATE SET marketplace = EXCLUDED.marketplace
            """), {"rn": role_name, "mod": module, "mp": marketplace})
    db.commit()
    return {"status": "success"}


@router.put("/{role_name}")
def update_role(role_name: str, payload: RoleCreate, db: Session = Depends(get_db), _=Depends(_admin_only)):
    role = db.execute(text("SELECT is_system FROM roles WHERE name = :n"), {"n": role_name}).first()
    if not role:
        raise HTTPException(status_code=404, detail="Роль не найдена")
    db.execute(text("""
        UPDATE roles SET display_name = :dn, description = :desc WHERE name = :n
    """), {"dn": payload.display_name, "desc": payload.description, "n": role_name})
    db.commit()
    return {"status": "success"}


@router.delete("/{role_name}")
def delete_role(role_name: str, db: Session = Depends(get_db), _=Depends(_admin_only)):
    role = db.execute(text("SELECT is_system FROM roles WHERE name = :n"), {"n": role_name}).first()
    if not role:
        raise HTTPException(status_code=404, detail="Роль не найдена")
    if role.is_system:
        raise HTTPException(status_code=400, detail="Системные роли нельзя удалять")
    db.execute(text("DELETE FROM roles WHERE name = :n"), {"n": role_name})
    db.commit()
    return {"status": "success"}


@router.get("/my-permissions")
def my_permissions(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    """Возвращает права текущего пользователя — вызывается при старте приложения"""
    role = current_user["role"]
    if role == "admin":
        # Адмни видит всё
        return {"role": role, "permissions": {m["key"]: "all" for m in MODULES}}

    rows = db.execute(text("""
        SELECT module, marketplace FROM role_permissions WHERE role_name = :r
    """), {"r": role}).fetchall()
    permissions = {r.module: r.marketplace for r in rows}
    return {"role": role, "permissions": permissions}
