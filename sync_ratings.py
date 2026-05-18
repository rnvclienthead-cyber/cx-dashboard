import os
import asyncio
import re
import random
from datetime import datetime
from sqlalchemy import create_engine, text
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

DB_URL = os.environ.get("DB_URL", "").strip()

# --- СИСТЕМА ЛОГИРОВАНИЯ ---
class Logger:
    def __init__(self):
        self.buffer = []
        self.has_errors = False

    def info(self, msg):
        print(msg)
        self.buffer.append(f"[INFO] {msg}")

    def error(self, msg):
        print(msg)
        self.buffer.append(f"[ERROR] {msg}")
        self.has_errors = True

    def get_full_log(self):
        return "\n".join(self.buffer)

logger = Logger()

def get_wb_cards_mapping_from_db(engine):
    mapping = {}
    try:
        query = text("""
            SELECT DISTINCT nm_id, supplier_article 
            FROM wb_logistics 
            WHERE nm_id IS NOT NULL AND supplier_article IS NOT NULL
        """)
        with engine.connect() as conn:
            result = conn.execute(query).fetchall()
            for row in result:
                mapping[row[0]] = str(row[1]).strip()
        logger.info(f"✅ Успешно собран маппинг из БД: {len(mapping)} товаров")
        return mapping
    except Exception as e:
        logger.error(f"❌ Ошибка получения маппинга из БД: {e}")
        return {}

class WBRatingsParser:
    def __init__(self, concurrency_limit=1):
        self.semaphore = asyncio.Semaphore(concurrency_limit)
        self.results = []

    @staticmethod
    async def get_stats(page):
        rating, reviews = None, None
        try:
            text_content = ""
            try:
                text_content = await page.inner_text(".product-page__header", timeout=3000)
            except:
                text_content = await page.inner_text("body")
            
            clean_text = text_content.lower().replace('\xa0', ' ')
            
            m_rev = re.search(r'([\d\s]+)\s*(оцен|отзыв)', clean_text)
            if m_rev:
                reviews = int(re.sub(r'\s+', '', m_rev.group(1)))
            
            m_rating = re.search(r'([1-4][.,]\d|5[.,]0)', text_content)
            if m_rating:
                rating = float(m_rating.group(1).replace(',', '.'))
        except Exception:
            pass 
        return rating, reviews

    async def parse_single_article(self, browser_context, article):
        async with self.semaphore:
            page = await browser_context.new_page()
            await page.route("**/*.{png,jpg,jpeg,webp,svg,gif,woff,ttf,otf,mp4,mov}", lambda route: route.abort())
            await page.route("**/analytics/**", lambda route: route.abort())
            await Stealth().apply_stealth_async(page)
            
            target_url = f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
            row_data = {"article": article, "rating_val": None, "reviews_count": None, "status": "success"}

            try:
                try:
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(2) 
                    
                    page_title = await page.title()
                    if "Почти готово" in page_title or "Внимание" in page_title or not page_title:
                        logger.info(f"⏳ {article} попал на анти-бота. Ждем 8 сек...")
                        await asyncio.sleep(8) 
                        
                except Exception as e:
                    if "Timeout" in str(e) or "TargetClosed" in str(e) or "ERR_" in str(e):
                        logger.info(f"⚠️ Сбой соединения у {article}. Перезагрузка...")
                        await asyncio.sleep(5)
                        await page.reload(wait_until="domcontentloaded", timeout=30000)
                    else:
                        raise e

                await page.mouse.wheel(0, 1200)
                await asyncio.sleep(0.5)

                try:
                    await page.wait_for_selector(".product-page__header, h1", timeout=15000)
                except:
                    await page.keyboard.press("Space")
                    await asyncio.sleep(0.1)

                rating_val, reviews_count = await self.get_stats(page)
                row_data["rating_val"] = rating_val if rating_val else 0.0
                row_data["reviews_count"] = reviews_count if reviews_count else 0
                
                logger.info(f"✓ {article} | Рейтинг: {row_data['rating_val']} | Отзывов: {row_data['reviews_count']}")

            except Exception as e:
                logger.error(f"❌ Скип {article} из-за ошибки: {str(e)[:50]}...")
                row_data["status"] = "failed"
            finally:
                self.results = [r for r in self.results if r["article"] != article]
                self.results.append(row_data)
                await page.close()

    async def run(self, article_list):
        async with async_playwright() as p:
            # Запуск обычного браузера без прокси
            browser = await p.chromium.launch(headless=True)
            
            try:
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={'width': 1920, 'height': 1080},
                    timezone_id='Europe/Moscow'
                )

                async def parse_with_delay(article):
                    await asyncio.sleep(random.uniform(1, 3))
                    return await self.parse_single_article(context, article)

                logger.info(f"🚀 [Проход 1/2] Запуск для {len(article_list)} артикулов...")
                tasks = [self.parse_single_article(context, article) for article in article_list]
                await asyncio.gather(*tasks)

                to_retry = [res["article"] for res in self.results if res["status"] == "failed" or res["rating_val"] == 0.0]
                if to_retry:
                    logger.info(f"🔄 [Проход 2/2] Перепроверка {len(to_retry)} артикулов...")
                    self.results = [r for r in self.results if r["article"] not in to_retry]
                    retry_tasks = [parse_with_delay(art) for art in to_retry]
                    await asyncio.gather(*retry_tasks)

            except Exception as e:
                logger.error(f"❌ Критическая ошибка парсера: {e}")
            finally:
                await browser.close()
            return self.results

def save_log_to_db(engine, details, has_errors):
    try:
        status = "WARNING" if has_errors else "SUCCESS"
        query = text("""
            INSERT INTO system_logs (action, status, details, created_at)
            VALUES (:action, :status, :details, CURRENT_TIMESTAMP)
        """)
        with engine.begin() as conn:
            conn.execute(query, {"action": "Синхронизация рейтингов", "status": status, "details": details})
        print("💾 Лог успешно сохранен в базу данных system_logs.")
    except Exception as e:
        print(f"⚠️ Ошибка записи лога в БД: {e}")

async def main():
    if not DB_URL:
        return
    engine = create_engine(DB_URL)
    current_date = datetime.now().date()
    
    mapping = get_wb_cards_mapping_from_db(engine)
    if not mapping:
        save_log_to_db(engine, logger.get_full_log(), logger.has_errors)
        return

    parser = WBRatingsParser(concurrency_limit=1)
    parsed_data = await parser.run(list(mapping.keys()))

    if not parsed_data:
        save_log_to_db(engine, logger.get_full_log(), logger.has_errors)
        return

    upsert_query = text("""
        INSERT INTO wb_ratings (date, supplier_article, nm_id, average_rating, review_count, last_sync)
        VALUES (:date, :supplier_article, :nm_id, :average_rating, :review_count, CURRENT_TIMESTAMP)
        ON CONFLICT (date, supplier_article) 
        DO UPDATE SET 
            average_rating = EXCLUDED.average_rating,
            review_count = EXCLUDED.review_count,
            last_sync = EXCLUDED.last_sync;
    """)

    success_count = 0
    with engine.begin() as conn:
        for item in parsed_data:
            nm_id = item["article"]
            supplier_article = mapping.get(nm_id)
            if not supplier_article or item["status"] == "failed":
                continue
            try:
                conn.execute(upsert_query, {
                    "date": current_date,
                    "supplier_article": str(supplier_article).strip(),
                    "nm_id": int(nm_id),
                    "average_rating": float(item['rating_val']),
                    "review_count": int(item['reviews_count'])
                })
                success_count += 1
            except Exception as e:
                logger.error(f"⚠️ Ошибка записи БД для {supplier_article}: {e}")

    logger.info(f"🚀 Синхронизация завершена! Записано/обновлено строк: {success_count}")
    
    save_log_to_db(engine, logger.get_full_log(), logger.has_errors)

if __name__ == "__main__":
    asyncio.run(main())
