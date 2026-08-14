"""Фоновый тегировщик отзывов (поиск идей). Живёт отдельно от сайта — страницу можно закрыть."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / "tmp"
EMPTY_TAGS = json.dumps({"processed": True, "tags": [], "suggestion": ""}, ensure_ascii=False)


def _pid_path(platform: str) -> Path:
    return TMP / f"feedback_tagger_{platform}.pid"


def _unit_name(platform: str) -> str:
    return f"cx-feedback-tagger-{platform}"


def is_feedback_job_running(platform: str = "wb") -> bool:
    platform = (platform or "wb").lower()
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "--quiet", _unit_name(platform)],
            check=False,
        )
        if r.returncode == 0:
            return True
    except Exception:
        pass
    path = _pid_path(platform)
    if not path.exists():
        return False
    try:
        pid = int(path.read_text().strip())
        os.kill(pid, 0)
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        path.unlink(missing_ok=True)
        return False
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode()
        return "feedback_tagger_job" in cmd
    except Exception:
        return True


def spawn_feedback_job(platform: str, model: str, batch_size: int) -> tuple[bool, str]:
    platform = (platform or "wb").lower()
    if platform not in ("wb", "ym"):
        return False, "Неизвестная площадка"
    if is_feedback_job_running(platform):
        return False, "already_running"

    TMP.mkdir(exist_ok=True)
    unit = _unit_name(platform)
    subprocess.run(["systemctl", "reset-failed", unit], check=False, capture_output=True)
    run_cmd = [
        "systemd-run",
        f"--unit={unit}",
        "--collect",
        f"--working-directory={ROOT}",
        f"--setenv=PYTHONPATH={ROOT}",
        sys.executable,
        "-m",
        "backend.feedback_tagger_job",
        "--platform",
        platform,
        "--model",
        model,
        "--batch",
        str(int(batch_size) or 20),
    ]
    try:
        subprocess.check_call(run_cmd)
        return True, (
            "Разметка отзывов запущена на сервере. Страницу можно закрыть — "
            "очередь дойдёт до конца сама."
        )
    except FileNotFoundError:
        pass
    log_path = TMP / f"feedback_tagger_{platform}.log"
    logf = open(log_path, "a", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "backend.feedback_tagger_job",
            "--platform",
            platform,
            "--model",
            model,
            "--batch",
            str(int(batch_size) or 20),
        ],
        cwd=str(ROOT),
        stdout=logf,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
    )
    _pid_path(platform).write_text(str(proc.pid))
    return True, (
        "Разметка отзывов запущена на сервере. Страницу можно закрыть — "
        "очередь дойдёт до конца сама."
    )


def _mark_pid(platform: str) -> None:
    TMP.mkdir(exist_ok=True)
    _pid_path(platform).write_text(str(os.getpid()))


def _clear_pid(platform: str) -> None:
    path = _pid_path(platform)
    try:
        if path.exists() and path.read_text().strip() == str(os.getpid()):
            path.unlink()
    except Exception:
        pass


def _examples(db) -> str:
    rows = db.execute(text(
        "SELECT content, tags, suggestion FROM ai_feedback_knowledge_base ORDER BY RANDOM() LIMIT 5"
    )).fetchall()
    if not rows:
        return ""
    parts = ["ПРИМЕРЫ АНАЛИЗА (ОБРАЗЕЦ ЛОГИКИ):\n"]
    for ex in rows:
        tags_str = "[]"
        if ex.tags:
            tags_arr = [f'"{t.strip()}"' for t in str(ex.tags).split(",") if t.strip()]
            tags_str = f"[{', '.join(tags_arr)}]"
        parts.append(f"- Текст: {ex.content}\n  Теги: {tags_str}. Идея: '{ex.suggestion or ''}'\n\n")
    return "".join(parts)


async def _loop_table(
    *,
    platform: str,
    table: str,
    text_sql: str,
    model_key: str,
    batch_size: int,
    log_prefix: str,
) -> None:
    import aiohttp
    from backend.database import SessionLocal
    from backend.routers.ai import add_system_log_db, fetch_ai_feedback_tags

    batch_size = max(5, min(int(batch_size or 20), 40))
    db = SessionLocal()
    try:
        skipped = db.execute(text(f"""
            UPDATE {table}
               SET ai_tags = CAST(:payload AS jsonb)
             WHERE (ai_tags IS NULL OR NOT (ai_tags ? 'processed'))
               AND COALESCE(valuation, 5) = 5
               AND length(btrim(coalesce(({text_sql}), ''))) < 60
        """), {"payload": EMPTY_TAGS}).rowcount
        db.commit()
        add_system_log_db(
            db, f"{log_prefix}: Старт", "INFO",
            f"Короткие 5★ закрыты без ИИ: {skipped}. Дальше Qwen пачками по {batch_size}.",
        )

        examples_text = _examples(db)
        done_ai = 0
        async with aiohttp.ClientSession() as session:
            while True:
                rows = db.execute(text(f"""
                    SELECT id, ({text_sql}) AS body, valuation
                      FROM {table}
                     WHERE (ai_tags IS NULL OR NOT (ai_tags ? 'processed'))
                       AND NOT (
                            COALESCE(valuation, 5) = 5
                            AND length(btrim(coalesce(({text_sql}), ''))) < 60
                       )
                     ORDER BY id
                     LIMIT :lim
                """), {"lim": batch_size}).fetchall()
                if not rows:
                    break

                batch, id_map = [], {}
                for idx, row in enumerate(rows):
                    ref = f"REF_{idx}"
                    fid = str(row[0])
                    id_map[ref] = fid
                    batch.append({"id": ref, "text": str(row[1] or "").strip()})

                results = []
                last_err = ""
                for attempt in range(3):
                    results = await fetch_ai_feedback_tags(session, batch, model_key, examples_text)
                    if results and not all(isinstance(r, dict) and "error" in r for r in results):
                        break
                    last_err = ""
                    if results and isinstance(results[0], dict):
                        last_err = str(results[0].get("error") or "")
                    await asyncio.sleep(4 * (attempt + 1))

                saved = set()
                error_details = []
                for res in results or []:
                    if not isinstance(res, dict):
                        continue
                    if "error" in res:
                        error_details.append(res["error"])
                        continue
                    fid = id_map.get(str(res.get("id") or ""))
                    if not fid:
                        continue
                    payload = json.dumps({
                        "processed": True,
                        "tags": res.get("tags", []),
                        "suggestion": res.get("suggestion", ""),
                    }, ensure_ascii=False)
                    db.execute(
                        text(f"UPDATE {table} SET ai_tags = CAST(:t AS jsonb) WHERE id = :id"),
                        {"t": payload, "id": fid},
                    )
                    saved.add(fid)
                    done_ai += 1

                # Чтобы одна плохая пачка не крутилась вечно — помечаем остальные закрытыми.
                for fid in id_map.values():
                    if fid in saved:
                        continue
                    db.execute(
                        text(f"UPDATE {table} SET ai_tags = CAST(:t AS jsonb) WHERE id = :id"),
                        {"t": EMPTY_TAGS, "id": fid},
                    )
                db.commit()

                left = db.execute(text(f"""
                    SELECT count(*) FROM {table}
                    WHERE ai_tags IS NULL OR NOT (ai_tags ? 'processed')
                """)).scalar() or 0
                note = f"ИИ в этой пачке: {len(saved)}. Всего ИИ: {done_ai}. Осталось: {left}."
                if error_details or last_err:
                    add_system_log_db(
                        db, f"{log_prefix} пачка", "WARNING",
                        note + f" Ошибка: {error_details[0] if error_details else last_err}",
                    )
                else:
                    add_system_log_db(db, f"{log_prefix} пачка", "SUCCESS", note)
                await asyncio.sleep(1)

        add_system_log_db(db, f"{log_prefix}: Итог", "SUCCESS", f"Готово. Через ИИ размечено {done_ai}.")
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        from backend.routers.ai import add_system_log_db
        add_system_log_db(db, f"{log_prefix}: FATAL", "ERROR", str(e)[:400])
        raise
    finally:
        db.close()


async def run_wb(model_key: str, batch_size: int) -> None:
    await _loop_table(
        platform="wb",
        table="wb_feedbacks",
        text_sql="text",
        model_key=model_key,
        batch_size=batch_size,
        log_prefix="ИИ Отзывы WB",
    )


async def run_ym(model_key: str, batch_size: int) -> None:
    await _loop_table(
        platform="ym",
        table="ym_feedbacks",
        text_sql="TRIM(CONCAT_WS(' ', NULLIF(pro_text,''), NULLIF(contra_text,''), NULLIF(comment,'')))",
        model_key=model_key,
        batch_size=batch_size,
        log_prefix="ИИ Отзывы ЯМ",
    )


async def process_feedback_tagging_task(model_key: str, batch_size: int) -> None:
    await run_wb(model_key, batch_size)


async def process_ym_feedback_tagging_task(model_key: str, batch_size: int) -> None:
    await run_ym(model_key, batch_size)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", default="wb", choices=("wb", "ym"))
    parser.add_argument("--model", default="qwen-plus")
    parser.add_argument("--batch", type=int, default=20)
    args = parser.parse_args(argv)
    _mark_pid(args.platform)
    try:
        if args.platform == "ym":
            asyncio.run(run_ym(args.model, args.batch))
        else:
            asyncio.run(run_wb(args.model, args.batch))
        return 0
    finally:
        _clear_pid(args.platform)


if __name__ == "__main__":
    raise SystemExit(main())
