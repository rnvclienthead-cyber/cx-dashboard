"""Standalone тегировщик отзывов WB — без лимита 2000, запускается напрямую."""
import asyncio, aiohttp, json, os, re, sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv("/root/cx-dashboard/.env")

DB_URL      = os.getenv("DATABASE_URL_LOCAL", "postgresql://db_user:RDB_r6o_BA0qSlVVGjb_2026@127.0.0.1:5432/cx_dashboard")
YANDEX_KEY  = os.getenv("YANDEX_API_KEY", "").strip()
FOLDER_ID   = os.getenv("FOLDER_ID", "").strip()
BATCH_SIZE  = 15

engine = create_engine(DB_URL, pool_pre_ping=True)

SYSTEM_PROMPT = """Ты Product Manager. Проанализируй отзывы покупателей и классифицируй их строго по Иерархической матрице болей.

ДОПУСТИМЫЕ ТЕГИ:
- "КОНСТРУКЦИЯ: Хлипкость и Неустойчивость"
- "КОНСТРУКЦИЯ: Слабые узлы и Соединения"
- "КОНСТРУКЦИЯ: Тонкий металл / Пластик"
- "ЭРГОНОМИКА: Мало места / Вместимость"
- "ЭРГОНОМИКА: Несоответствие размерам вещей"
- "ЭРГОНОМИКА: Неудобная форма изделия"
- "ФУНКЦИОНАЛ: Нет стопоров на колесах"
- "ФУНКЦИОНАЛ: Нехватка бортов/защиты"
- "ФУНКЦИОНАЛ: Запрос нового элемента"
- "СБОРКА: Непонятная инструкция"
- "СБОРКА: Несовпадение пазов/отверстий"
- "СБОРКА: Тяжелый физический монтаж"
- "ОЖИДАНИЯ: Отличие цвета от фото"
- "ОЖИДАНИЯ: Ощущение дешевизны вживую"

Верни СТРОГО JSON: {"results": [{"id": "REF_0", "tags": [...], "suggestion": "..."}]}
Если проблем нет — оставляй tags пустым."""


async def ask_yandex(session, batch):
    content = "\n".join(f"ID {r['id']}: {r['text']}" for r in batch)
    payload = {
        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt-lite/latest",
        "completionOptions": {"temperature": 0.1, "maxTokens": 2000},
        "messages": [{"role": "system", "text": SYSTEM_PROMPT}, {"role": "user", "text": content}],
    }
    headers = {"Authorization": f"Api-Key {YANDEX_KEY}", "Content-Type": "application/json"}
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
                    raw = res["result"]["alternatives"][0]["message"]["text"]
                    clean = re.sub(r"```json|```", "", raw).strip()
                    s = min((i for i in [clean.find("["), clean.find("{")] if i >= 0), default=0)
                    e = max(clean.rfind("]"), clean.rfind("}")) + 1
                    parsed = json.loads(clean[s:e])
                    if isinstance(parsed, dict): return parsed.get("results", [])
                    return parsed
                if resp.status == 429:
                    await asyncio.sleep(10 * (attempt + 1))
        except Exception as ex:
            print(f"  Ошибка запроса (попытка {attempt+1}): {ex}")
            await asyncio.sleep(5)
    return []


async def main():
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, text, valuation FROM wb_feedbacks
            WHERE ai_tags IS NULL OR NOT (ai_tags ? 'processed')
        """)).fetchall()

    total = len(rows)
    print(f"Всего к тегированию: {total}")
    if total == 0:
        print("Нечего делать.")
        return

    done = 0
    async with aiohttp.ClientSession() as session:
        for i in range(0, total, BATCH_SIZE):
            chunk = rows[i:i + BATCH_SIZE]
            batch_to_send, id_map, auto_skip = [], {}, []

            for idx, row in enumerate(chunk):
                fid, ftxt, val = str(row[0]), str(row[1] or "").strip(), int(row[2] or 5)
                if val == 5 and len(ftxt) < 60:
                    auto_skip.append(fid)
                else:
                    ref = f"REF_{idx}"
                    id_map[ref] = fid
                    batch_to_send.append({"id": ref, "text": ftxt})

            with engine.begin() as conn:
                for fid in auto_skip:
                    payload = json.dumps({"processed": True, "tags": [], "suggestion": ""}, ensure_ascii=False)
                    conn.execute(text("UPDATE wb_feedbacks SET ai_tags = CAST(:t AS jsonb) WHERE id = :id"), {"t": payload, "id": fid})

            if batch_to_send:
                results = await ask_yandex(session, batch_to_send)
                with engine.begin() as conn:
                    for res in results:
                        m = re.search(r"\d+", str(res.get("id", "")))
                        if not m: continue
                        real_id = id_map.get(f"REF_{m.group()}")
                        if not real_id: continue
                        payload = json.dumps({
                            "processed": True,
                            "tags": res.get("tags", []),
                            "suggestion": res.get("suggestion", "")
                        }, ensure_ascii=False)
                        conn.execute(text("UPDATE wb_feedbacks SET ai_tags = CAST(:t AS jsonb) WHERE id = :id"), {"t": payload, "id": real_id})

            done += len(chunk)
            pct = done * 100 // total
            print(f"  [{pct:3d}%] {done}/{total} обработано")

    print("Тегирование завершено!")

asyncio.run(main())
