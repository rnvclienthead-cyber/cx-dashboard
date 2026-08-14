import asyncio
import os
import sys

# Гарантируем, что корень проекта находится в путях импорта
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.routers.ai import process_tagging_task

async def main():
    print("🚀 [КРОН ИИ] Старт автоматической разметки через Qwen Plus...")
    # Запускаем оригинальную фоновую задачу (модель qwen-plus, пачками по 10)
    await process_tagging_task(model_key="qwen-plus", batch_size=10)
    print("🏁 [КРОН ИИ] Автоматическая разметка успешно завершена.")

if __name__ == "__main__":
    asyncio.run(main())
