"""
Ночная резервная копия баз всех проектов на сервере.

  python3 scripts/backup_db.py            # снять копии
  python3 scripts/backup_db.py --check    # только проверить последние копии

Крон: 0 2 * * *  (= 05:00 МСК, сервер в UTC). Окно выбрано между
worker.py (01:00 UTC) и ночными сверками ЯМ/Ozon (03:00 UTC).

Базы: cx_dashboard, kontrolmp, navigator, shared_facts.
Ключи сюда не входят — их архив собирает scripts/backup_offsite.py.

Что делает для каждой базы:
  1. Проверяет, что на диске хватает места.
  2. pg_dump -Fc в /root/backups/db/<база>-ГГГГММДД-ЧЧММ.dump.
     Запуск от пользователя postgres (peer-доступ), пароль не нужен.
  3. Проверяет копию через pg_restore --list: файл читается, нужные таблицы
     на месте, размер не обвалился. Битый дамп удаляется.
  4. Ротация отдельно по каждой базе: 14 ночных + по одной на первое число месяца.
  5. Пишет итог в system_logs, при сбое сразу шлёт в Telegram.
     Сбой одной базы не отменяет копии остальных.

Восстановление (на чистую базу, боевую не трогаем):
  createdb -U postgres cx_restore_test
  pg_restore -U postgres -d cx_restore_test --no-owner /root/backups/db/ИМЯ.dump
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

from backend.database import engine
from backend import telegram_proxy

load_dotenv()

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/root/backups/db"))
MONTHLY_DIR = BACKUP_DIR / "monthly"

# Имя, минимальный размер копии в МБ, таблицы без которых копия бесполезна.
DATABASES = (
    {"name": "cx_dashboard", "min_mb": 30.0, "tables": ("wb_orders", "ozon_orders", "ym_orders", "system_logs")},
    {"name": "kontrolmp", "min_mb": 5.0, "tables": ("orders", "fbo_warehouse_stocks")},
    {"name": "navigator", "min_mb": 0.2, "tables": ("sales_days", "sku_am")},
    {"name": "shared_facts", "min_mb": 0.01, "tables": ("ozon_return_fact",)},
)

KEEP_DAILY = 14
KEEP_MONTHLY = 12
# Четыре копии за ночь. Порог с запасом на сами дампы и ротацию.
MIN_FREE_GB = 5.0
# Обвал относительно прошлой копии той же базы = дамп оборвался.
MIN_RATIO_TO_PREV = 0.7
DUMP_TIMEOUT_SEC = 60 * 30

LOG_ACTION = "Бэкап базы"
ALERT_CHAT = (os.getenv("TG_ADMIN_CHAT_ID") or os.getenv("TG_ALERT_CHAT_ID") or "").strip()


def _name_re(db_name: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(db_name)}-(\d{{8}})-(\d{{4}})\.dump$")


def log(msg: str) -> None:
    print(f"[backup] {msg}", flush=True)


def log_to_system(status: str, details: str = "") -> None:
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO system_logs (action, status, details) VALUES (:a, :s, :d)"),
                {"a": LOG_ACTION, "s": status, "d": str(details)[:2000]},
            )
    except Exception as e:
        log(f"не удалось записать лог в БД: {e}")


def alert(msg: str) -> None:
    """Сбой бэкапа — сразу в Telegram: ждать утренней сводки тут нельзя."""
    log(msg)
    if not ALERT_CHAT:
        log("TG_ADMIN_CHAT_ID не задан — алерт отправить некуда")
        return
    try:
        telegram_proxy.send_text(ALERT_CHAT, telegram_proxy.strip_tg_html(msg))
    except Exception as e:
        log(f"не удалось отправить алерт: {e}")


def human_mb(path: Path) -> float:
    return path.stat().st_size / (1024 ** 2)


def daily_dumps(db_name: str) -> list[Path]:
    if not BACKUP_DIR.exists():
        return []
    name_re = _name_re(db_name)
    found = [p for p in BACKUP_DIR.iterdir() if p.is_file() and name_re.match(p.name)]
    return sorted(found, key=lambda p: p.name)


def verify(dump: Path, prev: Path | None, spec: dict) -> str:
    """Возвращает описание проблемы или пустую строку, если копия годная."""
    size_mb = human_mb(dump)
    if size_mb < spec["min_mb"]:
        return (f"{spec['name']}: копия подозрительно мала: {size_mb:.1f} МБ "
                f"(минимум {spec['min_mb']:.0f} МБ)")

    if prev is not None:
        prev_mb = human_mb(prev)
        if prev_mb > 0 and size_mb < prev_mb * MIN_RATIO_TO_PREV:
            return (f"{spec['name']}: копия резко меньше предыдущей: {size_mb:.1f} МБ "
                    f"против {prev_mb:.1f} МБ ({prev.name}) — похоже, дамп оборвался")

    try:
        listing = subprocess.run(
            ["pg_restore", "--list", str(dump)],
            capture_output=True, text=True, timeout=300,
        )
    except Exception as e:
        return f"{spec['name']}: не удалось прочитать копию через pg_restore: {e}"
    if listing.returncode != 0:
        return f"{spec['name']}: pg_restore не читает копию: {(listing.stderr or '').strip()[:300]}"

    missing = [t for t in spec["tables"] if f" {t} " not in listing.stdout]
    if missing:
        return f"{spec['name']}: в копии нет таблиц: {', '.join(missing)}"
    return ""


def rotate(db_name: str) -> list[str]:
    """Оставляет KEEP_DAILY ночных копий этой базы; первые числа месяца уводит в monthly/."""
    removed: list[str] = []
    dumps = daily_dumps(db_name)
    name_re = _name_re(db_name)

    MONTHLY_DIR.mkdir(parents=True, exist_ok=True)
    for dump in dumps:
        m = name_re.match(dump.name)
        if not m or not m.group(1).endswith("01"):
            continue
        target = MONTHLY_DIR / dump.name
        if not target.exists():
            # Жёсткая ссылка: месячная копия не занимает место, пока жива ночная.
            try:
                os.link(dump, target)
            except OSError:
                shutil.copy2(dump, target)

    for dump in dumps[:-KEEP_DAILY] if len(dumps) > KEEP_DAILY else []:
        try:
            dump.unlink()
            removed.append(dump.name)
        except OSError as e:
            log(f"не удалось удалить {dump.name}: {e}")

    monthly = sorted(MONTHLY_DIR.glob(f"{db_name}-*.dump"), key=lambda p: p.name)
    for old in monthly[:-KEEP_MONTHLY] if len(monthly) > KEEP_MONTHLY else []:
        try:
            old.unlink()
            removed.append(f"monthly/{old.name}")
        except OSError as e:
            log(f"не удалось удалить {old.name}: {e}")

    return removed


def dump_one(spec: dict, stamp: str) -> tuple[str, str]:
    """Снимает одну базу. Возвращает (итог, ошибка). Ошибка пустая, если копия годная."""
    db_name = spec["name"]
    prev = daily_dumps(db_name)[-1] if daily_dumps(db_name) else None
    dump = BACKUP_DIR / f"{db_name}-{stamp}.dump"
    log(f"снимаю копию {db_name} → {dump}")

    try:
        with dump.open("wb") as fh:
            proc = subprocess.run(
                ["su", "postgres", "-c", f"pg_dump -Fc --compress=6 {db_name}"],
                stdout=fh, stderr=subprocess.PIPE, timeout=DUMP_TIMEOUT_SEC,
            )
    except subprocess.TimeoutExpired:
        dump.unlink(missing_ok=True)
        return "", f"{db_name}: pg_dump не уложился в {DUMP_TIMEOUT_SEC // 60} минут"
    except Exception as e:
        dump.unlink(missing_ok=True)
        return "", f"{db_name}: {e}"

    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()[:300]
        dump.unlink(missing_ok=True)
        return "", f"{db_name}: pg_dump вернул ошибку. {err}"

    problem = verify(dump, prev, spec)
    if problem:
        dump.unlink(missing_ok=True)
        return "", problem

    removed = rotate(db_name)
    line = f"{dump.name} ({human_mb(dump):.1f} МБ, ночных {len(daily_dumps(db_name))})"
    if removed:
        line += f", убраны старые: {', '.join(removed)}"
    return line, ""


def run_backup() -> int:
    started = datetime.now(timezone.utc)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    free_gb = shutil.disk_usage(BACKUP_DIR).free / (1024 ** 3)
    if free_gb < MIN_FREE_GB:
        msg = (f"❌ Бэкап баз не сделан: на диске свободно {free_gb:.1f} ГБ "
               f"(нужно минимум {MIN_FREE_GB:.0f} ГБ).")
        log_to_system("ERROR", msg)
        alert(msg)
        return 1

    stamp = started.strftime("%Y%m%d-%H%M")
    ok_lines: list[str] = []
    errors: list[str] = []
    for spec in DATABASES:
        line, err = dump_one(spec, stamp)
        if err:
            errors.append(err)
            log(err)
        else:
            ok_lines.append(line)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    free_after = shutil.disk_usage(BACKUP_DIR).free / (1024 ** 3)
    details = (f"За {elapsed:.0f} с. Свободно на диске: {free_after:.1f} ГБ. "
               f"Готово: {'; '.join(ok_lines) or 'ничего'}.")
    if errors:
        details += f" Сбой: {'; '.join(errors)}."
        log_to_system("ERROR", details)
        alert("❌ Бэкап баз неполный. " + details)
        log(details)
        return 1

    log_to_system("SUCCESS", details)
    log(details)
    return 0


def run_check() -> int:
    """Быстрая проверка последней копии каждой базы."""
    failed = False
    for spec in DATABASES:
        dumps = daily_dumps(spec["name"])
        monthly = list(MONTHLY_DIR.glob(f"{spec['name']}-*.dump"))
        if not dumps:
            print(f"{spec['name']}: копий нет")
            failed = True
            continue
        latest = dumps[-1]
        problem = verify(latest, dumps[-2] if len(dumps) > 1 else None, spec)
        age_h = (datetime.now().timestamp() - latest.stat().st_mtime) / 3600
        print(f"{spec['name']}: {latest.name}, {human_mb(latest):.1f} МБ, "
              f"возраст {age_h:.1f} ч, ночных {len(dumps)}, месячных {len(monthly)}")
        print(f"  проверка: {problem or 'копия читается, таблицы на месте'}")
        if problem:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_check() if "--check" in sys.argv[1:] else run_backup())
