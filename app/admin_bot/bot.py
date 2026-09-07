import asyncio
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from app.admin_bot.handlers.export import router as export_router
from app.admin_bot.middlewares import AdminMiddleware
from app.db.crud import add_admin
from app.db.db import SessionLocal, init_tables


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
    dp.include_router(export_router)
    return bot, dp


async def run_admin_bot():
    load_dotenv()
    init_tables()
    _sync_admins_from_env()
    bot, dp = create_admin_bot()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_admin_bot())
