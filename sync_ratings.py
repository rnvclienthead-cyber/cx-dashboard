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
        """Бронебойный поиск рейтинга без привязки к конкретным CSS-классам."""
        rating, reviews = None, None
        try:
            # Пытаемся взять шапку, если структура изменилась - берем весь текст страницы
            text_content = ""
            try:
                text_content = await page.inner_text(".product-page__header", timeout=3000)
            except:
                text_content = await page.inner_text("body")
            
            # Очищаем текст от неразрывных пробелов для поиска количества отзывов (1 234 -> 1234)
            clean_text = text_content.lower().replace('\xa0', '').replace(' ', '')
            
            # Ищем количество отзывов (любые цифры перед словом оценк или отзыв)
            m_rev = re.search(r'(\d+)(оцен|отзыв)', clean_text)
            if m_rev:
                reviews = int(m_rev.group(1))
            
            # Ищем сам рейтинг (цифры в формате X.X или X,X от 1 до 5)
            m_rating = re.search(r'([1-4][.,]\d|5[.,]0)', text_content)
            if m_rating:
                rating = float(m_rating.group(1).replace(',', '.'))
                
        except Exception as e:
            pass # Если не нашли, вернем None, чтобы скрипт проставил 0.0
            
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

                # --- ДИАГНОСТИКА КАПЧИ ---
                page_title = await page.title()
                if not page_title or ("Wildberries" not in page_title and "Вайлдберриз" not in page_title):
                    print(f"⚠️ Подозрительная страница у {article}. Заголовок: '{page_title}' (Возможно капча!)")

                # Имитация человека: скролл
                await page.mouse.wheel(0, 1200)
                await asyncio.sleep(0.1)

                try:
                    await page.wait_for_selector(".product-page__header, h1", timeout=15000)
                except:
                    await page.keyboard.press("Space")
                    await asyncio.sleep(0.1)

                # Вызов нового метода
                rating_val, reviews_count = await self.get_stats(page)

                row_data["rating_val"] = rating_val if rating_val else 0.0
                row_data["reviews_count"] = reviews_count if reviews_count else 0

                # --- ВИЗУАЛЬНЫЙ ДЕБАГ ---
                if row_data["rating_val"] == 0.0:
                    screenshot_path = f"/root/my_project/debug_wb_{article}.png"
                    await page.screenshot(path=screenshot_path, full_page=True)
                    print(f"📸 ВБ не отдал рейтинг! Скриншот экрана сохранен: {screenshot_path}")
                # -----------------------
                
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
            # Запускаем браузер с использованием купленного прокси
            browser = await p.chromium.launch(
                headless=True,
                proxy={
                    "server": "http://res.lteboost.com:1000",
                    "username": "user_fb89cb12",
                    "password": "rDHSZZazHs6VbzsfGQUA"
                }
            )
            try:
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={'width': 1620, 'height': 1380},
                    timezone_id='Europe/Moscow'
                )

                # --- ДИАГНОСТИКА ПРОКСИ ---
                print("🔍 Проверка прокси-соединения...")
                test_page = await context.new_page()
                try:
                    # Идем на независимый сервис проверки IP
                    response = await test_page.goto("https://api.ipify.org?format=json", timeout=15000)
                    ip_data = await response.json()
                    current_ip = ip_data.get('ip')
                    print(f"🌐 Внешний IP браузера: {current_ip}")
                    
                    if current_ip == "IP_ТВОЕГО_СЕРВЕРА_VPS":
                        print("⚠️ ВНИМАНИЕ: Прокси не сработал! Браузер идет напрямую с сервера.")
                    else:
                        print("✅ Прокси работает успешно! Начинаем парсинг ВБ...")
                except Exception as e:
                    print(f"❌ ОШИБКА ПРОКСИ: Не удалось выйти в интернет. Проверьте данные прокси. Детали: {e}")
                    return self.results # Прерываем скрипт, так как ВБ тоже не загрузится
                finally:
                    await test_page.close()
                # ---------------------------

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
