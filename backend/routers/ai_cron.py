import asyncio
import os
import sys

# Гарантируем, что корень проекта находится в путях импорта
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.routers.ai import process_tagging_task

async def main():
    print("🚀 [КРОН ИИ] Старт автоматической разметки через YandexGPT...")
    # Запускаем оригинальную фоновую задачу (модель yandex-lite, пачками по 10)
    await process_tagging_task(model_key="yandex-lite", batch_size=10)
    print("🏁 [КРОН ИИ] Автоматическая разметка успешно завершена.")

if __name__ == "__main__":
    asyncio.run(main())
