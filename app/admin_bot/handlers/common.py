"""Общие шаги диалогов админского бота: выбор чата, разбор периода, показ времени."""

import logging
import os
import re
from datetime import datetime, time, timezone
from pathlib import Path
from functools import lru_cache
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.crud import get_chats_overview
from app.db.db import SessionLocal
from app.db.models import Chat

logger = logging.getLogger(__name__)

DATE_PART = r"(\d{2}\.\d{2}\.\d{4})"
# Вторая дата необязательна: одна дата означает период из этого одного дня
PERIOD_PATTERN = re.compile(rf"^{DATE_PART}(?:\s*-\s*{DATE_PART})?$")
DATE_INPUT_FORMAT = "%d.%m.%Y"

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

# Чат выбирают на одном из трёх экранов. Какой открыт — нужно знать, чтобы
# ошибка ввода показалась там же, а не перебрасывала человека на другой
CHAT_SCREEN_KEY = "chat_screen"
QUESTION_SCREEN = "question"
LIST_SCREEN = "list"
IDS_SCREEN = "ids"

# Просит ли открытый экран прислать номер чата. Где не просит — там присланный
# текст мусор: его убираем молча, вместо того чтобы отвечать на него экраном
CHAT_ASKS_ID_KEY = "chat_asks_id"

# Кнопок в одном сообщении Telegram держит немного, остальные чаты выбираются вводом ID
MAX_CHAT_BUTTONS = 20

# Команды существуют в одном экземпляре: их печатают и приветствие, и ответ на
# неизвестную команду, и описание бота, и меню команд в самом Telegram, а копии
# однажды уже разъехались
COMMANDS = (
    ("export", "переписка выбранного чата за период: страница или JSON."),
    ("files", "архив с вложениями этих сообщений."),
)

# В меню команд Telegram ведёт и /start: это выход к главному окну из любого места
MENU_COMMANDS = (("start", "главное меню"),) + COMMANDS

COMMANDS_LIST = "\n".join(f"/{name} — {about}" for name, about in COMMANDS)

START_MESSAGE = (
    "Я бот для выгрузки корпоративных переписок.\n\n"
    "Вы можете управлять мной, отправляя следующие команды:\n\n" + COMMANDS_LIST
)

UNKNOWN_COMMAND = "Такой команды нет. Доступные команды:\n\n" + COMMANDS_LIST

# Описание видно до первого сообщения — на том экране, где Telegram рисует кнопку
# «Старт», — и потом по кнопке «Что умеет этот бот?». Разметку там не показывают
BOT_DESCRIPTION = (
    "Выгружаю переписки корпоративных чатов, которые собирает бот-коллектор.\n\n"
    + COMMANDS_LIST
    + "\n\nДоступ только у администраторов."
)

# Короткое описание стоит в профиле под названием, там места на одну строку
BOT_SHORT_DESCRIPTION = (
    "Выгрузка корпоративных переписок: сообщения за период и архив вложений."
)

CHAT_QUESTION = "Из какого чата взять данные?"

CHAT_LIST_HEADER = "Выберите чат из списка:"

CHAT_IDS_HEADER = (
    "telegram_chat_id собранных чатов. Нажмите на номер, чтобы скопировать его."
)

NO_CHATS_MESSAGE = (
    "В базе пока нет ни одного чата. Добавьте бота-коллектора в чат "
    "и напишите там сообщение — чат появится в списке."
)

BAD_CHAT_ID = (
    "Это не похоже на telegram_chat_id. Выберите его из списка ниже."
)

UNKNOWN_CHAT_ID = "Чата с таким telegram_chat_id в базе нет."

PERIOD_PROMPT = (
    "За какой период? Введите дату ДД.ММ.ГГГГ "
    "или диапазон ДД.ММ.ГГГГ - ДД.ММ.ГГГГ"
)


class PeriodError(ValueError):
    """Текст ошибки уже готов к отправке пользователю."""


def machine_timezone():
    """Пояс самой машины: в нём человек видит время и в своём Telegram."""
    path = Path("/etc/timezone")
    if path.is_file():
        name = path.read_text(encoding="utf-8").strip()
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("Система назвала неизвестный пояс %r", name)

    # Имени нет — остаётся текущий сдвиг машины, он же обычно и нужен
    return datetime.now().astimezone().tzinfo or timezone.utc


@lru_cache(maxsize=1)
def display_timezone():
    """Пояс, в котором показываем время. В базе оно всегда хранится в UTC."""
    name = os.getenv("DISPLAY_TIMEZONE")
    if not name:
        return machine_timezone()

    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Неизвестный DISPLAY_TIMEZONE=%r, берём пояс машины", name)
        return machine_timezone()


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


def _rounded(value):
    """Число с десятыми, но без пустого хвоста: 467,4 и 52, а не 467 и 52,0."""
    return f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def format_size(size_bytes):
    """Размер файла словами. Старые вложения писались без размера."""
    if size_bytes is None:
        return "неизвестен"
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    if size_bytes < 1024 * 1024:
        return f"{_rounded(size_bytes / 1024)} КБ"
    return f"{_rounded(size_bytes / (1024 * 1024))} МБ"


def format_duration(seconds):
    """Длительность как в плеере: 0:42, а для долгих записей 1:05:03."""
    if seconds is None:
        return None
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# В БД типы лежат так, как их называет Bot API, — выгрузку читают люди
FILE_TYPE_LABELS = {
    "photo": "фото",
    "animation": "GIF-анимация",
    "video": "видео",
    "video_note": "видеосообщение, кружок",
    "voice": "голосовое сообщение",
    "audio": "аудиофайл",
    "document": "документ",
}


def file_type_label(file_type):
    """Незнакомый тип отдаём как есть: соврать хуже, чем показать английское слово."""
    return FILE_TYPE_LABELS.get(file_type, file_type)


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


def chat_list_view(header=CHAT_LIST_HEADER):
    """Второй экран: (текст, клавиатура, ждём ли номер текстом).

    Если чатов нет, показывать нечего — (None, None, False). Номер просим только
    когда кнопок хватило не на все чаты: иначе чат выбирают кнопкой, и присланный
    на этом экране текст — просто мусор.

    В header передают замечание, если прошлый ввод не подошёл: оно встаёт на
    место обычной строки-приглашения, а не поверх неё.
    """
    chats = chats_overview()
    if not chats:
        return None, None, False

    lines = [header]
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

    return "\n".join(lines), builder.as_markup(), hidden > 0


def chat_ids_view(header=CHAT_IDS_HEADER):
    """Экран со списком telegram_chat_id. Номера в теге code — их удобно копировать.

    В header передают замечание, если прошлый ввод не подошёл: объяснять, зачем
    экран открыт, уже незачем — человек его видит, а вот что делать теперь, нет.
    """
    chats = chats_overview()
    if not chats:
        return None, None

    lines = [header, ""]
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
    """Уборка в переписке: не вышло — и ладно, диалог от этого не зависит.

    Ловим любую ошибку Telegram, а не только «сообщения нет»: при быстром вводе
    подряд удаления могут упереться в ограничение частоты запросов, и лишний
    мусор в переписке лучше упавшего обработчика.
    """
    if message_id is None:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramAPIError as error:
        logger.info("Сообщение %s удалить не удалось: %s", message_id, error)


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
    """Шаги диалога снизу вверх: «экран бота и то, что человек ввёл над ним».

    У шагов, открытых кнопкой, ввода нет — там None. По этим парам диалог
    откатывается: убрать экран вместе с вводом, на который он отвечает,
    и оживить тот, что был выше.
    """
    return [[step[0], step[1]] for step in data.get(STEPS_KEY, [])]


def _menu_in_sight(data):
    """Меню всё ещё на виду: ниже него только команды и экраны диалога.

    Такое меню можно переиспользовать, а команды и экраны — убрать: в переписке
    не останется дыр. Стоит появиться выгрузке или пользовательскому вводу, как
    всё выше становится историей, и трогать её уже нельзя.
    """
    return (
        data.get("menu_message_id") is not None
        and data.get("file_message_id") is None
        and all(input_message_id is None for _, input_message_id in _steps(data))
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
    bot,
    chat_id,
    state,
    text,
    keyboard=None,
    parse_mode=None,
    answers=None,
    back=False,
):
    """Показывает шаг диалога.

    Переход по кнопке правит текущий экран на месте: диалог живёт в одном
    сообщении и переписка от него не растёт. Если это ответ на присланный текст
    (answers), экран должен встать под ним, а не поверх — тогда у прошлого шага
    снимаем кнопки и присылаем новый вниз. Шаг назад (back) возвращает диалог
    туда, откуда он ушёл: экран и все вводы под ним убираются, а прошлый экран
    оживает.
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
            steps=steps + [[screen.message_id, answers]],
            file_message_id=None,
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
        except TelegramBadRequest as error:
            # Экран уже такой: второй раз то же замечание, менять нечего. Telegram
            # считает это ошибкой, но присылать копию экрана тут точно не нужно
            if "not modified" in str(error):
                await state.update_data(steps=steps)
                return
            logger.info("Экран %s изменить не удалось, присылаем новый", steps[-1][0])
            steps.pop()

    await _drop_buttons(bot, chat_id, file_message_id)
    screen = await bot.send_message(
        chat_id, text, reply_markup=keyboard, parse_mode=parse_mode
    )
    await state.update_data(
        steps=steps + [[screen.message_id, None]], file_message_id=None
    )


async def show_chat_question(
    bot, chat_id, state, states, notice=None, answers=None, back=False
):
    text = CHAT_QUESTION if notice is None else f"{notice}\n\n{CHAT_QUESTION}"
    await state.set_state(states.waiting_chat_id)
    # Здесь только кнопки: номер чата человек может прислать, но его не просят
    await state.update_data(chat_screen=QUESTION_SCREEN, chat_asks_id=False)
    await show_screen(
        bot,
        chat_id,
        state,
        text,
        chat_question_keyboard(),
        answers=answers,
        back=back,
    )


async def show_chat_list(bot, chat_id, state, states, notice=None, answers=None):
    """Возвращает False, если список пуст и показывать нечего.

    answers — id присланного сообщения, если список открывается ответом на него.
    notice — замечание о прошлом вводе; оно заменяет строку-приглашение, потому
    что говорит то же самое и вместо неё.
    """
    text, keyboard, asks_id = chat_list_view(notice or CHAT_LIST_HEADER)
    if text is None:
        return False

    await state.set_state(states.waiting_chat_id)
    await state.update_data(chat_screen=LIST_SCREEN, chat_asks_id=asks_id)
    await show_screen(bot, chat_id, state, text, keyboard, answers=answers)
    return True


async def show_chat_ids(bot, chat_id, state, states, notice=None, answers=None):
    """Возвращает False, если чатов в базе нет.

    notice — замечание о прошлом вводе; оно заменяет заголовок экрана, иначе
    к списку номеров прибавляется ещё абзац, а он и без того длинный.
    """
    text, keyboard = chat_ids_view(notice or CHAT_IDS_HEADER)
    if text is None:
        return False

    await state.set_state(states.waiting_chat_id)
    # Экран для того и открыт, чтобы скопировать номер и прислать его
    await state.update_data(chat_screen=IDS_SCREEN, chat_asks_id=True)
    await show_screen(
        bot,
        chat_id,
        state,
        text,
        keyboard,
        parse_mode="HTML",
        answers=answers,
    )
    return True


async def _reject_chat_input(bot, chat_id, state, states, notice, answers):
    """Присланное на выбор чата не подошло.

    Убираем сам ввод и правим открытый экран на месте, дописывая к нему замечание.
    Экраны выбора чата длинные: если отвечать на неудачный ввод новым экраном, в
    переписке остаётся вторая копия всего списка, и с каждой попыткой ещё одна.
    Так же поступаем и с экраном-вопросом — ради одного правила на все три.

    Человек при этом ничего не теряет: править отправленное всё равно нельзя,
    а номер, который не разобрали или не нашли, перенабирают, а не исправляют.
    """
    await delete_message(bot, chat_id, answers)

    data = await state.get_data()
    screen = data.get(CHAT_SCREEN_KEY, QUESTION_SCREEN)
    shown = False

    if screen == LIST_SCREEN:
        shown = await show_chat_list(bot, chat_id, state, states, notice=notice)
    elif screen == IDS_SCREEN:
        shown = await show_chat_ids(bot, chat_id, state, states, notice=notice)

    # Чаты могли исчезнуть из базы, пока человек печатал, — тогда показывать
    # список нечем, и остаётся первый экран выбора
    if not shown:
        await show_chat_question(bot, chat_id, state, states, notice=notice)


async def reject_bad_chat_id(bot, chat_id, state, states, answers):
    """Присланное не разобрать как номер чата.

    Где чат выбирают кнопкой, номер никто не просил: случайный текст под таким
    экраном — мусор. Его убираем молча, сказать о таком вводе всё равно нечего.
    Если же номер на экране как раз ждут — на списке номеров или когда кнопок
    хватило не на все чаты, — подсказываем формат.
    """
    data = await state.get_data()
    if not data.get(CHAT_ASKS_ID_KEY):
        await delete_message(bot, chat_id, answers)
        return

    await _reject_chat_input(bot, chat_id, state, states, BAD_CHAT_ID, answers)


async def reject_unknown_chat(bot, chat_id, state, states, answers):
    """Номер разобран, но такого чата в базе нет — это не зависит от экрана."""
    await _reject_chat_input(bot, chat_id, state, states, UNKNOWN_CHAT_ID, answers)


async def show_period_question(bot, chat_id, state, notice=None, answers=None, back=False):
    """Шаг с периодом. В notice передают, чем закончилась прошлая попытка.

    Замечание идёт вместо вопроса, а не над ним: формат от этого не теряется.
    Либо он в самом замечании примером, либо присланное уже было правильного
    вида — тогда человек формат знает, и дело не в нём.
    """
    await show_screen(
        bot,
        chat_id,
        state,
        notice or PERIOD_PROMPT,
        period_keyboard(),
        answers=answers,
        back=back,
    )


async def reject_period(bot, chat_id, state, notice, answers):
    """Присланный период не подошёл.

    В отличие от выбора чата, здесь ничего не убирается: и присланные даты, и
    прежние замечания остаются в переписке. Даты человек набирает сам, и видеть
    все свои попытки рядом с ответами бота полезнее, чем чистое окно.
    """
    await show_period_question(bot, chat_id, state, notice=notice, answers=answers)


async def show_empty_result(bot, chat_id, state, text, answers):
    """Ничего не нашлось: файла не будет, зато видно, куда вернуться."""
    await show_screen(
        bot, chat_id, state, text, empty_result_keyboard(), answers=answers
    )


async def forget_menu(state):
    """Прежнее меню больше не переиспользовать: под ним появился чужой текст.

    Пока ниже меню только команды и экраны диалога, бот вправе всё это убрать и
    оставить одно живое окно. Сообщение, которого он не присылал, так не уберёшь,
    поэтому такое меню становится историей и остаётся в переписке.
    """
    await state.update_data(menu_message_id=None)


async def open_menu(bot, message, state, text=START_MESSAGE):
    """Меню по команде /start: она уже внизу переписки, поэтому меню присылаем новым.

    Прежнее меню и следы диалога убираем, если они ещё были последними, — иначе
    список команд задвоится. В text передают, чем меню открывается: приветствием
    или объяснением, почему присланное не подошло, — команды в нём те же.
    """
    data = await state.get_data()
    chat_id = message.chat.id
    await state.clear()

    if await _clear_steps(bot, chat_id, data):
        await delete_message(bot, chat_id, data.get("menu_message_id"))
        for command_message_id in data.get(COMMANDS_KEY, []):
            await delete_message(bot, chat_id, command_message_id)
    await _drop_buttons(bot, chat_id, data.get("file_message_id"))

    menu = await message.answer(text)
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


def _file_label(day_from, day_to):
    """Период в имени файла. За один день — одна дата, а не она же дважды."""
    if day_from == day_to:
        return day_from.strftime(FILENAME_DATE_FORMAT)
    return (
        f"{day_from.strftime(FILENAME_DATE_FORMAT)}_"
        f"{day_to.strftime(FILENAME_DATE_FORMAT)}"
    )


def _text_label(day_from, day_to):
    """Период в тексте сообщений: «17.09.2026» или «01.09.2026 — 17.09.2026»."""
    if day_from == day_to:
        return day_from.strftime(DATE_INPUT_FORMAT)
    return (
        f"{day_from.strftime(DATE_INPUT_FORMAT)} — "
        f"{day_to.strftime(DATE_INPUT_FORMAT)}"
    )


def parse_period(text):
    """Возвращает (граница «с» в UTC, граница «по» в UTC, подпись в имя файла, подпись в текст).

    Даты пользователь называет по своему времени, а в базе всё в UTC, поэтому
    границы суток переводим в UTC — иначе край периода съедет на размер смещения.
    """
    match = PERIOD_PATTERN.match((text or "").strip())
    if not match:
        # Формат не повторяем: он в вопросе выше. Примеры полезнее — по ним видно
        # и разделитель, и то, что год пишется полностью, и что дата бывает одна
        raise PeriodError(
            "Неверный формат. Например: 17.09.2026 или 01.09.2026 - 17.09.2026"
        )

    day_from = _parse_date(match.group(1))
    # Одна дата — это период из одного дня: спрашивают обычно «что было вчера»
    day_to = _parse_date(match.group(2)) if match.group(2) else day_from

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
        _file_label(day_from, day_to),
        _text_label(day_from, day_to),
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
