from aiogram import Bot, Dispatcher

from app.collector.config import get_collected_chat_ids, get_token
from app.collector.handlers import router


def create_collector_bot():
    bot = Bot(token=get_token())
    # Белый список читаем один раз при старте, дальше он приходит в обработчики
    dp = Dispatcher(allowed_chat_ids=get_collected_chat_ids())
    dp.include_router(router)
    return bot, dp
