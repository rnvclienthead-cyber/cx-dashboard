#!/usr/bin/env python3
"""Отдельный процесс фоновых задач CX Dashboard. Сайт его не ждёт."""
import asyncio
import logging
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.bg_runtime import run_forever  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("cx.bg").info("cx-bg process starting")
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
