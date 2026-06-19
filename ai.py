import os
import re
import json
import asyncio
import aiohttp
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Dict, Any
from ..database import get_db, SessionLocal
from .auth import get_current_user
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/api/v1/ai", tags=["AI / Автоматическое тегирование"], dependencies=[Depends(get_current_user)])

CATEGORIES = {
    1: "Некомплект: Фурнитура", 2: "Некомплект: Несущие детали", 3: "Состояние упаковки",
    4: "Производственный дефект", 5: "Механические повреждения", 6: "Инструкция и сборка",
    7: "Хлипкость / Устойчивость", 8: "Пересорт / Ошибка склада", 9: "Качество материалов",
    10: "Габариты и Размер", 11: "Несоответствие описанию", 12: "Субъективное 'Не подошло'",
    13: "Следы использования / Б/У"
}

def parse_ai_response(text_data: str) -> List[Dict[str, Any]]:
    try:
        clean_text = re.sub(r'```json|```', '', str(text_data)).strip()
        start = min([i for i in [clean_text.find('['), clean_text.find('{')] if i >= 0] or [0])
        end = max(clean_text.rfind(']'), clean_text.rfind('}')) + 1
        if start >= 0 and end > 0:
            clean_text = clean_text[start:end]
            
        parsed = json.loads(clean_text)
        if isinstance(parsed, dict): return parsed.get('results', [])
        elif isinstance(parsed, list): return parsed
        return [{"error": f"Неожиданный формат: {type(parsed)}"}]
    except Exception as e:
        return [{"error": f"Сбой формата JSON: {str(e)} | Ответ ИИ: {text_data}"}]

def find_similar_examples_sql(target_text: str, db: Session, top_n: int = 2) -> str:
    if not target_text: return "Опыта пока нет."
    sql = text("""
        SELECT content, tags, similarity(content, :target_text) as sml
        FROM ai_knowledge_base
        WHERE similarity(content, :target_text) > 0.05
        ORDER BY sml DESC LIMIT :top_n
    """)
    try:
        results = db.execute(sql, {"target_text": target_text, "top_n": top_n}).fetchall()
        if not results: return "Прямых совпадений в опыте не найдено."
        return "\n".join([f"Текст: {row[0]} -> Тег: {row[1]}" for row in results])
    except Exception as e:
        print(f"Ошибка поиска в базе знаний: {e}")
        return "Ошибка доступа к опыту."

def add_system_log_db(db: Session, action: str, status: str, details: str = ""):
    sql = text("INSERT INTO system_logs (action, status, details) VALUES (:action, :status, :details)")
    try:
        db.execute(sql, {"action": action, "status": status, "details": details})
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"🚨 Ошибка записи лога: {e}")

# ==========================================
# СТАТИСТИКА ДЛЯ ФРОНТЕНДА
# ==========================================
@router.get("/stats")
def get_ai_stats(
    platform: str = Query("wb", pattern="^(wb|ym|all)$"),
    db: Session = Depends(get_db)
):
    try:
        if platform == "ym":
            untagged_feedbacks = db.execute(text("""
                SELECT COUNT(*) FROM ym_feedbacks
                WHERE ai_tags IS NULL OR NOT (ai_tags ? 'processed')
            """)).scalar() or 0
            log_res = db.execute(text("""
                SELECT action, status, details FROM system_logs
                WHERE action LIKE '%ЯМ%' OR action LIKE '%ym%'
                ORDER BY id DESC LIMIT 1
            """)).fetchone()
            return {
                "status": "success",
                "untagged_count": 0,
                "unaudited_count": 0,
                "untagged_feedbacks": untagged_feedbacks,
                "last_log": {"action": log_res[0], "status": log_res[1], "details": log_res[2]} if log_res else None,
            }

        # WB
        untagged = db.execute(text("""
            SELECT COUNT(*) FROM wb_claims
            WHERE NOT (cat_1 OR cat_2 OR cat_3 OR cat_4 OR cat_5 OR cat_6 OR cat_7 OR cat_8 OR cat_9 OR cat_10 OR cat_11 OR cat_12 OR cat_13)
            AND (audit_status IS NULL OR audit_status != 'Пропущено ИИ')
        """)).scalar() or 0
        unaudited = db.execute(text("""
            SELECT COUNT(*) FROM wb_claims
            WHERE (cat_1 OR cat_2 OR cat_3 OR cat_4 OR cat_5 OR cat_6 OR cat_7 OR cat_8 OR cat_9 OR cat_10 OR cat_11 OR cat_12 OR cat_13)
            AND (audit_status IS NULL OR audit_status = '')
        """)).scalar() or 0
        untagged_feedbacks = db.execute(text("""
            SELECT COUNT(*) FROM wb_feedbacks
            WHERE ai_tags IS NULL OR NOT (ai_tags ? 'processed')
        """)).scalar() or 0
        log_res = db.execute(text("""
            SELECT action, status, details FROM system_logs
            WHERE action LIKE '%ИИ%' OR action LIKE '%Пачка%' OR action LIKE '%Аудит%' OR action LIKE '%Отзывы%'
            ORDER BY id DESC LIMIT 1
        """)).fetchone()
        return {
            "status": "success",
            "untagged_count": untagged,
            "unaudited_count": unaudited,
            "untagged_feedbacks": untagged_feedbacks,
            "last_log": {"action": log_res[0], "status": log_res[1], "details": log_res[2]} if log_res else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# НЕЙРОСЕТЬ: ОТПРАВКА ДЛЯ БРАКА (ПРЕТЕНЗИЙ)
# ==========================================
async def fetch_ai_tags(session: aiohttp.ClientSession, batch: List[Dict[str, Any]], memory_string: str, model_key: str) -> List[Dict[str, Any]]:
    content_lines = [f"ID {i['id']}: {i['text']}" for i in batch]
    content = "\n".join(content_lines)

    system_prompt = f"""Ты эксперт контроля качества. 
    Категории (ID: Название): {json.dumps(CATEGORIES, ensure_ascii=False)}
    ПРАВИЛО 12: Если клиент хвалит, но есть мелкий дефект (рейтинг 4-5) - СТРОГО Категория 12.
    ВОТ ПРИМЕРЫ ПОХОЖИХ СИТУАЦИЙ ИЗ БАЗЫ:
    {memory_string}
    ИНСТРУКЦИЯ: Верни ТОЛЬКО массив category_ids (цифры подходящих категорий). Никакого текста!
    ОТВЕТЬ СТРОГО JSON: {{"results": [{{"id": "...", "category_ids": [1, 5]}}]}}"""

    if "yandex" in model_key:
        url = 'https://llm.api.cloud.yandex.net/foundationModels/v1/completion'
        api_key = os.getenv('YANDEX_API_KEY') or ""
        folder_id = os.getenv('FOLDER_ID') or ""
        headers = {"Authorization": f"Api-Key {api_key}", "Content-Type": "application/json"}
        if folder_id: headers["x-folder-id"] = str(folder_id)
            
        yandex_model_name = "yandexgpt-lite" if model_key == "yandex-lite" else "yandexgpt"
        payload = {
            "modelUri": f"gpt://{folder_id}/{yandex_model_name}/latest",
            "completionOptions": {"temperature": 0.1, "maxTokens": 2000},
            "messages": [{"role": "system", "text": system_prompt}, {"role": "user", "text": content}]
        }
        
        for attempt in range(3):
            try:
                async with session.post(url, headers=headers, json=payload, timeout=90) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        return parse_ai_response(res['result']['alternatives'][0]['message']['text'])
                    elif resp.status == 429:
                        await asyncio.sleep(10 * (attempt + 1))
                        continue
                    return [{"error": f"Ошибка Яндекса ({resp.status}): {await resp.text()}"}]
            except Exception as e:
                if attempt == 2: return [{"error": str(e)}]
                await asyncio.sleep(5)
    return []

# ==========================================
# НЕЙРОСЕТЬ: ОТПРАВКА ДЛЯ ОТЗЫВОВ (VOC)
# ==========================================
async def fetch_ai_feedback_tags(session: aiohttp.ClientSession, batch: List[Dict[str, Any]], model_key: str, examples_text: str) -> List[Dict[str, Any]]:
    content_lines = [f"ID {i['id']}: {i['text']}" for i in batch]
    content = "\n".join(content_lines)

    system_prompt = f"""Ты Product Manager. Проанализируй отзывы покупателей и классифицируй их строго по Иерархической матрице болей.
    
    ДОПУСТИМЫЕ ТЕГИ (Выбирай один или несколько строго из этого списка):
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

    {examples_text}

    ИНСТРУКЦИЯ:
    Верни результат СТРОГО в виде JSON-объекта с массивом `results`. 
    Для КАЖДОГО отзыва из запроса ты обязан вернуть его `id` (REF_...), массив `tags` и строку `suggestion`.
    Если проблем нет (или это жалоба на логистику) - оставляй массив тегов пустым.

    ПРИМЕР ИДЕАЛЬНОГО ОТВЕТА (ШАБЛОН):
    {{"results": [
        {{"id": "REF_0", "tags": ["ЭРГОНОМИКА: Мало места / Вместимость"], "suggestion": "Увеличить ширину"}},
        {{"id": "REF_1", "tags": [], "suggestion": ""}}
    ]}}"""

    if "yandex" in model_key:
        url = 'https://llm.api.cloud.yandex.net/foundationModels/v1/completion'
        api_key = os.getenv('YANDEX_API_KEY') or ""
        folder_id = os.getenv('FOLDER_ID') or ""
        headers = {"Authorization": f"Api-Key {api_key}", "Content-Type": "application/json"}
        if folder_id: headers["x-folder-id"] = str(folder_id)
            
        yandex_model_name = "yandexgpt-lite" if model_key == "yandex-lite" else "yandexgpt"
        payload = {
            "modelUri": f"gpt://{folder_id}/{yandex_model_name}/latest",
            "completionOptions": {"temperature": 0.1, "maxTokens": 2000},
            "messages": [{"role": "system", "text": system_prompt}, {"role": "user", "text": content}]
        }
        
        for attempt in range(3):
            try:
                async with session.post(url, headers=headers, json=payload, timeout=90) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        return parse_ai_response(res['result']['alternatives'][0]['message']['text'])
                    elif resp.status == 429:
                        await asyncio.sleep(5 * (attempt + 1))
                        continue
                    return [{"error": f"Ошибка Яндекса ({resp.status})"}]
            except Exception as e:
                if attempt == 2: return [{"error": str(e)}]
                await asyncio.sleep(3)
    return []

# --- ФОНОВЫЕ ЗАДАЧИ ---
async def process_tagging_task(model_key: str, batch_size: int):
    db = SessionLocal()
    try:
        query = """
            SELECT srid, supplier_article, user_comment 
            FROM wb_claims 
            WHERE NOT (cat_1 OR cat_2 OR cat_3 OR cat_4 OR cat_5 OR cat_6 OR cat_7 OR cat_8 OR cat_9 OR cat_10 OR cat_11 OR cat_12 OR cat_13)
            AND (audit_status IS NULL OR audit_status != 'Пропущено ИИ')
        """
        rows = db.execute(text(query)).fetchall()
        total_rows = len(rows)
        if total_rows == 0: return

        add_system_log_db(db, "Бэкенд ИИ: Старт", "INFO", f"Запущено тегирование {total_rows} строк через {model_key}")

        async with aiohttp.ClientSession() as session:
            for i in range(0, total_rows, batch_size):
                chunk = rows[i:i+batch_size]
                memory_list = []
                for row in chunk:
                    if row[2]:
                        mem = find_similar_examples_sql(str(row[2]), db, top_n=2)
                        if mem and "Прямых совпадений" not in mem: memory_list.append(mem)
                chunk_memory = "\n".join(set(memory_list)) if memory_list else "Опыта пока нет."

                batch_to_send = []
                srid_map = {}
                for idx, row in enumerate(chunk):
                    ref_id = f"REF_{idx}"
                    srid_map[ref_id] = str(row[0])
                    batch_to_send.append({"id": ref_id, "text": f"Артикул: {row[1]}. Текст: {row[2]}"})

                results = await fetch_ai_tags(session, batch_to_send, chunk_memory, model_key)
                
                if results:
                    saved_count = 0
                    batch_details = []
                    for res in results:
                        if "error" in res:
                            batch_details.append(f"❌ Ошибка ИИ: {res.get('error')}")
                            continue
                        
                        num_match = re.search(r'\d+', str(res.get('id', '')).upper())
                        if not num_match: continue
                        
                        real_srid = srid_map.get(f"REF_{num_match.group()}")
                        cats_array = res.get('category_ids', [])

                        if real_srid:
                            if cats_array:
                                updates = [f"cat_{re.search(r'\d+', str(c)).group()} = true" for c in cats_array if re.search(r'\d+', str(c))]
                                if updates:
                                    db.execute(text(f"UPDATE wb_claims SET {', '.join(updates)} WHERE srid = :srid"), {"srid": real_srid})
                                    saved_count += 1
                                    batch_details.append(f"✅ SRID {real_srid}: теги {cats_array}")
                            else:
                                db.execute(text("UPDATE wb_claims SET audit_status = 'Пропущено ИИ' WHERE srid = :srid"), {"srid": real_srid})
                                batch_details.append(f"⚠️ SRID {real_srid}: ИИ пропустил")
                    
                    db.commit()
                    add_system_log_db(db, f"Пачка {i}", "SUCCESS" if saved_count > 0 else "WARNING", f"Обработано {saved_count} из {len(chunk)}\n" + "\n".join(batch_details))
                    await asyncio.sleep(3)
    except Exception as e: print(f"Ошибка тегирования: {e}")
    finally: db.close()

async def process_feedback_tagging_task(model_key: str, batch_size: int):
    db = SessionLocal()
    try:
        examples_query = text("SELECT content, tags, suggestion FROM ai_feedback_knowledge_base ORDER BY RANDOM() LIMIT 5")
        examples_rows = db.execute(examples_query).fetchall()
        examples_text = ""
        if examples_rows:
            examples_text = "ПРИМЕРЫ АНАЛИЗА (ОБРАЗЕЦ ЛОГИКИ):\n"
            for ex in examples_rows:
                tags_str = '[]'
                if ex.tags:
                    tags_arr = [f'"{t.strip()}"' for t in ex.tags.split(',') if t.strip()]
                    tags_str = f"[{', '.join(tags_arr)}]"
                sugg = ex.suggestion if ex.suggestion else ""
                examples_text += f"- Текст: {ex.content}\n  Теги: {tags_str}. Идея: '{sugg}'\n\n"

        query = text("""
            SELECT id, text, valuation 
            FROM wb_feedbacks 
            WHERE ai_tags IS NULL OR NOT (ai_tags ? 'processed')
            LIMIT 2000
        """)
        rows = db.execute(query).fetchall()
        if not rows: return

        add_system_log_db(db, "ИИ Отзывы: Старт", "INFO", f"Взято в работу {len(rows)} отзывов. Примеров: {len(examples_rows)}")

        async with aiohttp.ClientSession() as session:
            for i in range(0, len(rows), batch_size):
                chunk = rows[i:i+batch_size]
                batch_to_send = []
                id_map = {}
                skipped_count = 0
                
                try:
                    for idx, row in enumerate(chunk):
                        f_id = str(row[0])
                        f_text = str(row[1]).strip()
                        val = int(row[2]) if row[2] else 5
                        
                        if val == 5 and len(f_text) < 60:
                            empty_payload = json.dumps({"processed": True, "tags": [], "suggestion": ""}, ensure_ascii=False)
                            db.execute(text("UPDATE wb_feedbacks SET ai_tags = CAST(:tags AS jsonb) WHERE id = :id"), {"tags": empty_payload, "id": f_id})
                            skipped_count += 1
                        else:
                            ref_id = f"REF_{idx}"
                            id_map[ref_id] = f_id
                            batch_to_send.append({"id": ref_id, "text": f_text})
                    db.commit()
                except Exception as prep_e:
                    db.rollback()
                    add_system_log_db(db, f"Отзывы Пачка {i}", "ERROR", f"Сбой БД при фильтрации: {str(prep_e)}")
                    continue 

                if batch_to_send:
                    try:
                        results = await fetch_ai_feedback_tags(session, batch_to_send, model_key, examples_text)
                        
                        if not results:
                            add_system_log_db(db, f"Отзывы Пачка {i}", "ERROR", "Яндекс завис или вернул пустой ответ")
                            continue
                            
                        ai_saved_count = 0
                        error_details = []
                        
                        for res in results:
                            if "error" in res:
                                error_details.append(res["error"])
                                continue
                                
                            ref_id = res.get('id', '')
                            f_id = id_map.get(ref_id)
                            
                            if f_id:
                                json_payload = json.dumps({
                                    "processed": True, 
                                    "tags": res.get('tags', []), 
                                    "suggestion": res.get('suggestion', '')
                                }, ensure_ascii=False)
                                
                                db.execute(text("UPDATE wb_feedbacks SET ai_tags = CAST(:tags AS jsonb) WHERE id = :id"), {"tags": json_payload, "id": f_id})
                                ai_saved_count += 1
                                
                        db.commit()
                        
                        if error_details:
                            add_system_log_db(db, f"Отзывы Пачка {i}", "WARNING", f"Размечено ИИ: {ai_saved_count}. Ошибка АПИ: {error_details[0]}")
                        else:
                            add_system_log_db(db, f"Отзывы Пачка {i}", "SUCCESS", f"Размечено ИИ: {ai_saved_count}. Отброшено: {skipped_count}")
                            
                        await asyncio.sleep(2)
                    except Exception as inner_e:
                        db.rollback()
                        add_system_log_db(db, f"Отзывы Пачка {i}", "ERROR", f"Критический сбой кода: {str(inner_e)}")
                else:
                    add_system_log_db(db, f"Отзывы Пачка {i}", "SUCCESS", f"Вся пачка ({skipped_count}) отсеяна фильтром.")
                    
    except Exception as e:
        db.rollback()
        add_system_log_db(db, "ИИ Отзывы: FATAL", "ERROR", f"Полный сбой воркера: {str(e)}")
    finally:
        db.close()

async def process_ym_feedback_tagging_task(model_key: str, batch_size: int):
    db = SessionLocal()
    try:
        examples_rows = db.execute(text(
            "SELECT content, tags, suggestion FROM ai_feedback_knowledge_base ORDER BY RANDOM() LIMIT 5"
        )).fetchall()
        examples_text = ""
        if examples_rows:
            examples_text = "ПРИМЕРЫ АНАЛИЗА (ОБРАЗЕЦ ЛОГИКИ):\n"
            for ex in examples_rows:
                tags_str = "[]"
                if ex.tags:
                    tags_arr = [f'"{t.strip()}"' for t in ex.tags.split(',') if t.strip()]
                    tags_str = f"[{', '.join(tags_arr)}]"
                examples_text += f"- Текст: {ex.content}\n  Теги: {tags_str}. Идея: '{ex.suggestion or ''}'\n\n"

        rows = db.execute(text("""
            SELECT id,
                   TRIM(CONCAT_WS(' ', NULLIF(pro_text,''), NULLIF(contra_text,''), NULLIF(comment,''))) AS combined_text,
                   valuation
            FROM ym_feedbacks
            WHERE ai_tags IS NULL OR NOT (ai_tags ? 'processed')
            LIMIT 2000
        """)).fetchall()

        if not rows:
            add_system_log_db(db, "ИИ Отзывы ЯМ", "INFO", "Нет необработанных отзывов")
            return

        add_system_log_db(db, "ИИ Отзывы ЯМ: Старт", "INFO", f"Взято в работу {len(rows)} отзывов ЯМ. Примеров: {len(examples_rows)}")

        async with aiohttp.ClientSession() as session:
            for i in range(0, len(rows), batch_size):
                chunk = rows[i:i+batch_size]
                batch_to_send = []
                id_map = {}
                skipped_count = 0
                try:
                    for idx, row in enumerate(chunk):
                        f_id = str(row[0])
                        f_text = str(row[1] or "").strip()
                        val = int(row[2]) if row[2] else 5
                        if val == 5 and len(f_text) < 60:
                            empty_payload = json.dumps({"processed": True, "tags": [], "suggestion": ""}, ensure_ascii=False)
                            db.execute(text("UPDATE ym_feedbacks SET ai_tags = CAST(:tags AS jsonb) WHERE id = :id"), {"tags": empty_payload, "id": f_id})
                            skipped_count += 1
                        else:
                            ref_id = f"REF_{idx}"
                            id_map[ref_id] = f_id
                            batch_to_send.append({"id": ref_id, "text": f_text})
                    db.commit()
                except Exception as prep_e:
                    db.rollback()
                    add_system_log_db(db, f"ЯМ Отзывы Пачка {i}", "ERROR", f"Сбой БД при фильтрации: {str(prep_e)}")
                    continue

                if batch_to_send:
                    try:
                        results = await fetch_ai_feedback_tags(session, batch_to_send, model_key, examples_text)
                        if not results:
                            add_system_log_db(db, f"ЯМ Отзывы Пачка {i}", "ERROR", "Яндекс завис или вернул пустой ответ")
                            continue
                        ai_saved_count = 0
                        error_details = []
                        for res in results:
                            if "error" in res:
                                error_details.append(res["error"])
                                continue
                            f_id = id_map.get(res.get('id', ''))
                            if f_id:
                                json_payload = json.dumps({"processed": True, "tags": res.get('tags', []), "suggestion": res.get('suggestion', '')}, ensure_ascii=False)
                                db.execute(text("UPDATE ym_feedbacks SET ai_tags = CAST(:tags AS jsonb) WHERE id = :id"), {"tags": json_payload, "id": f_id})
                                ai_saved_count += 1
                        db.commit()
                        if error_details:
                            add_system_log_db(db, f"ЯМ Отзывы Пачка {i}", "WARNING", f"Размечено ИИ: {ai_saved_count}. Ошибка АПИ: {error_details[0]}")
                        else:
                            add_system_log_db(db, f"ЯМ Отзывы Пачка {i}", "SUCCESS", f"Размечено ИИ: {ai_saved_count}. Отброшено: {skipped_count}")
                        await asyncio.sleep(2)
                    except Exception as inner_e:
                        db.rollback()
                        add_system_log_db(db, f"ЯМ Отзывы Пачка {i}", "ERROR", f"Критический сбой: {str(inner_e)}")
                else:
                    add_system_log_db(db, f"ЯМ Отзывы Пачка {i}", "SUCCESS", f"Вся пачка ({skipped_count}) отсеяна фильтром.")
    except Exception as e:
        db.rollback()
        add_system_log_db(db, "ИИ Отзывы ЯМ: FATAL", "ERROR", f"Полный сбой воркера: {str(e)}")
    finally:
        db.close()


# --- ЭНДПОИНТЫ ЗАПУСКА ---
@router.post("/start-tagging")
def start_tagging(model: str = "yandex-lite", batch_size: int = 10, background_tasks: BackgroundTasks = None):
    background_tasks.add_task(process_tagging_task, model, batch_size)
    return {"status": "started", "message": "Процесс разметки запущен в фоне."}

@router.post("/start-audit")
def start_audit(model: str = "grok", batch_size: int = 10, background_tasks: BackgroundTasks = None):
    return {"status": "started", "message": "Процесс аудита запущен в фоне."}

@router.post("/start-feedback-tagging")
def start_feedback_tagging(
    platform: str = "wb",
    model: str = "yandex-lite",
    batch_size: int = 15,
    background_tasks: BackgroundTasks = None
):
    if platform == "ym":
        background_tasks.add_task(process_ym_feedback_tagging_task, model, batch_size)
        return {"status": "started", "message": "Разметка отзывов ЯМ запущена."}
    background_tasks.add_task(process_feedback_tagging_task, model, batch_size)
    return {"status": "started", "message": "Разметка отзывов запущена."}

# =====================================================================
# БЛОК МОДЕРАЦИИ И САМООБУЧЕНИЯ
# =====================================================================
from pydantic import BaseModel
from typing import List

class ModerationAction(BaseModel):
    srid: str
    action: str
    categories: List[int] = []

class KnowledgeItem(BaseModel):
    content: str
    tags: str

class FeedbackKnowledgeBulk(BaseModel):
    items: List[Dict[str, Any]]

@router.get("/moderation/queue")
def get_moderation_queue(db: Session = Depends(get_db)):
    query = text("""
        SELECT 
            srid as srid, created_dt as claim_date, supplier_article as sku, user_comment as comment, audit_status as audit_status,
            cat_1, cat_2, cat_3, cat_4, cat_5, cat_6, cat_7, cat_8, cat_9, cat_10, cat_11, cat_12, cat_13
        FROM wb_claims
        WHERE (cat_1 OR cat_2 OR cat_3 OR cat_4 OR cat_5 OR cat_6 OR cat_7 OR cat_8 OR cat_9 OR cat_10 OR cat_11 OR cat_12 OR cat_13)
        AND (correction IS NULL OR TRIM(correction) = '')
        AND (LOWER(TRIM(status)) IN ('одобрено', '2', '2.0', 'да', 'true') 
             OR LOWER(TRIM(status_ex)) IN ('одобрено', '2', '2.0', 'да', 'true'))
        ORDER BY created_dt DESC LIMIT 500
    """)
    try:
        with db.bind.connect() as conn:
            rows = conn.execute(query).mappings().all()
            return {"status": "success", "data": [dict(row) for row in rows]}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/moderation/action")
def process_moderation_action(payload: ModerationAction, db: Session = Depends(get_db)):
    try:
        with db.bind.connect() as conn:
            if payload.action == "confirm":
                conn.execute(text("UPDATE wb_claims SET correction = 'Подтверждено' WHERE srid = :srid"), {"srid": payload.srid})
            elif payload.action == "correct":
                updates = [f"cat_{i} = False" for i in range(1, 14)]
                for cat_id in payload.categories:
                    updates[cat_id - 1] = f"cat_{cat_id} = True"
                correction_text = "; ".join([CATEGORIES[i] for i in payload.categories])
                
                update_sql = f"UPDATE wb_claims SET {', '.join(updates)}, correction = :corr WHERE srid = :srid"
                conn.execute(text(update_sql), {"corr": correction_text, "srid": payload.srid})
                
                claim_text = conn.execute(text("SELECT user_comment FROM wb_claims WHERE srid = :srid"), {"srid": payload.srid}).scalar()
                if claim_text:
                    conn.execute(text("""
                        INSERT INTO ai_knowledge_base (content, tags, source) 
                        VALUES (:txt, :tgs, 'manual')
                        ON CONFLICT (content) DO UPDATE SET tags = EXCLUDED.tags
                    """), {"txt": claim_text, "tgs": correction_text})
            conn.commit()
            add_system_log_db(db, "Модерация", "SUCCESS", f"SRID {payload.srid}: {payload.action}")
            return {"status": "success"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

# =====================================================================
# БАЗА ЗНАНИЙ (ОБУЧЕНИЕ ИИ)
# =====================================================================
@router.get("/knowledge/feedback")
def get_feedback_knowledge(db: Session = Depends(get_db)):
    try:
        query = text("SELECT id, content, tags, suggestion, source FROM ai_feedback_knowledge_base ORDER BY id DESC")
        with db.bind.connect() as conn:
            rows = conn.execute(query).mappings().all()
            return {"status": "success", "data": [dict(r) for r in rows]}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.post("/knowledge/feedback/bulk")
def upload_feedback_knowledge(payload: FeedbackKnowledgeBulk, db: Session = Depends(get_db)):
    try:
        with db.bind.connect() as conn:
            for item in payload.items:
                conn.execute(text("""
                    INSERT INTO ai_feedback_knowledge_base (content, tags, suggestion, source) 
                    VALUES (:txt, :tags, :sugg, 'csv_upload')
                    ON CONFLICT (content) DO UPDATE SET tags = EXCLUDED.tags, suggestion = EXCLUDED.suggestion
                """), {"txt": item['content'], "tags": item.get('tags', ''), "sugg": item.get('suggestion', '')})
            conn.commit()
            add_system_log_db(db, "База знаний (VOC)", "INFO", f"Загружено {len(payload.items)} примеров")
            return {"status": "success", "count": len(payload.items)}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.delete("/knowledge/feedback/{item_id}")
def delete_fb_knowledge_item(item_id: int, db: Session = Depends(get_db)):
    try:
        with db.bind.connect() as conn:
            conn.execute(text("DELETE FROM ai_feedback_knowledge_base WHERE id = :id"), {"id": item_id})
            conn.commit()
            return {"status": "success"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.get("/knowledge")
def get_knowledge_base(db: Session = Depends(get_db)):
    try:
        query = text("SELECT id, content, tags, source FROM ai_knowledge_base ORDER BY id DESC LIMIT 1000")
        with db.bind.connect() as conn:
            rows = conn.execute(query).mappings().all()
            return {"status": "success", "data": [dict(r) for r in rows]}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.post("/knowledge")
def add_knowledge_item(item: KnowledgeItem, db: Session = Depends(get_db)):
    try:
        with db.bind.connect() as conn:
            query = text("""
                INSERT INTO ai_knowledge_base (content, tags, source) 
                VALUES (:content, :tags, 'manual')
                ON CONFLICT (content) DO UPDATE SET tags = EXCLUDED.tags
                RETURNING id
            """)
            result = conn.execute(query, {"content": item.content, "tags": item.tags})
            conn.commit()
            add_system_log_db(db, "База знаний", "INFO", f"Добавлено новое правило вручную: {item.tags}")
            return {"status": "success", "id": result.scalar()}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@router.delete("/knowledge/{item_id}")
def delete_knowledge_item(item_id: int, db: Session = Depends(get_db)):
    try:
        with db.bind.connect() as conn:
            conn.execute(text("DELETE FROM ai_knowledge_base WHERE id = :id"), {"id": item_id})
            conn.commit()
            add_system_log_db(db, "База знаний", "WARNING", f"Правило ID {item_id} удалено")
            return {"status": "success"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))