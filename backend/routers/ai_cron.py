import asyncio
import os
import sys

# Гарантируем, что корень проекта находится в путях импорта
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.routers.ai import process_tagging_task
from backend.feedback_tagger_job import spawn_feedback_job


async def main():
    print("🚀 [КРОН ИИ] Старт: брак + отзывы через свободный лимит Qwen...")
    await process_tagging_task(model_key="qwen-free", batch_size=20)
    ok, msg = spawn_feedback_job("wb", "qwen-reviews", 20)
    print(f"🏁 [КРОН ИИ] Брак готов. Отзывы: {msg} (ok={ok})")


if __name__ == "__main__":
    asyncio.run(main())
