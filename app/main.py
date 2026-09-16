"""Общая точка входа: коллектор и бот администратора в одном процессе.

Запуск:
    python -m app.main

Модули независимы и общаются только через БД, поэтому здесь только запуск
двух корутин рядом и корректное завершение обеих, если одна из них упала.
"""

import asyncio
import logging

from dotenv import load_dotenv

from app.admin_bot.bot import run_admin_bot
from app.collector.runner import run_collector
from app.db.db import init_tables

logger = logging.getLogger(__name__)


async def main():
    load_dotenv()
    init_tables()

    tasks = [
        asyncio.create_task(run_collector(), name="collector"),
        asyncio.create_task(run_admin_bot(), name="admin_bot"),
    ]
    logger.info("Запускаем collector и admin_bot")

    try:
        await asyncio.gather(*tasks)
    finally:
        # Падение одного модуля не должно оставлять второй висеть в процессе
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено вручную")
