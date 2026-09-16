"""Общие шаги диалогов админского бота: выбор чата, разбор периода, показ времени."""

import logging
import os
import re
from datetime import datetime, time, timezone
from functools import lru_cache
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.crud import get_chats_overview
from app.db.db import SessionLocal
from app.db.models import Chat

logger = logging.getLogger(__name__)

DATE_PART = r"(\d{2}\.\d{2}\.\d{4})"
PERIOD_PATTERN = re.compile(rf"^{DATE_PART}\s*-\s*{DATE_PART}$")
DATE_INPUT_FORMAT = "%d.%m.%Y"

DEFAULT_TIMEZONE = "Europe/Moscow"
DATETIME_FORMAT = "%d.%m.%Y %H:%M:%S"
FILENAME_DATE_FORMAT = "%d-%m-%Y"

CHAT_CALLBACK_PREFIX = "chat:"
MENU_CALLBACK = "nav:menu"
CHAT_QUESTION_CALLBACK = "nav:chat"
CHAT_LIST_CALLBACK = "nav:list"
CHAT_IDS_CALLBACK = "nav:ids"

# Кнопок в одном сообщении Telegram держит немного, остальные чаты выбираются вводом ID
MAX_CHAT_BUTTONS = 20

START_MESSAGE = (
    "Привет! Это бот для выгрузки корпоративных переписок.\n"
    "/export — JSON с сообщениями выбранного чата за период.\n"
    "/files — архив с вложениями этих сообщений."
)

CHAT_QUESTION = "Из какого чата взять данные?"

CHAT_LIST_HEADER = "Выберите чат из списка и укажите его telegram_chat_id."

CHAT_IDS_HEADER = (
    "telegram_chat_id собранных чатов. Нажмите на номер, чтобы скопировать его — "
    "он нужен для COLLECTOR_CHAT_IDS в .env."
)

NO_CHATS_MESSAGE = (
    "В базе пока нет ни одного чата. Добавьте бота-коллектора в чат "
    "и напишите там сообщение — чат появится в списке."
)

PERIOD_PROMPT = "За какой период? Введите даты в формате ДД.ММ.ГГГГ - ДД.ММ.ГГГГ"


class PeriodError(ValueError):
    """Текст ошибки уже готов к отправке пользователю."""


@lru_cache(maxsize=1)
def display_timezone():
    """Пояс, в котором показываем время. В базе оно всегда хранится в UTC."""
    name = os.getenv("DISPLAY_TIMEZONE") or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Неизвестный DISPLAY_TIMEZONE=%r, показываем время в UTC", name)
        return timezone.utc


def to_display(value):
    """Переводит naive UTC из базы в пояс показа."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone(display_timezone())


def format_datetime(value):
    moment = to_display(value)
    if moment is None:
        return None
    return moment.strftime(DATETIME_FORMAT)


def format_day(value):
    """Дата для папок внутри архива — в ISO, чтобы папки сортировались по хронологии."""
    moment = to_display(value)
    if moment is None:
        return None
    return moment.strftime("%Y-%m-%d")


def format_date(value):
    moment = to_display(value)
    if moment is None:
        return None
    return moment.strftime("%d.%m.%Y")


def chats_overview():
    db = SessionLocal()
    try:
        return get_chats_overview(db)
    finally:
        db.close()


def chat_question_keyboard():
    """Первый экран выбора: показать список или вернуться к командам."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Показать список чатов", callback_data=CHAT_LIST_CALLBACK)
    builder.button(text="Назад", callback_data=MENU_CALLBACK)
    builder.adjust(1)
    return builder.as_markup()


def chat_list_view():
    """Второй экран: (текст со списком, клавиатура) или (None, None), если чатов нет."""
    chats = chats_overview()
    if not chats:
        return None, None

    lines = [CHAT_LIST_HEADER]
    builder = InlineKeyboardBuilder()
    # «Назад» первой кнопкой — она должна быть над списком чатов
    builder.button(text="Назад", callback_data=CHAT_QUESTION_CALLBACK)

    for telegram_chat_id, title, _, _ in chats[:MAX_CHAT_BUTTONS]:
        name = title or f"чат {telegram_chat_id}"
        builder.button(text=name, callback_data=f"{CHAT_CALLBACK_PREFIX}{telegram_chat_id}")

    builder.button(text="Показать ID чатов", callback_data=CHAT_IDS_CALLBACK)
    builder.adjust(1)

    hidden = len(chats) - MAX_CHAT_BUTTONS
    if hidden > 0:
        lines.append(f"\nЕщё чатов: {hidden}. Для них отправьте telegram_chat_id.")

    return "\n".join(lines), builder.as_markup()


def chat_ids_view():
    """Экран со списком telegram_chat_id. Номера в теге code — их удобно копировать."""
    chats = chats_overview()
    if not chats:
        return None, None

    lines = [CHAT_IDS_HEADER, ""]
    for telegram_chat_id, title, messages, last_sent_at in chats:
        name = escape(title or f"чат {telegram_chat_id}")
        activity = f"{messages} сообщений" if messages else "пока пусто"
        last = format_date(last_sent_at)
        if last:
            activity += f", последнее {last}"
        lines.append(f"• {name} — <code>{telegram_chat_id}</code> ({activity})")

    builder = InlineKeyboardBuilder()
    builder.button(text="Назад", callback_data=CHAT_LIST_CALLBACK)

    return "\n".join(lines), builder.as_markup()


def period_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Назад", callback_data=CHAT_QUESTION_CALLBACK)
    return builder.as_markup()


async def open_chat_question(message, state, states):
    """Первый экран диалога.

    Запоминаем id самого экрана и команды, которой он вызван: все шаги правят
    одно сообщение, а «Назад» в конце убирает и экран, и команду.
    """
    screen = await message.answer(CHAT_QUESTION, reply_markup=chat_question_keyboard())
    await state.set_state(states.waiting_chat_id)
    await state.update_data(
        screen_message_id=screen.message_id,
        command_message_id=message.message_id,
    )


async def edit_screen(bot, chat_id, state, text, keyboard=None, parse_mode=None):
    """Переписывает экран на месте. Если сообщения уже нет — отправляет новое."""
    data = await state.get_data()
    message_id = data.get("screen_message_id")

    if message_id is not None:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboard,
                parse_mode=parse_mode,
            )
            return
        except TelegramBadRequest:
            logger.info("Экран %s не удалось изменить, отправляем новый", message_id)

    screen = await bot.send_message(
        chat_id, text, reply_markup=keyboard, parse_mode=parse_mode
    )
    await state.update_data(screen_message_id=screen.message_id)


async def show_chat_question(bot, chat_id, state, states):
    await state.set_state(states.waiting_chat_id)
    await edit_screen(bot, chat_id, state, CHAT_QUESTION, chat_question_keyboard())


async def show_chat_list(bot, chat_id, state, states):
    """Возвращает False, если список пуст и показывать нечего."""
    text, keyboard = chat_list_view()
    if text is None:
        return False

    await state.set_state(states.waiting_chat_id)
    await edit_screen(bot, chat_id, state, text, keyboard)
    return True


async def show_chat_ids(bot, chat_id, state, states):
    """Возвращает False, если чатов в базе нет."""
    text, keyboard = chat_ids_view()
    if text is None:
        return False

    await state.set_state(states.waiting_chat_id)
    await edit_screen(bot, chat_id, state, text, keyboard, parse_mode="HTML")
    return True


async def show_period_question(bot, chat_id, state):
    await edit_screen(bot, chat_id, state, PERIOD_PROMPT, period_keyboard())


async def close_screen(bot, chat_id, state):
    """Убирает экран вместе с вызвавшей его командой и сбрасывает диалог.

    В переписке остаётся предыдущее сообщение — то самое меню с командами.
    """
    data = await state.get_data()
    await state.clear()

    removed = True
    for key in ("screen_message_id", "command_message_id"):
        message_id = data.get(key)
        if message_id is None:
            continue
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramBadRequest:
            logger.info("Сообщение %s удалить не удалось", message_id)
            if key == "screen_message_id":
                removed = False

    # Экран остался на месте — тогда хотя бы показываем меню, чтобы кнопки не висели
    if not removed:
        await bot.send_message(chat_id, START_MESSAGE)


async def accept_chat(state, telegram_chat_id, next_state):
    """Запоминает выбранный чат и переводит диалог к вводу периода.

    Возвращает False, если чата с таким ID в базе нет.
    """
    chat = find_chat(telegram_chat_id)
    if chat is None:
        return False

    chat_pk, chat_title = chat
    await state.update_data(
        chat_pk=chat_pk,
        telegram_chat_id=telegram_chat_id,
        chat_title=chat_title,
    )
    await state.set_state(next_state)
    return True


def _parse_date(text):
    try:
        return datetime.strptime(text, DATE_INPUT_FORMAT).date()
    except ValueError:
        raise PeriodError("Некорректная дата. Проверьте числа в диапазоне.")


def parse_period(text):
    """Возвращает (граница «с» в UTC, граница «по» в UTC, подпись «с», подпись «по»).

    Даты пользователь называет по своему времени, а в базе всё в UTC, поэтому
    границы суток переводим в UTC — иначе край периода съедет на размер смещения.
    """
    match = PERIOD_PATTERN.match((text or "").strip())
    if not match:
        raise PeriodError("Неверный формат. Введите даты так: ДД.ММ.ГГГГ - ДД.ММ.ГГГГ")

    day_from = _parse_date(match.group(1))
    day_to = _parse_date(match.group(2))

    if day_from > day_to:
        raise PeriodError("Дата начала не может быть позже даты окончания.")

    tz = display_timezone()

    # Начало периода не позже конца, поэтому будущее начало означает,
    # что в будущем весь период целиком
    today = datetime.now(tz).date()
    if day_from > today:
        if day_from == day_to:
            raise PeriodError(
                f"Дата {day_from.strftime('%d.%m.%Y')} ещё не наступила, "
                "сообщений за неё быть не может."
            )
        raise PeriodError(
            f"Период с {day_from.strftime('%d.%m.%Y')} по "
            f"{day_to.strftime('%d.%m.%Y')} целиком в будущем, "
            "сообщений за него быть не может."
        )

    # Конец периода дотягиваем до конца суток, иначе сообщения последнего дня не попадут
    local_from = datetime.combine(day_from, time.min, tzinfo=tz)
    local_to = datetime.combine(day_to, time.max, tzinfo=tz)

    return (
        local_from.astimezone(timezone.utc).replace(tzinfo=None),
        local_to.astimezone(timezone.utc).replace(tzinfo=None),
        day_from.strftime(FILENAME_DATE_FORMAT),
        day_to.strftime(FILENAME_DATE_FORMAT),
    )


def find_chat(telegram_chat_id):
    """Возвращает (id чата в БД, название) или None, если чата в базе нет."""
    db = SessionLocal()
    try:
        chat = db.query(Chat).filter(Chat.telegram_chat_id == telegram_chat_id).first()
        if chat is None:
            return None
        return chat.id, chat.title
    finally:
        db.close()


def sender_name(user):
    if user is None:
        return None
    parts = [user.first_name, user.last_name]
    name = " ".join(part for part in parts if part)
    if user.username:
        if name:
            return f"{name} (@{user.username})"
        return f"@{user.username}"
    return name or str(user.telegram_user_id)
