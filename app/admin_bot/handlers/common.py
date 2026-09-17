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
PERIOD_CALLBACK = "nav:period"

# Шаги открытого диалога и команды, которые его вызвали, — из чего складывается
# экран и что бот вправе убрать, если диалог закрывается, не оставив следа
STEPS_KEY = "steps"
COMMANDS_KEY = "commands"

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
    """Первый экран выбора: оба способа найти чат и выход к командам."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Показать список чатов", callback_data=CHAT_LIST_CALLBACK)
    builder.button(text="Показать ID чатов", callback_data=CHAT_IDS_CALLBACK)
    builder.button(text="Назад", callback_data=MENU_CALLBACK)
    builder.adjust(2, 1)
    return builder.as_markup()


def chat_list_view():
    """Второй экран: (текст со списком, клавиатура) или (None, None), если чатов нет."""
    chats = chats_overview()
    if not chats:
        return None, None

    lines = [CHAT_LIST_HEADER]
    builder = InlineKeyboardBuilder()
    # Выход идёт сверху, чтобы список чатов ниже читался одним блоком
    builder.button(text="Назад", callback_data=CHAT_QUESTION_CALLBACK)

    for telegram_chat_id, title, _, _ in chats[:MAX_CHAT_BUTTONS]:
        name = title or f"чат {telegram_chat_id}"
        builder.button(text=name, callback_data=f"{CHAT_CALLBACK_PREFIX}{telegram_chat_id}")

    # Здесь кнопки строго одна под другой: чат выбирают глазами по названию
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
    builder.button(text="Назад", callback_data=CHAT_QUESTION_CALLBACK)

    return "\n".join(lines), builder.as_markup()


def period_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="К выбору чата", callback_data=CHAT_QUESTION_CALLBACK)
    builder.button(text="В главное меню", callback_data=MENU_CALLBACK)
    builder.adjust(2)
    return builder.as_markup()


def empty_result_keyboard():
    """Экран «ничего не нашлось». Самый нужный выход — назвать другие даты."""
    builder = InlineKeyboardBuilder()
    builder.button(text="К вводу даты", callback_data=PERIOD_CALLBACK)
    builder.button(text="К выбору чата", callback_data=CHAT_QUESTION_CALLBACK)
    builder.button(text="В главное меню", callback_data=MENU_CALLBACK)
    builder.adjust(1, 2)
    return builder.as_markup()


def delivered_keyboard():
    """Кнопки под отправленной выгрузкой: куда идти дальше, не вызывая команду."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Главное меню", callback_data=MENU_CALLBACK)
    builder.button(text="Выбор чатов", callback_data=CHAT_QUESTION_CALLBACK)
    builder.button(text="Выбор даты", callback_data=PERIOD_CALLBACK)
    builder.adjust(1, 2)
    return builder.as_markup()


async def delete_message(bot, chat_id, message_id):
    if message_id is None:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        logger.info("Сообщение %s удалить не удалось", message_id)


async def _drop_buttons(bot, chat_id, message_id):
    """Снимает кнопки, оставляя текст.

    Так закрывается прошлый шаг: сообщение остаётся в переписке историей запроса,
    но нажать в нём уже нечего — вести дальше должен только нижний экран.
    """
    if message_id is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=None
        )
    except TelegramBadRequest:
        logger.info("Кнопки под сообщением %s снять не удалось", message_id)


def _steps(data):
    """Шаги диалога снизу вверх: пары «экран бота, ввод пользователя над ним».

    Ввод есть только у тех шагов, что открылись ответом на присланный текст.
    По этим парам диалог умеет откатываться: убрать экран вместе с вводом и
    оживить тот, что был выше.
    """
    return [list(step) for step in data.get(STEPS_KEY, [])]


def _menu_in_sight(data):
    """Меню всё ещё на виду: ниже него только команды и экраны диалога.

    Такое меню можно переиспользовать, а команды и экраны — убрать: в переписке
    не останется дыр. Стоит появиться выгрузке или пользовательскому вводу, как
    всё выше становится историей, и трогать её уже нельзя.
    """
    return (
        data.get("menu_message_id") is not None
        and data.get("file_message_id") is None
        and all(step[1] is None for step in _steps(data))
    )


async def _clear_steps(bot, chat_id, data):
    """Закрывает экраны прошлого диалога.

    Пока меню на виду, экраны удаляем целиком — диалог не оставляет следа. Иначе
    они уже часть истории: у них только снимаются кнопки, чтобы живое окно в
    переписке было одно.
    """
    removable = _menu_in_sight(data)
    for screen_message_id, _ in _steps(data):
        if removable:
            await delete_message(bot, chat_id, screen_message_id)
        else:
            await _drop_buttons(bot, chat_id, screen_message_id)
    return removable


async def open_dialog(bot, message, state, states):
    """Первый экран диалога. Команда и меню над ним остаются на месте.

    Команду запоминаем: если из первого окна уйти «Назад», она уберётся вместе с
    окном и на виду останется одно меню.
    """
    data = await state.get_data()
    chat_id = message.chat.id
    commands = data.get(COMMANDS_KEY, []) if _menu_in_sight(data) else []
    await _clear_steps(bot, chat_id, data)
    await _drop_buttons(bot, chat_id, data.get("file_message_id"))

    screen = await message.answer(CHAT_QUESTION, reply_markup=chat_question_keyboard())
    await state.set_state(states.waiting_chat_id)
    await state.update_data(
        steps=[[screen.message_id, None]],
        commands=list(commands) + [message.message_id],
        file_message_id=None,
    )


async def show_screen(
    bot, chat_id, state, text, keyboard=None, parse_mode=None, answers=None, back=False
):
    """Показывает шаг диалога.

    Переход по кнопке правит текущий экран на месте: диалог живёт в одном
    сообщении и переписка от него не растёт. Если это ответ на присланный текст
    (answers), экран должен встать под ним, а не поверх — тогда у прошлого шага
    снимаем кнопки и присылаем новый вниз, ничего не удаляя. Шаг назад (back)
    возвращает диалог туда, откуда он ушёл: экран и вызвавший его ввод убираются,
    а прошлый экран оживает.
    """
    data = await state.get_data()
    steps = _steps(data)
    file_message_id = data.get("file_message_id")

    if answers is not None:
        if steps:
            await _drop_buttons(bot, chat_id, steps[-1][0])
        await _drop_buttons(bot, chat_id, file_message_id)

        screen = await bot.send_message(
            chat_id, text, reply_markup=keyboard, parse_mode=parse_mode
        )
        await state.update_data(
            steps=steps + [[screen.message_id, answers]], file_message_id=None
        )
        return

    if back and len(steps) > 1 and steps[-1][1] is not None:
        screen_message_id, input_message_id = steps.pop()
        await delete_message(bot, chat_id, screen_message_id)
        await delete_message(bot, chat_id, input_message_id)

    if steps and file_message_id is None:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=steps[-1][0],
                reply_markup=keyboard,
                parse_mode=parse_mode,
            )
            await state.update_data(steps=steps)
            return
        except TelegramBadRequest:
            logger.info("Экран %s изменить не удалось, присылаем новый", steps[-1][0])
            steps.pop()

    await _drop_buttons(bot, chat_id, file_message_id)
    screen = await bot.send_message(
        chat_id, text, reply_markup=keyboard, parse_mode=parse_mode
    )
    await state.update_data(
        steps=steps + [[screen.message_id, None]], file_message_id=None
    )


async def show_chat_question(bot, chat_id, state, states, back=False):
    await state.set_state(states.waiting_chat_id)
    await show_screen(
        bot, chat_id, state, CHAT_QUESTION, chat_question_keyboard(), back=back
    )


async def show_chat_list(bot, chat_id, state, states, notice=None, answers=None):
    """Возвращает False, если список пуст и показывать нечего.

    answers — id присланного сообщения, если список открывается ответом на него.
    """
    text, keyboard = chat_list_view()
    if text is None:
        return False

    if notice:
        text = f"{notice}\n\n{text}"

    await state.set_state(states.waiting_chat_id)
    await show_screen(bot, chat_id, state, text, keyboard, answers=answers)
    return True


async def show_chat_ids(bot, chat_id, state, states):
    """Возвращает False, если чатов в базе нет."""
    text, keyboard = chat_ids_view()
    if text is None:
        return False

    await state.set_state(states.waiting_chat_id)
    await show_screen(bot, chat_id, state, text, keyboard, parse_mode="HTML")
    return True


async def show_period_question(bot, chat_id, state, notice=None, answers=None, back=False):
    """Шаг с периодом. В notice передают, чем закончилась прошлая попытка."""
    text = PERIOD_PROMPT if notice is None else f"{notice}\n\n{PERIOD_PROMPT}"
    await show_screen(
        bot, chat_id, state, text, period_keyboard(), answers=answers, back=back
    )


async def show_empty_result(bot, chat_id, state, text, answers):
    """Ничего не нашлось: файла не будет, зато видно, куда вернуться."""
    await show_screen(
        bot, chat_id, state, text, empty_result_keyboard(), answers=answers
    )


async def open_menu(bot, message, state):
    """Меню по команде /start: она уже внизу переписки, поэтому меню присылаем новым.

    Прежнее меню и следы диалога убираем, если они ещё были последними, — иначе
    список команд задвоится.
    """
    data = await state.get_data()
    chat_id = message.chat.id
    await state.clear()

    if await _clear_steps(bot, chat_id, data):
        await delete_message(bot, chat_id, data.get("menu_message_id"))
        for command_message_id in data.get(COMMANDS_KEY, []):
            await delete_message(bot, chat_id, command_message_id)
    await _drop_buttons(bot, chat_id, data.get("file_message_id"))

    menu = await message.answer(START_MESSAGE)
    await state.update_data(menu_message_id=menu.message_id, commands=[])


async def show_menu(bot, chat_id, state):
    """Возврат к списку команд.

    Если меню на виду, диалог убирает за собой всё: и окно, и вызвавшую его
    команду, — второе меню присылать незачем, иначе они копятся гармошкой. Если
    же ниже меню остался ввод или выгрузка, всё это история: её не трогаем, а
    меню открываем на месте закрытого окна.
    """
    data = await state.get_data()
    await state.clear()

    steps = _steps(data)
    await _drop_buttons(bot, chat_id, data.get("file_message_id"))

    if _menu_in_sight(data):
        for screen_message_id, _ in steps:
            await delete_message(bot, chat_id, screen_message_id)
        for command_message_id in data.get(COMMANDS_KEY, []):
            await delete_message(bot, chat_id, command_message_id)
        await state.update_data(
            menu_message_id=data.get("menu_message_id"), commands=[]
        )
        return

    for screen_message_id, _ in steps[:-1]:
        await _drop_buttons(bot, chat_id, screen_message_id)

    if steps:
        try:
            await bot.edit_message_text(
                START_MESSAGE,
                chat_id=chat_id,
                message_id=steps[-1][0],
                reply_markup=None,
            )
            await state.update_data(menu_message_id=steps[-1][0], commands=[])
            return
        except TelegramBadRequest:
            logger.info("Экран %s изменить не удалось, присылаем новый", steps[-1][0])

    menu = await bot.send_message(chat_id, START_MESSAGE)
    await state.update_data(menu_message_id=menu.message_id, commands=[])


async def finish_dialog(bot, chat_id, state, file_message_id):
    """Выгрузка отправлена.

    Шаги диалога и присланный период остаются в переписке — это история запроса.
    Кнопки последнего экрана снимаем: дальше ведут кнопки под файлом. Состояние
    не сбрасываем — выбранный чат остаётся, и следующий период можно назвать сразу.
    """
    data = await state.get_data()
    for screen_message_id, _ in _steps(data):
        await _drop_buttons(bot, chat_id, screen_message_id)

    await state.update_data(
        steps=[],
        commands=[],
        file_message_id=file_message_id,
        # Под меню теперь лежит выгрузка, на виду его больше нет
        menu_message_id=None,
    )


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
