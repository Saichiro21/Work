"""Запись сообщений в БД.

Общий код для двух обработчиков: новых сообщений и правок.
"""

import logging
from datetime import datetime, timezone

from app.collector.media import detect_attachment, download_attachment
from app.db import crud
from app.db.db import SessionLocal
from app.db.models import ForwardOriginType

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


def reply_to_id(message):
    parent = message.reply_to_message
    return parent.message_id if parent is not None else None


def user_label(user):
    if user.username:
        return f"{user.full_name} (@{user.username})"
    return user.full_name


def chat_label(chat, signature):
    name = chat.title or (f"@{chat.username}" if chat.username else str(chat.id))
    if signature:
        return f"{name} ({signature})"
    return name


def forward_source(message):
    """Возвращает (тип источника, имя источника, telegram-id, дату оригинала).

    Пересылку Telegram описывает полем forward_origin в одной из четырёх форм.
    Сводим их к плоским полям: отдельная таблица тут ничего не добавит.
    """
    origin = message.forward_origin
    if origin is None:
        return None, None, None, None

    # Дата оригинала, а не пересылки: между ними бывают месяцы
    sent_at = naive_utc(origin.date)

    if origin.type == ForwardOriginType.USER:
        user = origin.sender_user
        return ForwardOriginType.USER, user_label(user), user.id, sent_at

    if origin.type == ForwardOriginType.HIDDEN_USER:
        return ForwardOriginType.HIDDEN_USER, origin.sender_user_name, None, sent_at

    if origin.type == ForwardOriginType.CHANNEL:
        kind, chat = ForwardOriginType.CHANNEL, origin.chat
    else:
        kind, chat = ForwardOriginType.CHAT, origin.sender_chat

    return kind, chat_label(chat, origin.author_signature), chat.id, sent_at


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
    Служебные сообщения тут отбрасываются — их пишет collector/service.py.
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
            origin_type, origin_name, origin_id, origin_date = forward_source(message)
            stored = crud.create_message(
                db,
                message.message_id,
                chat.id,
                author.id if author is not None else None,
                text,
                naive_utc(message.date),
                edited_at=naive_utc(message.edit_date),
                reply_to_message_id=reply_to_id(message),
                forward_origin_type=origin_type,
                forward_from_name=origin_name,
                forward_from_id=origin_id,
                forward_origin_date=origin_date,
                is_automatic_forward=bool(message.is_automatic_forward),
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
