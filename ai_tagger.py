"""
Автономный AI-тегировщик возвратов (WB + ЯМ).
Запускается из cron после загрузки данных воркерами.

  python3 ai_tagger.py wb   — тегировать WB-претензии
  python3 ai_tagger.py ym   — тегировать ЯМ-возвраты
  python3 ai_tagger.py all  — оба (default)
"""
import asyncio
import aiohttp
import json
import logging
import os
import re
import sys
import time
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [AI-TAGGER] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ai_tagger")

DATABASE_URL = os.getenv(
    "DATABASE_URL_LOCAL",
    "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard",
)
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "").strip()
FOLDER_ID      = os.getenv("FOLDER_ID", "").strip()

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

CATEGORIES = {
    1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
    4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
    7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
    10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
    13: "Следы использования / Б/У"
}

BATCH_SIZE = 10


def _log_db(action, status, details=""):
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO system_logs (action, status, details) VALUES (:a,:s,:d)"),
                {"a": action, "s": status, "d": str(details)[:2000]},
            )
    except Exception:
        pass


def _repair_json(s: str) -> str:
    stack, in_str, escape = [], False, False
    for ch in s:
        if escape: escape = False; continue
        if ch == '\\' and in_str: escape = True; continue
        if ch == '"': in_str = not in_str; continue
        if in_str: continue
        if ch in '{[': stack.append('}' if ch == '{' else ']')
        elif ch in '}]' and stack and stack[-1] == ch: stack.pop()
    return s + ''.join(reversed(stack))


def _parse_response(raw: str):
    try:
        clean = re.sub(r'```json|```', '', raw).strip()
        start = min([i for i in [clean.find('['), clean.find('{')] if i >= 0] or [0])
        end   = max(clean.rfind(']'), clean.rfind('}')) + 1
        clean = clean[start:end]
        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError:
            parsed = json.loads(_repair_json(clean))
        if isinstance(parsed, dict):  return parsed.get('results', [])
        if isinstance(parsed, list):  return parsed
    except Exception:
        pass
    return []


def _get_memory(text_sample: str) -> str:
    if not text_sample:
        return "Опыта пока нет."
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT content, tags FROM ai_knowledge_base
                WHERE similarity(content, :t) > 0.05
                ORDER BY similarity(content, :t) DESC LIMIT 2
            """), {"t": text_sample}).fetchall()
        if rows:
            return "\n".join(f"Текст: {r[0]} -> Тег: {r[1]}" for r in rows)
    except Exception:
        pass
    return "Опыта пока нет."


async def _ask_yandex(session: aiohttp.ClientSession, batch: list, memory: str) -> list:
    content = "\n".join(f"ID {i['id']}: {i['text']}" for i in batch)
    prompt = (
        f"Ты эксперт контроля качества.\n"
        f"Категории (ID: Название): {json.dumps(CATEGORIES, ensure_ascii=False)}\n"
        f"ПРАВИЛО 12: Если клиент хвалит, но есть мелкий дефект — СТРОГО Категория 12.\n"
        f"ПРИМЕРЫ ИЗ БАЗЫ:\n{memory}\n"
        f"ОТВЕТЬ СТРОГО JSON: {{\"results\": [{{\"id\": \"...\", \"category_ids\": [1, 5]}}]}}"
    )
    payload = {
        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {"temperature": 0.1, "maxTokens": 2000},
        "messages": [{"role": "system", "text": prompt}, {"role": "user", "text": content}],
    }
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    if FOLDER_ID:
        headers["x-folder-id"] = FOLDER_ID

    for attempt in range(3):
        try:
            async with session.post(
                "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=90)
            ) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    return _parse_response(res['result']['alternatives'][0]['message']['text'])
                if resp.status == 429:
                    await asyncio.sleep(10 * (attempt + 1))
                    continue
                log.warning(f"YandexGPT вернул {resp.status}")
                return []
        except Exception as e:
            if attempt == 2:
                log.error(f"YandexGPT ошибка: {e}")
            await asyncio.sleep(5)
    return []


async def _tag_platform(platform: str):
    """Тегирует одну площадку: 'wb' или 'ym'."""
    if platform == "wb":
        query = text("""
            SELECT srid AS rid, supplier_article, user_comment AS comment
            FROM wb_claims
            WHERE NOT (cat_1 OR cat_2 OR cat_3 OR cat_4 OR cat_5 OR cat_6 OR
                       cat_7 OR cat_8 OR cat_9 OR cat_10 OR cat_11 OR cat_12 OR cat_13)
              AND (audit_status IS NULL OR audit_status != 'Пропущено ИИ')
              AND user_comment IS NOT NULL AND TRIM(user_comment) != ''
        """)
        update_sql  = "UPDATE wb_claims SET {cols} WHERE srid = :rid"
        skip_sql    = "UPDATE wb_claims SET audit_status = 'Пропущено ИИ' WHERE srid = :rid"
        label       = "WB"
    elif platform == "ym":
        query = text("""
            SELECT return_id AS rid, supplier_article, return_comment AS comment
            FROM ym_returns
            WHERE return_comment IS NOT NULL AND TRIM(return_comment) != ''
              AND cat_1  IS NULL AND cat_2  IS NULL AND cat_3  IS NULL
              AND cat_4  IS NULL AND cat_5  IS NULL AND cat_6  IS NULL
              AND cat_7  IS NULL AND cat_8  IS NULL AND cat_9  IS NULL
              AND cat_10 IS NULL AND cat_11 IS NULL AND cat_12 IS NULL AND cat_13 IS NULL
              AND (audit_status IS NULL OR audit_status != 'Пропущено ИИ')
        """)
        update_sql  = "UPDATE ym_returns SET {cols} WHERE return_id = :rid"
        skip_sql    = "UPDATE ym_returns SET audit_status = 'Пропущено ИИ' WHERE return_id = :rid"
        label       = "ЯМ"
    else:
        # Ozon: только возвраты по качеству/браку с 2026-05-01
        query = text("""
            SELECT return_id AS rid, supplier_article, return_reason AS comment, product_name
            FROM ozon_returns
            WHERE return_date >= '2026-05-01'
              AND (return_reason ILIKE '%качеств%' OR return_reason ILIKE '%брак%'
                OR return_reason ILIKE '%комплект%' OR return_reason ILIKE '%дефект%'
                OR return_reason ILIKE '%поврежден%')
              AND cat_1  IS NULL AND cat_2  IS NULL AND cat_3  IS NULL
              AND cat_4  IS NULL AND cat_5  IS NULL AND cat_6  IS NULL
              AND cat_7  IS NULL AND cat_8  IS NULL AND cat_9  IS NULL
              AND cat_10 IS NULL AND cat_11 IS NULL AND cat_12 IS NULL AND cat_13 IS NULL
              AND (audit_status IS NULL OR audit_status != 'Пропущено ИИ')
        """)
        update_sql  = "UPDATE ozon_returns SET {cols} WHERE return_id = :rid"
        skip_sql    = "UPDATE ozon_returns SET audit_status = 'Пропущено ИИ' WHERE return_id = :rid"
        label       = "OZON"

    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    total = len(rows)
    if total == 0:
        log.info(f"{label}: нечего тегировать")
        _log_db(f"ИИ Разметка {label}", "INFO", "Нет записей для тегирования")
        return

    log.info(f"{label}: начинаем тегирование {total} записей")
    _log_db(f"ИИ Разметка {label}: Старт", "INFO", f"Запущено тегирование {total} записей")

    saved_total = 0
    async with aiohttp.ClientSession() as session:
        for i in range(0, total, BATCH_SIZE):
            chunk = rows[i : i + BATCH_SIZE]

            # Собираем контекст из базы знаний
            mem_parts = []
            for row in chunk:
                m = _get_memory(str(row[2] or ""))
                if m and "Прямых совпадений" not in m and "пока нет" not in m:
                    mem_parts.append(m)
            memory = "\n".join(set(mem_parts)) or "Опыта пока нет."

            id_map = {}
            batch_data = []
            for idx, row in enumerate(chunk):
                ref = f"REF_{idx}"
                id_map[ref] = str(row[0])
                if platform == "ozon":
                    product_info = row[3] if len(row) > 3 and row[3] else row[1]
                    text_body = f"Причина возврата: {row[2]}. Товар: {product_info}"
                else:
                    text_body = f"Артикул: {row[1]}. Текст: {row[2]}"
                batch_data.append({"id": ref, "text": text_body})

            results = await _ask_yandex(session, batch_data, memory)

            saved = 0
            details = []
            for res in results:
                num = re.search(r'\d+', str(res.get('id', '')).upper())
                if not num:
                    continue
                real_rid  = id_map.get(f"REF_{num.group()}")
                cats      = res.get('category_ids', [])
                if not real_rid:
                    continue

                if cats:
                    cols = ", ".join(
                        f"cat_{re.search(r'\d+', str(c)).group()} = true"
                        for c in cats if re.search(r'\d+', str(c))
                    )
                    if cols:
                        with engine.begin() as conn:
                            conn.execute(text(update_sql.format(cols=cols)), {"rid": real_rid})
                        saved += 1
                        details.append(f"✅ {real_rid}: {cats}")
                else:
                    with engine.begin() as conn:
                        conn.execute(text(skip_sql), {"rid": real_rid})
                    details.append(f"⚠️ {real_rid}: пропущено ИИ")

            saved_total += saved
            _log_db(
                f"ИИ {label} пачка {i}",
                "SUCCESS" if saved > 0 else "WARNING",
                f"Сохранено {saved}/{len(chunk)}\n" + "\n".join(details[:30]),
            )
            log.info(f"{label} пачка {i}: сохранено {saved}/{len(chunk)}")
            await asyncio.sleep(3)

    _log_db(f"ИИ Разметка {label}: Итог", "SUCCESS", f"Размечено {saved_total} из {total}")
    log.info(f"{label}: завершено — {saved_total}/{total}")


async def main(platforms: list):
    for p in platforms:
        await _tag_platform(p)


if __name__ == "__main__":
    if not YANDEX_API_KEY or not FOLDER_ID:
        log.error("Не заданы YANDEX_API_KEY / FOLDER_ID в .env")
        sys.exit(1)

    task = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    if task == "wb":
        asyncio.run(main(["wb"]))
    elif task == "ym":
        asyncio.run(main(["ym"]))
    elif task == "ozon":
        asyncio.run(main(["ozon"]))
    elif task == "all":
        asyncio.run(main(["wb", "ym", "ozon"]))
    else:
        log.error(f"Неизвестная задача '{task}'. Используй: wb | ym | ozon | all")
        sys.exit(1)
