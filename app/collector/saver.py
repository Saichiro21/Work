"""Запись сообщений в БД.

Общий код для двух обработчиков: новых сообщений и правок.
"""

import logging
from datetime import datetime, timezone

from app.collector.media import detect_attachment, download_attachment
from app.db import crud
from app.db.db import SessionLocal

logger = logging.getLogger(__name__)


def naive_utc(value):
    """Приводит дату к naive UTC, как ожидают колонки DateTime в моделях.

    aiogram отдаёт message.date как datetime с таймзоной,
    а message.edit_date — как Unix-таймстамп.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def message_text(message):
    return message.text or message.caption or None


def save_author(db, message):
    """Анонимные админы и посты от имени канала сохраняются без автора."""
    if message.sender_chat is not None:
        return None
    user = message.from_user
    if user is None:
        return None
    return crud.get_or_create_user(
        db,
        user.id,
        user.username,
        user.first_name,
        user.last_name,
    )


async def save_message(bot, message):
    """Сохраняет сообщение и его вложение.

    Возвращает (id записи в БД, было ли сообщение создано сейчас).
    Для служебных сообщений («X вошёл в чат») возвращает (None, False).
    """
    text = message_text(message)
    if text is None and detect_attachment(message) is None:
        return None, False

    telegram_chat_id = message.chat.id

    db = SessionLocal()
    try:
        chat = crud.get_or_create_chat(db, telegram_chat_id, message.chat.title)
        stored = crud.get_message_by_telegram_id(db, chat.id, message.message_id)
        created = stored is None
        if created:
            author = save_author(db, message)
            stored = crud.create_message(
                db,
                message.message_id,
                chat.id,
                author.id if author is not None else None,
                text,
                naive_utc(message.date),
                naive_utc(message.edit_date),
            )
        message_pk = stored.id
    finally:
        db.close()

    if created:
        attachment = await download_attachment(bot, message, telegram_chat_id)
        if attachment is not None:
            file_type, file_path, original_filename = attachment
            db = SessionLocal()
            try:
                crud.create_attachment(
                    db, message_pk, file_type, file_path, original_filename
                )
            finally:
                db.close()

    return message_pk, created
