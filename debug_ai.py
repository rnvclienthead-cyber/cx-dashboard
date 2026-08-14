import asyncio
import traceback
import sys
from backend.database import SessionLocal
from backend.routers.ai import process_feedback_tagging_task

async def main():
    print("🚀 [1/3] Инициализация ручного запуска...")
    try:
        print("⏳ [2/3] Вызов функции тегирования (берем 5 отзывов для теста)...")
        await process_feedback_tagging_task("qwen-plus", 5)
        print("✅ [3/3] Скрипт отработал до конца!")
    except Exception as e:
        print("\n❌ КРИТИЧЕСКАЯ ОШИБКА:")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
