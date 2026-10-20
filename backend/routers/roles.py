from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import List, Optional
from ..database import get_db
from .auth import get_current_user

router = APIRouter(prefix="/api/v1/admin/roles", tags=["Roles / Роли"])

# Два уровня: проект (вход) и разделы внутри. Панель ролей на хабе
# рисует это дерево. Сайдбар CX по-прежнему смотрит только свои ключи.
PROJECTS = [
    {
        "key": "cx",
        "module": "project_cx",
        "label": "CX Dashboard",
        "description": "Отзывы, чаты и возвраты",
    },
    {
        "key": "stock",
        "module": "project_stock",
        "label": "Контроль МП",
        "description": "Остатки, локальность, оборачиваемость",
    },
    {
        "key": "navigator",
        "module": "project_navigator",
        "label": "Навигатор",
        "description": "Продажи, план, цены и поставки",
    },
]

_CX_SECTIONS = [
    {"key": "dashboard", "label": "Главный дашборд", "group": "Аналитика", "description": "Общие показатели компании"},

    {"key": "sync", "label": "Статус автоматизации", "group": "Технический блок", "description": "Синхронизации и состояние источников"},
    {"key": "ai_tagging", "label": "ИИ Тегирование", "group": "Технический блок", "description": "Автоматическая разметка обращений"},
    {"key": "moderation", "label": "Модерация тегов", "group": "Технический блок", "description": "Проверка разметки ИИ"},
    {"key": "ai_training", "label": "Обучение ИИ", "group": "Технический блок", "description": "Примеры и правила для ИИ"},

    {"key": "production", "label": "Карта проблем", "group": "Операционный блок", "description": "Проблемы по товарам и производству"},
    {"key": "ppm", "label": "PPM, акты и реестр", "group": "Операционный блок", "description": "PPM, претензионные акты и их реестр"},
    {"key": "voc", "label": "Отзывы и аналитика", "group": "Операционный блок", "description": "Голос клиента и аналитика отзывов"},
    {"key": "ratings", "label": "Рейтинг товаров", "group": "Операционный блок", "description": "Динамика рейтингов товаров"},
    {"key": "finances", "label": "Финансовые потери", "group": "Операционный блок", "description": "Потери по возвратам и рекламациям"},
    {"key": "pnl", "label": "P&L по артикулу", "group": "Операционный блок", "description": "Экономика по отдельному артикулу"},
    {"key": "cx_control", "label": "Контроль отдела", "group": "Операционный блок", "description": "Нагрузка, SLA и активность сотрудников", "assignable": False},

    {"key": "cx_responses", "label": "Обращения клиентов", "group": "Клиентский сервис", "description": "Возвраты, чаты, отзывы и вопросы"},
    {"key": "tshopping", "label": "Запросы «Т-Шопинг»", "group": "Клиентский сервис", "description": "Общие чаты с Т-Шопингом: заявки, переписка, закрытие с их стороны"},
    {"key": "quality_questions", "label": "Уточнения по вопросам", "group": "Клиентский сервис", "description": "Очередь экспертов качества для подготовки ответа"},
    {"key": "reshipment", "label": "Отправка деталей", "group": "Клиентский сервис", "description": "Заявки на доотправку деталей"},
    {"key": "warehouse_intake", "label": "Приёмка", "group": "Клиентский сервис", "description": "Учёт товаров, принятых на склад по сканеру", "assignable": False},
    {"key": "shop_orders", "label": "Заказы сайта", "group": "Клиентский сервис", "description": "Заказы с lk.vidovito.com для сборки и отправки"},
    {"key": "faq", "label": "База знаний FAQ", "group": "Клиентский сервис", "description": "Инструкции и готовые ответы"},

    {"key": "logs", "label": "Журнал событий", "group": "Администрирование", "description": "Технические события системы"},
    {"key": "docs", "label": "Документация системы", "group": "Администрирование", "description": "Описание разделов и процессов"},
    {"key": "admin_panel", "label": "Панель управления", "group": "Администрирование", "description": "Пользователи, роли и системные настройки", "assignable": False},
]

_NAVIGATOR_SECTIONS = [
    {"key": "nav_sales", "label": "Продажи", "group": "Мониторинг", "description": "ABC/XYZ, динамика и расходы по позициям"},
    {"key": "nav_pulse", "label": "Пульс", "group": "Мониторинг", "description": "Ритм портфеля и что сбивает продажи"},
    {"key": "nav_graphs", "label": "Графики", "group": "Мониторинг", "description": "Динамика выбранного среза"},
    {"key": "nav_matrix", "label": "Матрица продаж", "group": "Мониторинг", "description": "Артикул по дням"},
    {"key": "nav_supply", "label": "Матрица поставок", "group": "Мониторинг", "description": "Даты приезда и отгрузки"},
    {"key": "nav_forecast", "label": "Прогноз", "group": "Мониторинг", "description": "Скорость последних дней и горизонт"},
    {"key": "nav_plan", "label": "План/факт", "group": "План", "description": "Выполнение плана новинок"},
    {"key": "nav_payback", "label": "Окупаемость заказов", "group": "План", "description": "Заказы и себестоимость"},
    {"key": "nav_prices", "label": "Коридор цен", "group": "Цены", "description": "Цена продавца, покупателя и минимум"},
]


_STOCK_SECTIONS = [
    {"key": "stock_weekly", "label": "Еженедельный отчёт", "group": "Разделы", "description": "Сводка недели"},
    {"key": "stock_queue", "label": "Очередь решений", "group": "Разделы", "description": "Что ставить и куда"},
    {"key": "stock_locality", "label": "Локальность", "group": "Разделы", "description": "Наличие в кластерах"},
    {"key": "stock_turnover", "label": "Оборачиваемость", "group": "Разделы", "description": "Скорость продаж и сток"},
    {"key": "stock_sales", "label": "Продажи (выкуп)", "group": "Разделы", "description": "Выкуп по артикулам"},
    {"key": "stock_fbs", "label": "ФБС", "group": "Разделы", "description": "Свободный остаток и заказы со своего склада"},
    {"key": "stock_plan", "label": "План месяца", "group": "Разделы", "description": "План и факт"},
    {"key": "stock_costs", "label": "Расходы: нелокальность", "group": "Разделы", "description": "Переплата за нелокальность"},
    {"key": "stock_ovh", "label": "ОВХ", "group": "Разделы", "description": "Объёмно-весовые характеристики"},
    {"key": "stock_weights", "label": "Веса по кластерам", "group": "Разделы", "description": "Веса и приоритеты кластеров"},
    {"key": "stock_finance", "label": "Финансы", "group": "Разделы", "description": "Юнит-экономика стока"},
    {"key": "stock_expenses", "label": "Расходы: где режем", "group": "Разделы", "description": "Где режем расходы"},
    {"key": "stock_actions", "label": "Что чинить", "group": "Разделы", "description": "Очередь исправлений"},
    {"key": "stock_loss", "label": "Динамика потерь", "group": "Разделы", "description": "Потери по периодам"},
]


def _tagged(rows, project):
    out = []
    for row in rows:
        item = dict(row)
        item.setdefault("project", project)
        out.append(item)
    return out


MODULES = (
    [
        {
            "key": proj["module"],
            "label": proj["label"],
            "group": "Проекты",
            "description": proj["description"],
            "project": proj["key"],
            "kind": "project",
        }
        for proj in PROJECTS
    ]
    + _tagged(_CX_SECTIONS, "cx")
    + _tagged(_STOCK_SECTIONS, "stock")
    + _tagged(_NAVIGATOR_SECTIONS, "navigator")
)

ASSIGNABLE_MODULE_KEYS = {
    module["key"] for module in MODULES if module.get("assignable", True)
}

MARKETPLACES = ["all", "wb", "ym", "ozon"]
PROJECT_MODULE_KEYS = {proj["module"] for proj in PROJECTS}


def inner_modules(project_key: str):
    return [
        module for module in MODULES
        if module.get("project") == project_key and module.get("kind") != "project"
    ]


def infer_project_permissions(permissions: dict) -> dict:
    """Старые роли без project_* всё равно имеют вход в CX, если выданы разделы."""
    out = dict(permissions or {})
    for proj in PROJECTS:
        if proj["module"] in out:
            continue
        inner_keys = [module["key"] for module in inner_modules(proj["key"])]
        if any(key in out for key in inner_keys):
            out[proj["module"]] = "all"
    return out


def has_project_access(permissions: dict, project_key: str) -> bool:
    perms = infer_project_permissions(permissions)
    proj = next((item for item in PROJECTS if item["key"] == project_key), None)
    if not proj:
        return False
    if proj["module"] in perms:
        return True
    return any(module["key"] in perms for module in inner_modules(project_key))

# Роли КС по умолчанию должны видеть рабочий стол обращений
_CS_DEFAULT_MODULES = ("cx_responses", "tshopping", "reshipment", "shop_orders", "faq")
_QA_DEFAULT_MODULES = ("quality_questions",)
_WAREHOUSE_DEFAULT_MODULES = ("reshipment", "shop_orders")


def ensure_cs_manager_permissions(engine) -> None:
    """Гарантирует системным ролям их обязательные рабочие разделы."""
    with engine.connect() as conn:
        defaults = {
            "cs_manager": _CS_DEFAULT_MODULES,
            "qa_manager": _QA_DEFAULT_MODULES,
            "warehouse_manager": _WAREHOUSE_DEFAULT_MODULES,
        }
        for role_name, modules in defaults.items():
            exists = conn.execute(text(
                "SELECT 1 FROM roles WHERE name = :role LIMIT 1"
            ), {"role": role_name}).scalar()
            if not exists:
                continue
            for mod in modules:
                conn.execute(text("""
                    INSERT INTO role_permissions (role_name, module, marketplace)
                    VALUES (:role, :module, 'all')
                    ON CONFLICT (role_name, module) DO NOTHING
                """), {"role": role_name, "module": mod})
        conn.commit()


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
def get_modules(_=Depends(_admin_only)):
    return {"projects": PROJECTS, "modules": MODULES, "marketplaces": MARKETPLACES}


@router.get("")
def get_roles(db: Session = Depends(get_db), _=Depends(_admin_only)):
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
        if module in ASSIGNABLE_MODULE_KEYS and marketplace in MARKETPLACES:
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
    permissions = infer_project_permissions({r.module: r.marketplace for r in rows})
    return {"role": role, "permissions": permissions}
