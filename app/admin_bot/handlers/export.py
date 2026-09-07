import json
import re
from datetime import datetime, time

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message

from app.db.crud import get_messages_by_period
from app.db.db import SessionLocal
from app.db.models import Chat

router = Router()

PERIOD_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})$"
)


class ExportStates(StatesGroup):
    waiting_chat_id = State()
    waiting_period = State()


def _sender_name(user):
    if user is None:
        return None
    parts = [user.first_name, user.last_name]
    name = " ".join(part for part in parts if part)
    if user.username:
        if name:
            return f"{name} (@{user.username})"
        return f"@{user.username}"
    return name or str(user.telegram_user_id)


def _serialize_message(message):
    return {
        "отправитель": _sender_name(message.user),
        "текст": message.text,
        "дата отправки": message.sent_at.isoformat() if message.sent_at else None,
        "была ли отредактирована": message.edited_at is not None,
        "список реакций": [reaction.emoji for reaction in message.reactions],
        "список вложений": [
            {
                "file_type": attachment.file_type,
                "file_path": attachment.file_path,
                "original_filename": attachment.original_filename,
            }
            for attachment in message.attachments
        ],
    }


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Это бот для выгрузки корпоративных переписок.\n"
        "Команда /export сформирует JSON-файл с сообщениями выбранного чата за период."
    )


@router.message(Command("export"))
async def cmd_export(message: Message, state: FSMContext):
    await state.set_state(ExportStates.waiting_chat_id)
    await message.answer("Из какого чата выгрузить данные? Отправьте telegram_chat_id")


@router.message(StateFilter(ExportStates.waiting_chat_id))
async def receive_chat_id(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    try:
        telegram_chat_id = int(text)
    except ValueError:
        await message.answer("Нужно отправить число — telegram_chat_id чата.")
        return

    db = SessionLocal()
    try:
        chat = (
            db.query(Chat)
            .filter(Chat.telegram_chat_id == telegram_chat_id)
            .first()
        )
        if chat is None:
            await message.answer("Чат с таким telegram_chat_id не найден в базе.")
            return
        chat_pk = chat.id
        chat_title = chat.title
    finally:
        db.close()

    await state.update_data(
        chat_pk=chat_pk,
        telegram_chat_id=telegram_chat_id,
        chat_title=chat_title,
    )
    await state.set_state(ExportStates.waiting_period)
    await message.answer(
        "За какой период? Введите даты в формате ГГГГ-ММ-ДД - ГГГГ-ММ-ДД\n"
        "например: 2026-08-01 - 2026-08-27"
    )


@router.message(StateFilter(ExportStates.waiting_period))
async def receive_period(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    match = PERIOD_PATTERN.match(text)
    if not match:
        await message.answer(
            "Неверный формат. Введите даты так: ГГГГ-ММ-ДД - ГГГГ-ММ-ДД"
        )
        return

    try:
        date_from = datetime.strptime(match.group(1), "%Y-%m-%d")
        date_to_date = datetime.strptime(match.group(2), "%Y-%m-%d")
    except ValueError:
        await message.answer("Некорректная дата. Проверьте числа в диапазоне.")
        return

    if date_from.date() > date_to_date.date():
        await message.answer("Дата начала не может быть позже даты окончания.")
        return

    date_to = datetime.combine(date_to_date.date(), time.max)
    data = await state.get_data()
    chat_pk = data["chat_pk"]
    telegram_chat_id = data["telegram_chat_id"]

    db = SessionLocal()
    try:
        messages = get_messages_by_period(db, chat_pk, date_from, date_to)
        payload = [_serialize_message(item) for item in messages]
    finally:
        db.close()

    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    filename = (
        f"export_{telegram_chat_id}_{match.group(1)}_{match.group(2)}.json"
    )
    document = BufferedInputFile(content, filename=filename)

    await message.answer_document(
        document,
        caption=f"Найдено сообщений: {len(payload)}",
    )
    await state.clear()
