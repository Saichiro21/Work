import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from dotenv import load_dotenv

from app.admin_bot.handlers.common import (
    BOT_DESCRIPTION,
    BOT_SHORT_DESCRIPTION,
    MENU_COMMANDS,
)
from app.admin_bot.handlers.export import router as export_router
from app.admin_bot.handlers.fallback import router as fallback_router
from app.admin_bot.handlers.files import router as files_router
from app.admin_bot.handlers.menu import router as menu_router
from app.admin_bot.middlewares import AdminMiddleware
from app.db.crud import add_admin
from app.db.db import SessionLocal, init_tables

logger = logging.getLogger(__name__)


def _sync_admins_from_env():
    raw_ids = os.getenv("ADMIN_IDS", "")
    db = SessionLocal()
    try:
        for item in raw_ids.split(","):
            item = item.strip()
            if not item:
                continue
            add_admin(db, int(item))
    finally:
        db.close()


def create_admin_bot():
    load_dotenv()
    token = os.getenv("ADMIN_BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задан ADMIN_BOT_TOKEN")

    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(AdminMiddleware())
    dp.callback_query.middleware(AdminMiddleware())
    dp.include_router(menu_router)
    dp.include_router(export_router)
    dp.include_router(files_router)
    # Перехватывает всё подряд, поэтому строго последним
    dp.include_router(fallback_router)
    return bot, dp


async def publish_profile(bot):
    """Витрина бота в Telegram: описание, подпись в профиле и меню команд.

    Описание — это экран, который человек видит до первого сообщения, с кнопкой
    «Старт»; потом его же показывает кнопка «Что умеет этот бот?». Ставим из
    кода, чтобы правка текстов не требовала похода в BotFather и не разъезжалась
    с приветствием. Не вышло — работаем дальше: на сбор и выгрузку это не влияет.
    """
    try:
        await bot.set_my_short_description(short_description=BOT_SHORT_DESCRIPTION)
        await bot.set_my_description(description=BOT_DESCRIPTION)
        await bot.set_my_commands(
            [
                BotCommand(command=name, description=about.rstrip("."))
                for name, about in MENU_COMMANDS
            ]
        )
    except TelegramAPIError as error:
        logger.warning("Описание и меню команд обновить не удалось: %s", error)


async def run_admin_bot():
    load_dotenv()
    init_tables()
    _sync_admins_from_env()
    bot, dp = create_admin_bot()
    try:
        await publish_profile(bot)
        # Остановкой управляет app/main.py, свои обработчики сигналов aiogram не нужны
        await dp.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_admin_bot())
