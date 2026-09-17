"""Команда /search: кто и когда писал про нужное слово.

Ищем сразу по всей истории чата — так быстрее всего понять, обсуждалась ли тема
вообще. Период накидывается сверху отдельной кнопкой, когда совпадений слишком
много. Найденное показываем списком прямо в переписке, а полную выгрузку отдаём
файлом по кнопке: в чате читают глазами, в файле разбирают подробности.
"""

import json
import re
from html import escape

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
    KEYWORD_CALLBACK,
    MENU_CALLBACK,
    NO_CHATS_MESSAGE,
    PERIOD_CALLBACK,
    PeriodError,
    accept_chat,
    chats_overview,
    finish_dialog,
    open_dialog,
    parse_period,
    sender_name,
    show_chat_ids,
    show_chat_list,
    show_chat_question,
    show_period_question,
    show_screen,
    to_display,
)
from app.admin_bot.serialize import timeline
from app.db.crud import search_messages
from app.db.db import SessionLocal

router = Router()

KEYWORD_PROMPT = "Что искать? Пришлите слово или фразу."

# По одной букве найдётся половина переписки, читать такое бесполезно
MIN_KEYWORD = 2
MAX_KEYWORD = 100

# Сколько совпадений показываем в переписке. Остальное — в файле по кнопке:
# длинные простыни в чате всё равно не читают, а лимит сообщения Telegram близко
PREVIEW_COUNT = 10

# Отрывок вокруг найденного слова: столько символов слева и справа от него
PREVIEW_BEFORE = 40
PREVIEW_AFTER = 120

# В списке секунды только мешают, они есть в выгрузке
LIST_DATETIME_FORMAT = "%d.%m.%Y %H:%M"


class SearchStates(StatesGroup):
    waiting_chat_id = State()
    waiting_keyword = State()
    waiting_period = State()


def question_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="К выбору чата", callback_data=CHAT_QUESTION_CALLBACK)
    builder.button(text="В главное меню", callback_data=MENU_CALLBACK)
    builder.adjust(2)
    return builder.as_markup()


def results_keyboard(found, narrowed):
    """Кнопки под результатами. Состав зависит от того, есть ли что выгружать."""
    builder = InlineKeyboardBuilder()
    if found:
        builder.button(text="Выгрузить найденное в JSON", callback_data="search:file")

    if narrowed:
        builder.button(text="Искать за всё время", callback_data="search:all")
    else:
        builder.button(text="Сузить по датам", callback_data=PERIOD_CALLBACK)

    builder.button(text="К выбору чата", callback_data=CHAT_QUESTION_CALLBACK)
    builder.button(text="В главное меню", callback_data=MENU_CALLBACK)
    if found:
        builder.adjust(1, 1, 2)
    else:
        builder.adjust(1, 2)
    return builder.as_markup()


def delivered_keyboard():
    """Под отправленным файлом: продолжить поиск или уйти из диалога."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Новый поиск", callback_data=KEYWORD_CALLBACK)
    builder.button(text="Выбор чатов", callback_data=CHAT_QUESTION_CALLBACK)
    builder.button(text="Главное меню", callback_data=MENU_CALLBACK)
    builder.adjust(1, 2)
    return builder.as_markup()


def _preview(text, keyword):
    """Отрывок вокруг первого совпадения, найденное слово выделено жирным."""
    flat = " ".join((text or "").split())
    position = flat.lower().find(keyword.lower())
    if position < 0:
        # Совпадение было в тексте до схлопывания переносов — покажем начало
        return escape(flat[:PREVIEW_AFTER]) + ("…" if len(flat) > PREVIEW_AFTER else "")

    start = max(0, position - PREVIEW_BEFORE)
    end = min(len(flat), position + len(keyword) + PREVIEW_AFTER)

    head = ("…" if start > 0 else "") + escape(flat[start:position])
    match = escape(flat[position : position + len(keyword)])
    tail = escape(flat[position + len(keyword) : end]) + ("…" if end < len(flat) else "")
    return f"{head}<b>{match}</b>{tail}"


def _messages_word(count):
    """«1 сообщение», «2 сообщения», «5 сообщений» — иначе заголовок режет глаз."""
    if 11 <= count % 100 <= 14:
        return "сообщений"
    last = count % 10
    if last == 1:
        return "сообщение"
    if 2 <= last <= 4:
        return "сообщения"
    return "сообщений"


def _results_text(messages, keyword, period_label):
    """Заголовок с числом совпадений и последние из них — свежее обычно важнее."""
    where = f" за {period_label}" if period_label else ""
    header = (
        f"Нашёл {len(messages)} {_messages_word(len(messages))} "
        f"со словом «{escape(keyword)}»{where}."
    )

    shown = messages[-PREVIEW_COUNT:]
    if len(shown) < len(messages):
        header += f"\nПоказываю последние {len(shown)}, остальное — в файле."

    lines = [header, ""]
    for message in shown:
        author = escape(sender_name(message.user) or "не определён")
        when = to_display(message.sent_at).strftime(LIST_DATETIME_FORMAT)
        lines.append(f"{when} · {author}")
        lines.append(_preview(message.text, keyword))
        lines.append("")

    return "\n".join(lines).strip()


def _filename(telegram_chat_id, keyword):
    slug = re.sub(r"\W+", "_", keyword, flags=re.UNICODE).strip("_")[:30]
    return f"search_{telegram_chat_id}_{slug or 'запрос'}.json"


def _find(data):
    """Выполняет поиск по тому, что уже выбрано в диалоге."""
    db = SessionLocal()
    try:
        messages = search_messages(
            db,
            data["chat_pk"],
            data["keyword"],
            data.get("date_from"),
            data.get("date_to"),
        )
        return messages, timeline(messages)
    finally:
        db.close()


async def _show_results(bot, chat_id, state, answers=None):
    """Показывает совпадения и запоминает выгрузку, чтобы собрать её по кнопке."""
    data = await state.get_data()
    messages, payload = _find(data)
    keyword = data["keyword"]
    period_label = data.get("period_label")

    if messages:
        text = _results_text(messages, keyword, period_label)
    else:
        where = f" за {period_label}" if period_label else ""
        text = (
            f"Ничего не нашёл по слову «{escape(keyword)}»{where}.\n"
            "Пришлите другое слово — поищу заново."
        )

    await state.set_state(SearchStates.waiting_keyword)
    await show_screen(
        bot,
        chat_id,
        state,
        text,
        results_keyboard(bool(messages), period_label is not None),
        parse_mode="HTML",
        answers=answers,
    )


@router.message(Command("search", ignore_case=True))
async def cmd_search(message: Message, state: FSMContext, bot: Bot):
    if not chats_overview():
        # Состояние сбрасываем, но данные оставляем: в них номер меню
        await state.set_state(None)
        await message.answer(NO_CHATS_MESSAGE)
        return

    await open_dialog(bot, message, state, SearchStates)


@router.callback_query(
    StateFilter(SearchStates.waiting_chat_id),
    F.data == CHAT_LIST_CALLBACK,
)
async def show_list(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not await show_chat_list(bot, callback.from_user.id, state, SearchStates):
        await callback.answer(NO_CHATS_MESSAGE, show_alert=True)
        return
    await callback.answer()


@router.callback_query(
    StateFilter(SearchStates.waiting_chat_id),
    F.data == CHAT_IDS_CALLBACK,
)
async def show_ids(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not await show_chat_ids(bot, callback.from_user.id, state, SearchStates):
        await callback.answer(NO_CHATS_MESSAGE, show_alert=True)
        return
    await callback.answer()


@router.callback_query(
    StateFilter(
        SearchStates.waiting_chat_id,
        SearchStates.waiting_keyword,
        SearchStates.waiting_period,
    ),
    F.data == CHAT_QUESTION_CALLBACK,
)
async def back_to_chat(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await show_chat_question(bot, callback.from_user.id, state, SearchStates, back=True)
    await callback.answer()


@router.callback_query(
    StateFilter(SearchStates.waiting_chat_id),
    F.data.startswith(CHAT_CALLBACK_PREFIX),
)
async def choose_chat(callback: CallbackQuery, state: FSMContext, bot: Bot):
    telegram_chat_id = int(callback.data.removeprefix(CHAT_CALLBACK_PREFIX))
    if not await accept_chat(state, telegram_chat_id, SearchStates.waiting_keyword):
        await callback.answer("Этого чата уже нет в базе", show_alert=True)
        return

    await callback.answer()
    await _show_keyword_question(bot, callback.from_user.id, state)


async def _show_keyword_question(bot, chat_id, state, notice=None, answers=None, back=False):
    text = KEYWORD_PROMPT if notice is None else f"{notice}\n\n{KEYWORD_PROMPT}"
    await state.set_state(SearchStates.waiting_keyword)
    await show_screen(
        bot, chat_id, state, text, question_keyboard(), answers=answers, back=back
    )


@router.callback_query(
    StateFilter(SearchStates.waiting_keyword, SearchStates.waiting_period),
    F.data == KEYWORD_CALLBACK,
)
async def back_to_keyword(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Новый поиск по тому же чату: период сбрасываем, он был от прошлого запроса."""
    await state.update_data(date_from=None, date_to=None, period_label=None)
    await _show_keyword_question(bot, callback.from_user.id, state, back=True)
    await callback.answer()


@router.message(StateFilter(SearchStates.waiting_chat_id))
async def receive_chat_id(message: Message, state: FSMContext, bot: Bot):
    try:
        telegram_chat_id = int((message.text or "").strip())
    except ValueError:
        await show_chat_list(
            bot,
            message.chat.id,
            state,
            SearchStates,
            notice="Выберите чат кнопкой или отправьте его telegram_chat_id.",
            answers=message.message_id,
        )
        return

    if not await accept_chat(state, telegram_chat_id, SearchStates.waiting_keyword):
        await show_chat_list(
            bot,
            message.chat.id,
            state,
            SearchStates,
            notice="Чат с таким telegram_chat_id не найден в базе.",
            answers=message.message_id,
        )
        return

    await _show_keyword_question(bot, message.chat.id, state, answers=message.message_id)


@router.message(StateFilter(SearchStates.waiting_keyword))
async def receive_keyword(message: Message, state: FSMContext, bot: Bot):
    keyword = (message.text or "").strip()

    if len(keyword) < MIN_KEYWORD:
        await _show_keyword_question(
            bot,
            message.chat.id,
            state,
            notice="Слишком короткий запрос: нужно хотя бы два символа.",
            answers=message.message_id,
        )
        return

    if len(keyword) > MAX_KEYWORD:
        await _show_keyword_question(
            bot,
            message.chat.id,
            state,
            notice=f"Слишком длинный запрос: не больше {MAX_KEYWORD} символов.",
            answers=message.message_id,
        )
        return

    await state.update_data(keyword=keyword)
    await _show_results(bot, message.chat.id, state, answers=message.message_id)


@router.callback_query(
    StateFilter(SearchStates.waiting_keyword),
    F.data == PERIOD_CALLBACK,
)
async def narrow_by_period(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.set_state(SearchStates.waiting_period)
    await show_period_question(bot, callback.from_user.id, state)
    await callback.answer()


@router.callback_query(
    StateFilter(SearchStates.waiting_keyword),
    F.data == "search:all",
)
async def drop_period(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.update_data(date_from=None, date_to=None, period_label=None)
    await _show_results(bot, callback.from_user.id, state)
    await callback.answer()


@router.message(StateFilter(SearchStates.waiting_period))
async def receive_period(message: Message, state: FSMContext, bot: Bot):
    try:
        date_from, date_to, label_from, label_to = parse_period(message.text)
    except PeriodError as error:
        await show_period_question(
            bot, message.chat.id, state, notice=str(error), answers=message.message_id
        )
        return

    # Подписи из parse_period сделаны для имени файла, в тексте нужны через точку
    await state.update_data(
        date_from=date_from,
        date_to=date_to,
        period_label=f"{label_from.replace('-', '.')} - {label_to.replace('-', '.')}",
    )
    await _show_results(bot, message.chat.id, state, answers=message.message_id)


@router.callback_query(
    StateFilter(SearchStates.waiting_keyword),
    F.data == "search:file",
)
async def send_file(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    # Собираем заново: держать выгрузку в состоянии диалога незачем, а данные
    # за время чтения списка могли и дополниться
    _, payload = _find(data)
    if not payload:
        await callback.answer("Выгружать нечего, поищите заново", show_alert=True)
        return

    await callback.answer()
    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    document = BufferedInputFile(
        content, filename=_filename(data["telegram_chat_id"], data["keyword"])
    )

    caption = f"Найдено по слову «{data['keyword']}»: {len(payload)}"
    if data.get("period_label"):
        caption += f", период {data['period_label']}"

    sent = await bot.send_document(
        callback.from_user.id,
        document,
        caption=caption,
        reply_markup=delivered_keyboard(),
    )
    await finish_dialog(bot, callback.from_user.id, state, sent.message_id)
