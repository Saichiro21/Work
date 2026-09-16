import logging

from aiogram import Bot, Router
from aiogram.types import Message

from app.collector.saver import message_text, naive_utc, save_message
from app.collector.service import save_service_events
from app.db import crud
from app.db.db import SessionLocal
from app.db.models import Chat

logger = logging.getLogger(__name__)

router = Router()

GROUP_TYPES = {"group", "supergroup"}


def _is_collected(message: Message, allowed_chat_ids):
    if message.chat.type not in GROUP_TYPES:
        return False
    if allowed_chat_ids and message.chat.id not in allowed_chat_ids:
        return False
    return True


@router.message()
async def on_message(message: Message, bot: Bot, allowed_chat_ids: set):
    if not _is_collected(message, allowed_chat_ids):
        return
    # Служебные сообщения идут тем же потоком, но перепиской не являются
    if save_service_events(message):
        return
    await save_message(bot, message)


@router.edited_message()
async def on_edited_message(message: Message, bot: Bot, allowed_chat_ids: set):
    if not _is_collected(message, allowed_chat_ids):
        return

    new_text = message_text(message)
    edited_at = naive_utc(message.edit_date) or naive_utc(message.date)

    db = SessionLocal()
    try:
        chat = db.query(Chat).filter(Chat.telegram_chat_id == message.chat.id).first()
        stored = None
        if chat is not None:
            stored = crud.get_message_by_telegram_id(db, chat.id, message.message_id)
        if stored is not None:
            crud.update_message_text(db, stored.id, new_text, edited_at)
            return
    finally:
        db.close()

    # Сообщение отредактировали раньше, чем коллектор успел его увидеть
    logger.info("Правки сообщения %s нет в БД — сохраняем как новое", message.message_id)
    message_pk, _ = await save_message(bot, message)
    if message_pk is None:
        return

    db = SessionLocal()
    try:
        crud.update_message_text(db, message_pk, new_text, edited_at)
    finally:
        db.close()
