import json

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.admin_bot.handlers.common import (
    CHAT_CALLBACK_PREFIX,
    CHAT_IDS_CALLBACK,
    CHAT_LIST_CALLBACK,
    CHAT_QUESTION_CALLBACK,
    NO_CHATS_MESSAGE,
    PeriodError,
    accept_chat,
    chats_overview,
    format_datetime,
    open_chat_question,
    parse_period,
    sender_name,
    show_chat_ids,
    show_chat_list,
    show_chat_question,
    show_period_question,
)
from app.db.crud import get_messages_by_period
from app.db.db import SessionLocal

router = Router()


class ExportStates(StatesGroup):
    waiting_chat_id = State()
    waiting_period = State()


def _serialize_message(message):
    return {
        "отправитель": sender_name(message.user),
        "текст": message.text,
        "дата отправки": format_datetime(message.sent_at),
        "была ли отредактирована": message.edited_at is not None,
        "список вложений": [
            {
                "file_type": attachment.file_type,
                "file_path": attachment.file_path,
                "original_filename": attachment.original_filename,
            }
            for attachment in message.attachments
        ],
    }


@router.message(Command("export", ignore_case=True))
async def cmd_export(message: Message, state: FSMContext):
    if not chats_overview():
        await state.clear()
        await message.answer(NO_CHATS_MESSAGE)
        return

    await open_chat_question(message, state, ExportStates)


@router.callback_query(
    StateFilter(ExportStates.waiting_chat_id),
    F.data == CHAT_LIST_CALLBACK,
)
async def show_list(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not await show_chat_list(bot, callback.from_user.id, state, ExportStates):
        await callback.answer(NO_CHATS_MESSAGE, show_alert=True)
        return
    await callback.answer()


@router.callback_query(
    StateFilter(ExportStates.waiting_chat_id),
    F.data == CHAT_IDS_CALLBACK,
)
async def show_ids(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not await show_chat_ids(bot, callback.from_user.id, state, ExportStates):
        await callback.answer(NO_CHATS_MESSAGE, show_alert=True)
        return
    await callback.answer()


@router.callback_query(
    StateFilter(ExportStates.waiting_chat_id, ExportStates.waiting_period),
    F.data == CHAT_QUESTION_CALLBACK,
)
async def back_to_question(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await show_chat_question(bot, callback.from_user.id, state, ExportStates)
    await callback.answer()


@router.callback_query(
    StateFilter(ExportStates.waiting_chat_id),
    F.data.startswith(CHAT_CALLBACK_PREFIX),
)
async def choose_chat(callback: CallbackQuery, state: FSMContext, bot: Bot):
    telegram_chat_id = int(callback.data.removeprefix(CHAT_CALLBACK_PREFIX))
    if not await accept_chat(state, telegram_chat_id, ExportStates.waiting_period):
        await callback.answer("Этого чата уже нет в базе", show_alert=True)
        return

    await callback.answer()
    await show_period_question(bot, callback.from_user.id, state)


@router.message(StateFilter(ExportStates.waiting_chat_id))
async def receive_chat_id(message: Message, state: FSMContext, bot: Bot):
    try:
        telegram_chat_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("Выберите чат кнопкой или отправьте его telegram_chat_id.")
        return

    if not await accept_chat(state, telegram_chat_id, ExportStates.waiting_period):
        await message.answer("Чат с таким telegram_chat_id не найден в базе.")
        return

    await show_period_question(bot, message.chat.id, state)


@router.message(StateFilter(ExportStates.waiting_period))
async def receive_period(message: Message, state: FSMContext):
    try:
        date_from, date_to, label_from, label_to = parse_period(message.text)
    except PeriodError as error:
        await message.answer(str(error))
        return

    data = await state.get_data()
    chat_pk = data["chat_pk"]
    telegram_chat_id = data["telegram_chat_id"]

    db = SessionLocal()
    try:
        messages = get_messages_by_period(db, chat_pk, date_from, date_to)
        payload = [_serialize_message(item) for item in messages]
    finally:
        db.close()

    if not payload:
        await message.answer("За этот период сообщений не найдено.")
        await state.clear()
        return

    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"export_{telegram_chat_id}_{label_from}_{label_to}.json"
    document = BufferedInputFile(content, filename=filename)

    await message.answer_document(
        document,
        caption=f"Найдено сообщений: {len(payload)}",
    )
    await state.clear()
