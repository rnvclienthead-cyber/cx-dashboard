"""
Копия резервных данных на Яндекс Диск — хранилище вне сервера.

  python3 scripts/backup_offsite.py             # залить то, чего ещё нет
  python3 scripts/backup_offsite.py --check     # показать, что лежит на Диске

Крон: 30 2 * * *  (= 05:30 МСК) — через полчаса после scripts/backup_db.py,
чтобы заливать уже проверенную ночную копию.

Зачем: копии в /root/backups лежат на том же диске, что и база. Они спасают
от испорченных данных, но не от потери самого сервера. Здесь — второй экземпляр
снаружи.

Работаем через REST API Диска, а не через WebDAV: WebDAV Яндекс режет до
16-160 КБ/с (замерено 14.09.2026), на копии базы это часы. REST выдаёт ссылку
на отдельный загрузочный хост, который так не ограничен.

Что заливает:
  db/         — ночные копии баз CX, Контроля МП, Навигатора и полки.
                По каждой базе хранится столько файлов, сколько есть на сервере
                (14 ночных), но не больше 30.
  db-monthly/ — по одной копии на месяц, 12 месяцев, отдельно по каждой базе.
  photos/     — фото претензий и возвратов CX посуточными архивами за 14 дней.
                Посуточно, а не одним архивом: сутки это ~85 МБ, такой кусок
                переживает обрыв связи без больших потерь.
  secrets/    — ключи и пароли проектов (архив за сутки). Без них копию базы
                не к чему подключить.

Настройка в /root/cx-dashboard/.env (токен из oauth.yandex.ru, права
cloud_api:disk.read / disk.write / disk.info):

  YADISK_TOKEN=y0_...

Восстановление: скачать файл с Диска и развернуть как обычную копию
(см. docs/04_ДЕПЛОЙ.md → «Резервные копии базы»).
"""
from __future__ import annotations

import os
import re
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from sqlalchemy import text

from backend.database import engine
from backend import telegram_proxy

load_dotenv()

API = "https://cloud-api.yandex.net/v1/disk"
TOKEN = (os.getenv("YADISK_TOKEN") or "").strip()
ROOT = (os.getenv("YADISK_ROOT") or "CX-Dashboard-Backup").strip("/")

LOCAL_DB_DIR = Path(os.getenv("BACKUP_DIR", "/root/backups/db"))
SECRETS_DIR = Path("/root/backups/secrets")
PHOTO_DIRS = [Path("/root/cx-dashboard/static"), Path("/root/cx-dashboard/uploads")]
DB_NAMES = ("cx_dashboard", "kontrolmp", "navigator", "shared_facts")

KEEP_DB_REMOTE = 30
KEEP_MONTHLY_REMOTE = 12
KEEP_PHOTO_DAYS = 14
KEEP_SECRETS = 14

API_TIMEOUT = (30, 120)
# Первое число в requests — таймаут подключения, но по факту он же ограничивает
# и отправку тела: socket.timeout меняется на «читающий» только перед ответом.
# С 30 с файл рвался на середине, поэтому здесь 15 минут на отправку.
UPLOAD_TIMEOUT = (60 * 15, 60 * 15)
# Яндекс по ночам часто отвечает 502/503 на ссылку загрузки. Три коротких
# попытки не хватает — даём 5, с паузой подольше.
UPLOAD_ATTEMPTS = 5
# Яндекс режет скорость после крупной заливки (база идёт 13 МБ/с, следующий
# файл — уже 128 КБ/с). Чтобы ночная задача не висела часами, за один прогон
# берём несколько архивов фото; за пару ночей история догоняется сама.
MAX_PHOTO_UPLOADS = int(os.getenv("BACKUP_MAX_PHOTO_UPLOADS", "4"))

LOG_ACTION = "Копия на Яндекс Диск"
ALERT_CHAT = (os.getenv("TG_ADMIN_CHAT_ID") or os.getenv("TG_ALERT_CHAT_ID") or "").strip()

_PHOTO_RE = re.compile(r"^photos-(\d{8})\.tar$")
_DB_RE = re.compile(
    r"^(cx_dashboard|kontrolmp|navigator|shared_facts)-(\d{8})-\d{4}\.dump$"
)
_SECRET_RE = re.compile(r"^secrets-(\d{8})\.tar$")


def log(msg: str) -> None:
    print(f"[offsite] {msg}", flush=True)


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
    log(msg)
    if not ALERT_CHAT:
        return
    try:
        telegram_proxy.send_text(ALERT_CHAT, telegram_proxy.strip_tg_html(msg))
    except Exception as e:
        log(f"не удалось отправить алерт: {e}")


class YaDisk:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"OAuth {TOKEN}"

    def _check(self, r: requests.Response, what: str, ok: tuple[int, ...]) -> None:
        if r.status_code not in ok:
            try:
                reason = (r.json() or {}).get("message") or r.text[:200]
            except Exception:
                reason = r.text[:200]
            raise RuntimeError(f"{what}: HTTP {r.status_code} {reason}")

    def mkdir(self, path: str) -> None:
        r = self.session.put(f"{API}/resources", params={"path": f"disk:/{path}"}, timeout=API_TIMEOUT)
        # 409 = папка уже есть.
        self._check(r, f"создание папки {path}", (201, 409))

    def listdir(self, path: str) -> dict[str, int]:
        """Имя файла → размер. Пустой словарь, если папки ещё нет."""
        r = self.session.get(
            f"{API}/resources",
            params={"path": f"disk:/{path}", "limit": 1000,
                    "fields": "_embedded.items.name,_embedded.items.size"},
            timeout=API_TIMEOUT,
        )
        if r.status_code == 404:
            return {}
        self._check(r, f"список {path}", (200,))
        items = ((r.json() or {}).get("_embedded") or {}).get("items") or []
        return {it["name"]: int(it.get("size") or 0) for it in items if it.get("name")}

    def upload(self, local: Path, path: str) -> None:
        size = local.stat().st_size
        for attempt in range(1, UPLOAD_ATTEMPTS + 1):
            try:
                r = self.session.get(
                    f"{API}/resources/upload",
                    params={"path": f"disk:/{path}", "overwrite": "true"},
                    timeout=API_TIMEOUT,
                )
                self._check(r, f"ссылка для загрузки {path}", (200,))
                href = (r.json() or {}).get("href")
                if not href:
                    raise RuntimeError(f"Диск не дал ссылку для загрузки {path}")

                started = time.time()
                with local.open("rb") as fh:
                    # Ссылка одноразовая и уже содержит доступ — свой заголовок не шлём.
                    up = requests.put(href, data=fh, timeout=UPLOAD_TIMEOUT)
                self._check(up, f"загрузка {path}", (201, 202))
                speed = size / max(time.time() - started, 0.1) / 1024
                log(f"залито {path} ({size / 1024 ** 2:.0f} МБ, {speed:.0f} КБ/с)")
                return
            except Exception as e:
                # Файл мог долететь целиком, а ответ — потеряться. Проверяем по размеру.
                folder, name = path.rsplit("/", 1)
                try:
                    if self.listdir(folder).get(name) == size:
                        log(f"{path}: связь оборвалась, но файл на Диске целиком")
                        return
                except Exception:
                    pass
                if attempt == UPLOAD_ATTEMPTS:
                    raise
                delay = 30 * attempt
                log(f"{path}: попытка {attempt} не удалась ({e}), повторяю через {delay} с")
                time.sleep(delay)

    def delete(self, path: str) -> None:
        r = self.session.delete(
            f"{API}/resources",
            params={"path": f"disk:/{path}", "permanently": "true"},
            timeout=API_TIMEOUT,
        )
        self._check(r, f"удаление {path}", (202, 204, 404))

    def free_gb(self) -> float:
        r = self.session.get(API, timeout=API_TIMEOUT)
        self._check(r, "информация о Диске", (200,))
        data = r.json() or {}
        return (int(data.get("total_space", 0)) - int(data.get("used_space", 0))) / (1024 ** 3)


def _day_bounds(day: datetime) -> tuple[float, float]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def build_photo_archive(day: datetime, dest: Path) -> int:
    """Складывает в tar фото, созданные за эти сутки. Возвращает число файлов.

    Без сжатия: внутри jpg, gzip дал бы проценты за минуты процессорного времени.
    """
    lo, hi = _day_bounds(day)
    count = 0
    with tarfile.open(dest, "w") as tar:
        for base in PHOTO_DIRS:
            if not base.exists():
                continue
            for root, _dirs, files in os.walk(base):
                for name in files:
                    full = Path(root) / name
                    try:
                        mtime = full.stat().st_mtime
                    except OSError:
                        continue
                    if lo <= mtime < hi:
                        tar.add(full, arcname=str(full.relative_to(base.parent)))
                        count += 1
    return count


def _db_of(name: str) -> str | None:
    match = _DB_RE.match(name)
    return match.group(1) if match else None


def _sync_dump_dir(disk: YaDisk, remote_dir: str, local_dir: Path, keep_n: int) -> list[str]:
    """Заливает дампы по каждой базе отдельно. Пустую локальную базу на Диске не трогает."""
    done: list[str] = []
    disk.mkdir(remote_dir)
    remote = disk.listdir(remote_dir)
    for db_name in DB_NAMES:
        local = sorted(
            (p for p in local_dir.glob(f"{db_name}-*.dump") if _db_of(p.name) == db_name),
            key=lambda p: p.name,
        ) if local_dir.exists() else []
        if not local:
            continue
        chosen = local[-keep_n:]
        keep = {p.name for p in chosen}
        for dump in chosen:
            if remote.get(dump.name) == dump.stat().st_size:
                continue
            disk.upload(dump, f"{remote_dir}/{dump.name}")
            done.append(dump.name)
        for name in remote:
            if _db_of(name) == db_name and name not in keep:
                disk.delete(f"{remote_dir}/{name}")
                log(f"удалил с Диска старую копию {name}")
    return done


def sync_db(disk: YaDisk) -> list[str]:
    done = _sync_dump_dir(disk, f"{ROOT}/db", LOCAL_DB_DIR, KEEP_DB_REMOTE)
    monthly = _sync_dump_dir(disk, f"{ROOT}/db-monthly", LOCAL_DB_DIR / "monthly", KEEP_MONTHLY_REMOTE)
    return done + [f"monthly/{name}" for name in monthly]


def build_secrets_archive() -> Path | None:
    """Архив ключей за сегодня. В лог содержимое не пишем."""
    env = Path("/root/cx-dashboard/.env")
    secrets = Path("/root/secrets")
    if not env.is_file() and not secrets.is_dir():
        return None
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    dest = SECRETS_DIR / f"secrets-{datetime.now():%Y%m%d}.tar"
    with tarfile.open(dest, "w") as tar:
        if env.is_file():
            tar.add(env, arcname="cx-dashboard.env")
        if secrets.is_dir():
            tar.add(secrets, arcname="secrets")
    os.chmod(dest, 0o600)
    old = sorted(SECRETS_DIR.glob("secrets-*.tar"))
    for path in old[:-KEEP_SECRETS]:
        path.unlink(missing_ok=True)
    return dest


def sync_secrets(disk: YaDisk) -> list[str]:
    archive = build_secrets_archive()
    if archive is None:
        log("архив ключей не собран: на сервере нет .env и /root/secrets")
        return []
    disk.mkdir(f"{ROOT}/secrets")
    remote = disk.listdir(f"{ROOT}/secrets")
    done: list[str] = []
    if remote.get(archive.name) != archive.stat().st_size:
        disk.upload(archive, f"{ROOT}/secrets/{archive.name}")
        done.append(archive.name)
    local_names = {p.name for p in sorted(SECRETS_DIR.glob("secrets-*.tar"))[-KEEP_SECRETS:]}
    for name in remote:
        if _SECRET_RE.match(name) and name not in local_names:
            disk.delete(f"{ROOT}/secrets/{name}")
            log(f"удалил с Диска старый архив ключей {name}")
    return done


def sync_photos(disk: YaDisk) -> list[str]:
    done: list[str] = []
    disk.mkdir(f"{ROOT}/photos")
    remote = disk.listdir(f"{ROOT}/photos")

    today = datetime.now()
    # Вчера и глубже: сегодняшние сутки ещё не кончились, архив вышел бы неполным.
    days = [today - timedelta(days=back) for back in range(1, KEEP_PHOTO_DAYS + 1)]
    # Полный список нужных дней считаем заранее: если прогон упрётся в лимит,
    # ротация ниже не должна принять ещё не дошедшие архивы за лишние.
    wanted = {f"photos-{d.strftime('%Y%m%d')}.tar" for d in days}

    for day in days:
        name = f"photos-{day.strftime('%Y%m%d')}.tar"
        if name in remote:
            continue
        if len(done) >= MAX_PHOTO_UPLOADS:
            log(f"за этот прогон залито {len(done)} архивов, остальные — в следующий раз")
            break
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / name
            count = build_photo_archive(day, archive)
            if count == 0:
                log(f"{day:%d.%m}: новых фото нет, архив не нужен")
                continue
            log(f"собрал фото за {day:%d.%m}: {count} файлов")
            disk.upload(archive, f"{ROOT}/photos/{name}")
            done.append(name)

    for name in remote:
        if _PHOTO_RE.match(name) and name not in wanted:
            disk.delete(f"{ROOT}/photos/{name}")
            log(f"удалил с Диска старый архив фото {name}")
    return done


def run() -> int:
    if not TOKEN:
        msg = "❌ Копия на Яндекс Диск не сделана: не задан YADISK_TOKEN в .env."
        log_to_system("ERROR", msg)
        alert(msg)
        return 1

    started = datetime.now(timezone.utc)
    free = -1.0
    try:
        disk = YaDisk()
        free = disk.free_gb()
        if 0 <= free < 3:
            msg = f"❌ На Яндекс Диске свободно всего {free:.1f} ГБ — копии не поместятся."
            log_to_system("ERROR", msg)
            alert(msg)
            return 1
        disk.mkdir(ROOT)

        db_done = sync_db(disk)
        photo_done = sync_photos(disk)
        secret_done = sync_secrets(disk)
    except Exception as e:
        msg = f"❌ Копия на Яндекс Диск не удалась: {e}"
        log_to_system("ERROR", msg)
        alert(msg)
        return 1

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    details = (f"Залито: копий баз {len(db_done)}, архивов фото {len(photo_done)}, "
               f"архивов ключей {len(secret_done)}, за {elapsed:.0f} с. "
               f"Свободно на Диске: {free:.1f} ГБ.")
    log_to_system("SUCCESS", details)
    log(details)
    return 0


def run_check() -> int:
    if not TOKEN:
        print("YADISK_TOKEN не задан в .env")
        return 1
    disk = YaDisk()
    for folder in ("db", "db-monthly", "photos", "secrets"):
        try:
            items = disk.listdir(f"{ROOT}/{folder}")
        except Exception as e:
            print(f"{folder}: ошибка — {e}")
            continue
        total_mb = sum(items.values()) / 1024 ** 2
        print(f"{folder}: файлов {len(items)}, {total_mb:.0f} МБ, свежий: {max(items) if items else '—'}")
    print(f"Свободно на Диске: {disk.free_gb():.1f} ГБ")
    return 0


if __name__ == "__main__":
    sys.exit(run_check() if "--check" in sys.argv[1:] else run())
