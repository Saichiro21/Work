import asyncio
import logging

from app.collector.bot import create_collector_bot
from app.collector.config import get_collected_chat_ids
from app.db.db import init_tables

logger = logging.getLogger(__name__)

# Вложение скачивается внутри обработчика, поэтому залп файлов в чате
# превращается в залп одновременных загрузок. Telegram отвечает на такое 429,
# а вложение, которое не удалось скачать, второй попытки не получает.
CONCURRENT_UPDATES_LIMIT = 4


async def run_collector():
    init_tables()
    bot, dp = create_collector_bot()

    allowed_chat_ids = get_collected_chat_ids()
    if allowed_chat_ids:
        logger.info("Собираем сообщения из чатов: %s", sorted(allowed_chat_ids))
    else:
        logger.info("COLLECTOR_CHAT_IDS не задан — собираем из всех групп бота")

    try:
        me = await bot.get_me()
        logger.info("Collector запущен как @%s (id=%s)", me.username, me.id)
        # Остановкой управляет app/main.py, свои обработчики сигналов aiogram не нужны
        await dp.start_polling(
            bot,
            handle_signals=False,
            tasks_concurrency_limit=CONCURRENT_UPDATES_LIMIT,
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_collector())
