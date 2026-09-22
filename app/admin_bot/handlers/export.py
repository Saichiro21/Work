import json

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.admin_bot.handlers.common import (
    CHAT_CALLBACK_PREFIX,
    CHAT_IDS_CALLBACK,
    CHAT_LIST_CALLBACK,
    CHAT_QUESTION_CALLBACK,
    MENU_CALLBACK,
    NOT_A_COMMAND,
    NO_CHATS_MESSAGE,
    PERIOD_CALLBACK,
    PeriodError,
    accept_chat,
    chats_overview,
    delete_message,
    delivered_keyboard,
    finish_dialog,
    open_dialog,
    parse_period,
    show_chat_ids,
    show_chat_list,
    reject_bad_chat_id,
    reject_period,
    reject_unknown_chat,
    show_chat_question,
    show_empty_result,
    show_period_question,
    show_screen,
)
from app.admin_bot.html_report import render_report
from app.admin_bot.serialize import timeline
from app.db.crud import get_chat_events_by_period, get_messages_by_period
from app.db.db import SessionLocal

router = Router()

FORMAT_PROMPT = "В каком виде выгрузить?"

HTML_CALLBACK = "export:format:html"
JSON_CALLBACK = "export:format:json"


class ExportStates(StatesGroup):
    # Подпись окна выбора чата: по ней видно, какая команда его открыла
    label = "/export — переписка файлом"

    waiting_chat_id = State()
    waiting_period = State()
    waiting_format = State()


def format_keyboard():
    """Два назначения выгрузки, под ними выходы: они не про формат."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Читать глазами (HTML)", callback_data=HTML_CALLBACK)
    builder.button(text="Данные (JSON)", callback_data=JSON_CALLBACK)
    builder.button(text="Назад", callback_data=PERIOD_CALLBACK)
    builder.button(text="Главное меню", callback_data=MENU_CALLBACK)
    builder.adjust(2, 2)
    return builder.as_markup()


def _found_text(messages, events):
    line = f"сообщений: {len(messages)}"
    if events:
        line += f", служебных событий: {len(events)}"
    return line


def _format_text(messages, events, period_label):
    """Сколько нашлось — видно до выбора формата: файл может и не понадобиться."""
    found = f"За {period_label} найдено {_found_text(messages, events)}."
    return "\n".join([FORMAT_PROMPT, "", found])


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


@router.message(StateFilter(ExportStates.waiting_chat_id), NOT_A_COMMAND)
async def receive_chat_id(message: Message, state: FSMContext, bot: Bot):
    try:
        telegram_chat_id = int((message.text or "").strip())
    except ValueError:
        await reject_bad_chat_id(
            bot, message.chat.id, state, ExportStates, message.message_id
        )
        return

    if not await accept_chat(state, telegram_chat_id, ExportStates.waiting_period):
        await reject_unknown_chat(
            bot, message.chat.id, state, ExportStates, message.message_id
        )
        return

    await show_period_question(bot, message.chat.id, state, answers=message.message_id)


@router.message(StateFilter(ExportStates.waiting_period), NOT_A_COMMAND)
async def receive_period(message: Message, state: FSMContext, bot: Bot):
    try:
        date_from, date_to, file_label, text_label = parse_period(message.text)
    except PeriodError as error:
        await reject_period(bot, message.chat.id, state, str(error), message.message_id)
        return

    data = await state.get_data()

    db = SessionLocal()
    try:
        messages = get_messages_by_period(db, data["chat_pk"], date_from, date_to)
        events = get_chat_events_by_period(db, data["chat_pk"], date_from, date_to)
    finally:
        db.close()

    if not messages and not events:
        await show_empty_result(
            bot,
            message.chat.id,
            state,
            "За этот период сообщений не найдено.",
            answers=message.message_id,
        )
        return

    await state.update_data(
        date_from=date_from,
        date_to=date_to,
        file_label=file_label,
        text_label=text_label,
    )
    await state.set_state(ExportStates.waiting_format)
    await show_screen(
        bot,
        message.chat.id,
        state,
        _format_text(messages, events, text_label),
        format_keyboard(),
        answers=message.message_id,
    )


# Бот не отправит файл тяжелее 50 МБ: до предела оставляем запас на служебное
SEND_LIMIT = 45 * 1024 * 1024
TOO_HEAVY = (
    "Выгрузка вышла тяжелее 45 МБ — столько Telegram не пропустит.\n"
    "Возьмите период покороче."
)


async def _deliver(bot, chat_id, state, as_html):
    """Собирает выбранный формат и отправляет файл.

    Страницу и JSON собираем при открытой сессии: и та, и другой дочитывают
    вложения, правки и авторов по связям, а после close() это уже не выйдет.
    """
    data = await state.get_data()
    telegram_chat_id = data["telegram_chat_id"]

    db = SessionLocal()
    try:
        messages = get_messages_by_period(
            db, data["chat_pk"], data["date_from"], data["date_to"]
        )
        events = get_chat_events_by_period(
            db, data["chat_pk"], data["date_from"], data["date_to"]
        )
        if as_html:
            content = render_report(
                data.get("chat_title"),
                telegram_chat_id,
                data["text_label"],
                messages,
                events,
            ).encode("utf-8")
        else:
            payload = timeline(messages, events)
            content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    finally:
        db.close()

    extension = "html" if as_html else "json"
    filename = f"export_{telegram_chat_id}_{data['file_label']}.{extension}"

    caption = f"Найдено {_found_text(messages, events)}"
    if as_html:
        caption += "\nОткройте файл в браузере."

    # Кнопки под выгрузкой ведут к периоду: туда и возвращаем диалог
    await state.set_state(ExportStates.waiting_period)

    if len(content) > SEND_LIMIT:
        sent = await bot.send_message(chat_id, TOO_HEAVY, reply_markup=delivered_keyboard())
        await finish_dialog(bot, chat_id, state, sent.message_id)
        return

    sent = await bot.send_document(
        chat_id,
        BufferedInputFile(content, filename=filename),
        caption=caption,
        reply_markup=delivered_keyboard(),
    )
    await finish_dialog(bot, chat_id, state, sent.message_id)


@router.callback_query(
    StateFilter(ExportStates.waiting_format),
    F.data == HTML_CALLBACK,
)
async def deliver_html(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await _deliver(bot, callback.from_user.id, state, as_html=True)


@router.callback_query(
    StateFilter(ExportStates.waiting_format),
    F.data == JSON_CALLBACK,
)
async def deliver_json(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await _deliver(bot, callback.from_user.id, state, as_html=False)


@router.callback_query(
    StateFilter(ExportStates.waiting_format),
    F.data == PERIOD_CALLBACK,
)
async def back_from_format(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.set_state(ExportStates.waiting_period)
    await show_period_question(bot, callback.from_user.id, state, back=True)
    await callback.answer()


@router.message(StateFilter(ExportStates.waiting_format), NOT_A_COMMAND)
async def ignore_typing(message: Message, bot: Bot):
    """На этом экране отвечают кнопкой, поэтому присланный текст убираем.

    Без такого обработчика он доходил бы до заглушки, а та открывает меню и
    сбрасывает диалог: чат и период пришлось бы называть заново.
    """
    await delete_message(bot, message.chat.id, message.message_id)
