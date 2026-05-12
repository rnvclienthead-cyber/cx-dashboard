import os
import asyncio
import re
import random
from datetime import datetime
from sqlalchemy import create_engine, text
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

DB_URL = os.environ.get("DB_URL", "").strip()

def get_wb_cards_mapping_from_db(engine):
    """Получает связку nmId -> Артикул продавца из таблицы wb_logistics"""
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
        print(f"✅ Успешно собран маппинг из БД: {len(mapping)} товаров")
        return mapping
    except Exception as e:
        print(f"❌ Ошибка получения маппинга из БД: {e}")
        return {}

class WBRatingsParser:
    def __init__(self, concurrency_limit=5):
        self.semaphore = asyncio.Semaphore(concurrency_limit)
        self.results = []

    @staticmethod
    async def get_stats(page):
        """Extracts rating and reviews count."""
        rating, reviews = None, None
        elements = page.locator(".mo-typography")
        count = await elements.count()
        words = ("оценка", "оценки", "оценок")
        
        for i in range(count):
            text = (await elements.nth(i).text_content()).lower()
            if not rating:
                match_rating = re.search(r'(\d[.,]\d)', text)
                if match_rating:
                    rating = float(match_rating.group(1).replace(',', '.'))
            if any(word in text for word in words) and not reviews:
                match_reviews = re.sub(r'[^\d]', '', text)
                if match_reviews:
                    reviews = int(match_reviews)
        return rating, reviews

    async def parse_single_article(self, browser_context, article):
        async with self.semaphore:
            page = await browser_context.new_page()
            
            # Блокируем медиа для скорости
            await page.route("**/*.{png,jpg,jpeg,webp,svg,gif,woff,ttf,otf,mp4,mov}", lambda route: route.abort())
            await page.route("**/analytics/**", lambda route: route.abort())
            
            await Stealth().apply_stealth_async(page)
            target_url = f"https://www.wildberries.ru/catalog/{article}/detail.aspx"

            row_data = {
                "article": article,
                "rating_val": None,
                "reviews_count": None,
                "status": "success"
            }

            try:
                try:
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(0.4)
                except Exception as e:
                    if "Timeout" in str(e):
                        print(f"⚠️ Timeout on {article}. Reloading...")
                        await page.reload(wait_until="domcontentloaded", timeout=30000)
                    else:
                        raise e

                # Имитация человека: скролл
                await page.mouse.wheel(0, 1200)
                await asyncio.sleep(0.1)

                try:
                    await page.wait_for_selector(".product-page__header, h1", timeout=15000)
                except:
                    await page.keyboard.press("Space")
                    await asyncio.sleep(0.1)

                rating_val, reviews_count = await self.get_stats(page)

                row_data["rating_val"] = rating_val if rating_val else 0.0
                row_data["reviews_count"] = reviews_count if reviews_count else 0
                
                print(f"✓ {article} | Рейтинг: {row_data['rating_val']} | Отзывов: {row_data['reviews_count']}")

            except Exception as e:
                print(f"❌ Скип {article} из-за ошибки: {str(e)[:50]}...")
                row_data["status"] = "failed"
            finally:
                # Удаляем старую запись (защита от дублей при Retry)
                self.results = [r for r in self.results if r["article"] != article]
                self.results.append(row_data)
                await page.close()

    async def run(self, article_list):
        async with async_playwright() as p:
            # На серверах GitHub Actions обязательно headless=True
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={'width': 1620, 'height': 1380},
                    timezone_id='Europe/Moscow'
                )

                async def parse_with_delay(article):
                    await asyncio.sleep(random.uniform(1, 3))
                    return await self.parse_single_article(context, article)

                # --- ПЕРВЫЙ ПРОХОД ---
                print(f"🚀 [Проход 1/2] Запуск для {len(article_list)} артикулов...")
                tasks = [self.parse_single_article(context, article) for article in article_list]
                await asyncio.gather(*tasks)

                # --- ВТОРОЙ ПРОХОД (RETRY) ---
                to_retry = [res["article"] for res in self.results if res["status"] == "failed" or res["rating_val"] == 0.0]
                
                if to_retry:
                    print(f"🔄 [Проход 2/2] Найдено {len(to_retry)} проблемных артикулов. Запуск проверки...")
                    self.results = [r for r in self.results if r["article"] not in to_retry]
                    retry_tasks = [parse_with_delay(art) for art in to_retry]
                    await asyncio.gather(*retry_tasks)
                else:
                    print("✅ Все данные собраны с первого раза.")

            except Exception as e:
                print(f"❌ Критическая ошибка парсера: {e}")
            finally:
                await browser.close()
                
            return self.results

async def main():
    if not DB_URL:
        print("🚨 КРИТИЧЕСКАЯ ОШИБКА: Не найдена переменная DB_URL")
        return

    engine = create_engine(DB_URL)
    current_date = datetime.now().date()
    
    mapping = get_wb_cards_mapping_from_db(engine)
    if not mapping:
        print("⚠️ Нет маппинга для синхронизации.")
        return

    nm_ids_list = list(mapping.keys())
    
    parser = WBRatingsParser(concurrency_limit=5)
    parsed_data = await parser.run(nm_ids_list)

    if not parsed_data:
        print("⚠️ Нет данных для сохранения.")
        return

    upsert_query = text("""
        INSERT INTO wb_ratings (date, supplier_article, nm_id, average_rating, review_count)
        VALUES (:date, :supplier_article, :nm_id, :average_rating, :review_count)
        ON CONFLICT (date, supplier_article) 
        DO UPDATE SET 
            average_rating = EXCLUDED.average_rating,
            review_count = EXCLUDED.review_count,
            created_at = CURRENT_TIMESTAMP;
    """)

    success_count = 0
    with engine.begin() as conn:
        for item in parsed_data:
            nm_id = item["article"]
            supplier_article = mapping.get(nm_id)
            
            # Пропускаем, если статус failed или рейтинг так и не найден
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
                print(f"⚠️ Ошибка записи БД для {supplier_article}: {e}")

    print(f"🚀 Синхронизация завершена! Записано/обновлено строк: {success_count}")

if __name__ == "__main__":
    asyncio.run(main())
