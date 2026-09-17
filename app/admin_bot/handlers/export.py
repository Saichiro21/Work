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
    PERIOD_CALLBACK,
    PeriodError,
    accept_chat,
    chats_overview,
    delivered_keyboard,
    finish_dialog,
    open_dialog,
    parse_period,
    show_chat_ids,
    show_chat_list,
    show_chat_question,
    show_empty_result,
    show_period_question,
)
from app.admin_bot.serialize import timeline
from app.db.crud import get_chat_events_by_period, get_messages_by_period
from app.db.db import SessionLocal

router = Router()


class ExportStates(StatesGroup):
    waiting_chat_id = State()
    waiting_period = State()


@router.message(Command("export", ignore_case=True))
async def cmd_export(message: Message, state: FSMContext, bot: Bot):
    if not chats_overview():
        # Состояние сбрасываем, но данные оставляем: в них номер меню
        await state.set_state(None)
        await message.answer(NO_CHATS_MESSAGE)
        return

    await open_dialog(bot, message, state, ExportStates)


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
    StateFilter(ExportStates.waiting_period),
    F.data == CHAT_QUESTION_CALLBACK,
)
async def back_to_chat(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Возврат к выбору чата с шага периода и из-под отправленного файла."""
    await show_chat_question(bot, callback.from_user.id, state, ExportStates, back=True)
    await callback.answer()


@router.callback_query(
    StateFilter(ExportStates.waiting_period),
    F.data == PERIOD_CALLBACK,
)
async def back_to_period(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await show_period_question(bot, callback.from_user.id, state, back=True)
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
    StateFilter(ExportStates.waiting_chat_id),
    F.data == CHAT_QUESTION_CALLBACK,
)
async def back_to_question(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await show_chat_question(bot, callback.from_user.id, state, ExportStates, back=True)
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
        await show_chat_list(
            bot,
            message.chat.id,
            state,
            ExportStates,
            notice="Выберите чат кнопкой или отправьте его telegram_chat_id.",
            answers=message.message_id,
        )
        return

    if not await accept_chat(state, telegram_chat_id, ExportStates.waiting_period):
        await show_chat_list(
            bot,
            message.chat.id,
            state,
            ExportStates,
            notice="Чат с таким telegram_chat_id не найден в базе.",
            answers=message.message_id,
        )
        return

    await show_period_question(bot, message.chat.id, state, answers=message.message_id)


@router.message(StateFilter(ExportStates.waiting_period))
async def receive_period(message: Message, state: FSMContext, bot: Bot):
    try:
        date_from, date_to, label_from, label_to = parse_period(message.text)
    except PeriodError as error:
        await show_period_question(
            bot, message.chat.id, state, notice=str(error), answers=message.message_id
        )
        return

    data = await state.get_data()
    chat_pk = data["chat_pk"]
    telegram_chat_id = data["telegram_chat_id"]

    db = SessionLocal()
    try:
        messages = get_messages_by_period(db, chat_pk, date_from, date_to)
        events = get_chat_events_by_period(db, chat_pk, date_from, date_to)
        payload = timeline(messages, events)
    finally:
        db.close()

    if not payload:
        await show_empty_result(
            bot,
            message.chat.id,
            state,
            "За этот период сообщений не найдено.",
            answers=message.message_id,
        )
        return

    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"export_{telegram_chat_id}_{label_from}_{label_to}.json"
    document = BufferedInputFile(content, filename=filename)

    caption = f"Найдено сообщений: {len(messages)}"
    if events:
        caption += f", служебных событий: {len(events)}"

    sent = await message.answer_document(
        document, caption=caption, reply_markup=delivered_keyboard()
    )
    await finish_dialog(bot, message.chat.id, state, sent.message_id)
