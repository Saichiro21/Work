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
    close_screen,
    format_datetime,
    open_chat_question,
    parse_period,
    sender_name,
    show_chat_ids,
    show_chat_list,
    show_chat_question,
    show_period_question,
)
from app.db.crud import get_chat_events_by_period, get_messages_by_period
from app.db.db import SessionLocal
from app.db.models import ChatEventType, ForwardOriginType

router = Router()

# Текст родительского сообщения в ответе даём отрывком: целиком он и так есть в выгрузке
REPLY_PREVIEW_LIMIT = 120

FORWARD_ORIGIN_LABELS = {
    ForwardOriginType.USER: "пользователь",
    ForwardOriginType.HIDDEN_USER: "скрытый пользователь",
    ForwardOriginType.CHAT: "чат",
    ForwardOriginType.CHANNEL: "канал",
}

EVENT_LABELS = {
    ChatEventType.TITLE_CHANGED: "чат переименовали",
    ChatEventType.PHOTO_CHANGED: "сменили фото чата",
    ChatEventType.PHOTO_DELETED: "удалили фото чата",
    ChatEventType.MESSAGE_PINNED: "закрепили сообщение",
    ChatEventType.CHAT_CREATED: "чат создан",
    ChatEventType.MIGRATED_TO: "группа стала супергруппой",
    ChatEventType.MIGRATED_FROM: "чат перенесён из группы",
    ChatEventType.AUTO_DELETE_CHANGED: "изменили таймер автоудаления",
}


class ExportStates(StatesGroup):
    waiting_chat_id = State()
    waiting_period = State()


def _reply_info(message, by_telegram_id):
    """Описание родительского сообщения. Его может не быть в выборке."""
    if message.reply_to_message_id is None:
        return None

    info = {"telegram_message_id": message.reply_to_message_id}
    parent = by_telegram_id.get(message.reply_to_message_id)
    if parent is None:
        return info

    preview = parent.text or ""
    if len(preview) > REPLY_PREVIEW_LIMIT:
        preview = preview[:REPLY_PREVIEW_LIMIT] + "…"

    info["отправитель"] = sender_name(parent.user)
    info["текст"] = preview or None
    info["дата отправки"] = format_datetime(parent.sent_at)
    return info


def _forward_info(message):
    """Откуда пришла пересылка. В «отправителе» стоит тот, кто переслал."""
    if message.forward_origin_type is None:
        return None

    kind = message.forward_origin_type
    return {
        "тип источника": FORWARD_ORIGIN_LABELS.get(kind, kind),
        "источник": message.forward_from_name,
        "id источника": message.forward_from_id,
        "дата оригинала": format_datetime(message.forward_origin_date),
        "автопересылка из канала": bool(message.is_automatic_forward),
    }


def _event_label(event):
    """Вход и выход читаются по-разному: человек сделал это сам или это сделали с ним."""
    same_person = event.actor_user_id == event.target_user_id

    if event.event_type == ChatEventType.MEMBER_JOINED:
        return "участник вошёл сам" if same_person else "участника добавили"

    if event.event_type == ChatEventType.MEMBER_LEFT:
        return "участник вышел сам" if same_person else "участника исключили"

    return EVENT_LABELS.get(event.event_type, event.event_type)


def _serialize_event(event):
    return {
        "тип": "служебное событие",
        "событие": _event_label(event),
        "дата": format_datetime(event.happened_at),
        "кто": sender_name(event.actor),
        "с кем": sender_name(event.target),
        "подробности": event.details,
        "закреплённое сообщение": event.target_message_id,
    }


def _serialize_message(message, by_telegram_id):
    return {
        "тип": "сообщение",
        "отправитель": sender_name(message.user),
        "текст": message.text,
        "дата отправки": format_datetime(message.sent_at),
        "была ли отредактирована": message.edited_at is not None,
        "ответ на": _reply_info(message, by_telegram_id),
        "переслано из": _forward_info(message),
        "история правок": [
            {
                "текст": version.text,
                "заменено": format_datetime(version.replaced_at),
            }
            for version in message.versions
        ],
        "список вложений": [
            {
                "file_type": attachment.file_type,
                "file_path": attachment.file_path,
                "original_filename": attachment.original_filename,
            }
            for attachment in message.attachments
        ],
    }


def _timeline(messages, events, by_telegram_id):
    """Сообщения и служебные события одной хронологией, как в самом Telegram."""
    records = [
        (item.sent_at, item.telegram_message_id, _serialize_message(item, by_telegram_id))
        for item in messages
    ]
    records += [
        (event.happened_at, event.telegram_message_id, _serialize_event(event))
        for event in events
    ]
    records.sort(key=lambda record: (record[0], record[1]))
    return [record[2] for record in records]


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
async def receive_period(message: Message, state: FSMContext, bot: Bot):
    try:
        date_from, date_to, label_from, label_to = parse_period(message.text)
    except PeriodError as error:
        await show_period_question(bot, message.chat.id, state, notice=str(error))
        return

    data = await state.get_data()
    chat_pk = data["chat_pk"]
    telegram_chat_id = data["telegram_chat_id"]

    db = SessionLocal()
    try:
        messages = get_messages_by_period(db, chat_pk, date_from, date_to)
        events = get_chat_events_by_period(db, chat_pk, date_from, date_to)
        by_telegram_id = {item.telegram_message_id: item for item in messages}
        payload = _timeline(messages, events, by_telegram_id)
    finally:
        db.close()

    if not payload:
        await show_period_question(
            bot, message.chat.id, state, notice="За этот период сообщений не найдено."
        )
        return

    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    filename = f"export_{telegram_chat_id}_{label_from}_{label_to}.json"
    document = BufferedInputFile(content, filename=filename)

    caption = f"Найдено сообщений: {len(messages)}"
    if events:
        caption += f", служебных событий: {len(events)}"

    await message.answer_document(document, caption=caption)
    # Экран с кнопкой «Назад» больше ни к чему не ведёт, убираем вместе с командой
    await close_screen(bot, message.chat.id, state)
