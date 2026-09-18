import asyncio
import io
import logging
import zipfile

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
    NO_CHATS_MESSAGE,
    PERIOD_CALLBACK,
    PeriodError,
    accept_chat,
    chats_overview,
    delete_message,
    delivered_keyboard,
    file_type_label,
    finish_dialog,
    format_datetime,
    format_day,
    format_size,
    open_dialog,
    parse_period,
    sender_name,
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
from app.db.crud import get_messages_by_period
from app.db.db import SessionLocal
from app.paths import ATTACHMENTS_DIR, BASE_DIR

logger = logging.getLogger(__name__)

router = Router()

# Через Bot API бот не может отправить файл больше 50 МБ, берём с запасом
MAX_ARCHIVE_BYTES = 45 * 1024 * 1024
MANIFEST_NAME = "список_вложений.txt"
MANIFEST_HEADER = "дата | отправитель | тип | имя в архиве | оригинальное имя"

DOCUMENT_TYPES = {"document"}
PICTURE_TYPES = {"photo", "video", "animation"}
# Кружок здесь со звуком, а не с видео: в переписке он звучит как голосовое
SOUND_TYPES = {"voice", "video_note", "audio"}
ALL_TYPES = DOCUMENT_TYPES | PICTURE_TYPES | SOUND_TYPES

# Для перечисления найденного нужны названия во множественном числе
COUNT_LABELS = {
    "photo": "фото",
    "animation": "GIF-анимации",
    "video": "видео",
    "video_note": "кружки",
    "voice": "голосовые",
    "audio": "аудиофайлы",
    "document": "документы",
}

TYPES_PROMPT = "Какие вложения включить в архив?"
VOICE_PROMPT = "Выгрузить голосовые сообщения?"

ALL_TYPES_CALLBACK = "files:kind:all"
DOCUMENTS_CALLBACK = "files:kind:documents"
PICTURES_CALLBACK = "files:kind:pictures"
TYPES_CALLBACK = "files:kind:back"
VOICE_YES_CALLBACK = "files:voice:yes"
VOICE_NO_CALLBACK = "files:voice:no"


class FilesStates(StatesGroup):
    waiting_chat_id = State()
    waiting_period = State()
    waiting_types = State()
    waiting_voice = State()


def types_keyboard():
    """Три способа собрать архив, а под ними выходы: они не про вложения."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Все вложения", callback_data=ALL_TYPES_CALLBACK)
    builder.button(text="Только документы", callback_data=DOCUMENTS_CALLBACK)
    builder.button(text="Фото и видео", callback_data=PICTURES_CALLBACK)
    builder.button(text="Назад", callback_data=PERIOD_CALLBACK)
    builder.button(text="Главное меню", callback_data=MENU_CALLBACK)
    builder.adjust(2, 1, 2)
    return builder.as_markup()


def voice_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Да", callback_data=VOICE_YES_CALLBACK)
    builder.button(text="Нет", callback_data=VOICE_NO_CALLBACK)
    builder.button(text="Назад", callback_data=TYPES_CALLBACK)
    builder.adjust(2, 1)
    return builder.as_markup()


def _resolve(file_path):
    """Путь из БД должен вести внутрь storage/attachments и никуда больше."""
    path = (BASE_DIR / file_path).resolve()
    if ATTACHMENTS_DIR.resolve() not in path.parents:
        return None
    return path


def _counts(messages):
    """Сколько вложений каждого типа в периоде: с этим выбор типа осмысленный."""
    counts = {}
    for message in messages:
        for attachment in message.attachments:
            counts[attachment.file_type] = counts.get(attachment.file_type, 0) + 1
    return counts


def _counts_text(counts, period_label):
    lines = [
        f"• {COUNT_LABELS.get(file_type, file_type)} — {number}"
        for file_type, number in sorted(counts.items(), key=lambda item: -item[1])
    ]
    header = f"За {period_label} найдено:"
    return "\n".join([TYPES_PROMPT, "", header] + lines)


def _collect(messages, kinds):
    """Разбирает вложения периода: отправляемые, не скачанные, пропавшие, большие.

    Всё, чей тип не выбран, пропускается молча: сообщать о проблемах с файлами,
    которых человек не просил, значит сбивать с толку.
    """
    found = []
    missing = []
    oversized = []
    not_saved = []

    for message in messages:
        day = format_day(message.sent_at) or "без-даты"
        sent = format_datetime(message.sent_at) or "дата неизвестна"

        for attachment in message.attachments:
            if attachment.file_type not in kinds:
                continue

            if attachment.file_path is None:
                # Коллектор знал о вложении, но файл не получил: лимит или сбой загрузки
                name = attachment.original_filename or file_type_label(
                    attachment.file_type
                )
                not_saved.append((name, attachment.file_size))
                continue

            path = _resolve(attachment.file_path)
            if path is None:
                logger.warning(
                    "Путь вложения ведёт за пределы storage/attachments: %s",
                    attachment.file_path,
                )
            if path is None or not path.is_file():
                missing.append(attachment.file_path)
                continue

            size = path.stat().st_size
            if size > MAX_ARCHIVE_BYTES:
                oversized.append((path.name, size))
                continue

            arcname = f"{day}/{path.name}"
            description = " | ".join(
                [
                    sent,
                    sender_name(message.user) or "автор неизвестен",
                    file_type_label(attachment.file_type),
                    arcname,
                    attachment.original_filename or path.name,
                ]
            )
            found.append((arcname, path, size, description))

    return found, missing, oversized, not_saved


def _build_archives(found):
    """Собирает ZIP-архивы, разрезая вложения так, чтобы каждый влез в лимит Telegram."""
    chunks = []
    current = []
    current_size = 0
    for item in found:
        size = item[2]
        if current and current_size + size > MAX_ARCHIVE_BYTES:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(item)
        current_size += size
    if current:
        chunks.append(current)

    archives = []
    for chunk in chunks:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for arcname, path, _, _ in chunk:
                try:
                    archive.write(path, arcname)
                except OSError:
                    logger.exception("Не удалось добавить в архив %s", path)
            manifest = [MANIFEST_HEADER] + [item[3] for item in chunk]
            archive.writestr(MANIFEST_NAME, "\n".join(manifest))
        archives.append(buffer.getvalue())
    return archives


def _names_with_size(items):
    return ", ".join(f"{name} ({format_size(size)})" for name, size in items)


def _problem_lines(missing, oversized, not_saved):
    lines = []
    if missing:
        lines.append(
            f"Нет файлов на диске: {len(missing)} шт. — "
            "скорее всего, их удалили из storage/attachments."
        )
    if oversized:
        lines.append(
            "Слишком большие, бот не может их отправить: "
            f"{_names_with_size(oversized)}"
        )
    if not_saved:
        lines.append(
            "Коллектор не смог скачать: "
            f"{_names_with_size(not_saved)} — "
            "Bot API отдаёт боту файлы не больше 20 МБ."
        )
    return lines


@router.message(Command("files", ignore_case=True))
async def cmd_files(message: Message, state: FSMContext, bot: Bot):
    if not chats_overview():
        # Состояние сбрасываем, но данные оставляем: в них номер меню
        await state.set_state(None)
        await message.answer(NO_CHATS_MESSAGE)
        return

    await open_dialog(bot, message, state, FilesStates)


@router.callback_query(
    StateFilter(FilesStates.waiting_chat_id),
    F.data == CHAT_LIST_CALLBACK,
)
async def show_list(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not await show_chat_list(bot, callback.from_user.id, state, FilesStates):
        await callback.answer(NO_CHATS_MESSAGE, show_alert=True)
        return
    await callback.answer()


@router.callback_query(
    StateFilter(FilesStates.waiting_period),
    F.data == CHAT_QUESTION_CALLBACK,
)
async def back_to_chat(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Возврат к выбору чата с шага периода и из-под отправленного архива."""
    await show_chat_question(bot, callback.from_user.id, state, FilesStates, back=True)
    await callback.answer()


@router.callback_query(
    StateFilter(FilesStates.waiting_period),
    F.data == PERIOD_CALLBACK,
)
async def back_to_period(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await show_period_question(bot, callback.from_user.id, state, back=True)
    await callback.answer()


@router.callback_query(
    StateFilter(FilesStates.waiting_chat_id),
    F.data == CHAT_IDS_CALLBACK,
)
async def show_ids(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not await show_chat_ids(bot, callback.from_user.id, state, FilesStates):
        await callback.answer(NO_CHATS_MESSAGE, show_alert=True)
        return
    await callback.answer()


@router.callback_query(
    StateFilter(FilesStates.waiting_chat_id),
    F.data == CHAT_QUESTION_CALLBACK,
)
async def back_to_question(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await show_chat_question(bot, callback.from_user.id, state, FilesStates, back=True)
    await callback.answer()


@router.callback_query(
    StateFilter(FilesStates.waiting_chat_id),
    F.data.startswith(CHAT_CALLBACK_PREFIX),
)
async def choose_chat(callback: CallbackQuery, state: FSMContext, bot: Bot):
    telegram_chat_id = int(callback.data.removeprefix(CHAT_CALLBACK_PREFIX))
    if not await accept_chat(state, telegram_chat_id, FilesStates.waiting_period):
        await callback.answer("Этого чата уже нет в базе", show_alert=True)
        return

    await callback.answer()
    await show_period_question(bot, callback.from_user.id, state)


@router.message(StateFilter(FilesStates.waiting_chat_id))
async def receive_chat_id(message: Message, state: FSMContext, bot: Bot):
    try:
        telegram_chat_id = int((message.text or "").strip())
    except ValueError:
        await reject_bad_chat_id(
            bot, message.chat.id, state, FilesStates, message.message_id
        )
        return

    if not await accept_chat(state, telegram_chat_id, FilesStates.waiting_period):
        await reject_unknown_chat(
            bot, message.chat.id, state, FilesStates, message.message_id
        )
        return

    await show_period_question(bot, message.chat.id, state, answers=message.message_id)


@router.message(StateFilter(FilesStates.waiting_period))
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
        counts = _counts(messages)
    finally:
        db.close()

    # Спрашивать про типы, когда выбирать не из чего, — только сбивать с толку
    if not counts:
        notice = (
            "За этот период сообщений не найдено."
            if not messages
            else "За этот период вложений нет."
        )
        await show_empty_result(
            bot, message.chat.id, state, notice, answers=message.message_id
        )
        return

    await state.update_data(
        date_from=date_from,
        date_to=date_to,
        file_label=file_label,
        text_label=text_label,
    )
    await state.set_state(FilesStates.waiting_types)
    await show_screen(
        bot,
        message.chat.id,
        state,
        _counts_text(counts, text_label),
        types_keyboard(),
        answers=message.message_id,
    )


async def _deliver(bot, chat_id, state, kinds):
    """Собирает и отправляет архив из вложений выбранных типов."""
    data = await state.get_data()
    telegram_chat_id = data["telegram_chat_id"]
    file_label = data["file_label"]

    db = SessionLocal()
    try:
        messages = get_messages_by_period(
            db, data["chat_pk"], data["date_from"], data["date_to"]
        )
        found, missing, oversized, not_saved = _collect(messages, kinds)
    finally:
        db.close()

    problems = _problem_lines(missing, oversized, not_saved)

    # Кнопки под выгрузкой и под пустым ответом ведут к периоду: туда и возвращаем
    await state.set_state(FilesStates.waiting_period)

    if not found:
        if problems:
            notice = "Отправить нечего.\n" + "\n".join(problems)
        else:
            notice = "Вложений выбранного типа за этот период нет."
        await show_empty_result(bot, chat_id, state, notice, answers=None)
        return

    progress = await bot.send_message(chat_id, f"Собираю архив, вложений: {len(found)}")

    # Упаковка десятков мегабайт блокирует event loop, поэтому уводим её в поток
    archives = await asyncio.to_thread(_build_archives, found)

    # Своё же уведомление о сборке после архива читать незачем
    await delete_message(bot, chat_id, progress.message_id)

    total = len(archives)
    delivered = None
    for number, content in enumerate(archives, start=1):
        suffix = "" if total == 1 else f"_часть{number}"
        filename = (
            f"attachments_{telegram_chat_id}_{file_label}{suffix}.zip"
        )
        if total == 1:
            caption = f"Вложений в архиве: {len(found)}"
        else:
            caption = f"Часть {number} из {total}"
        # Кнопки нужны под самым последним сообщением, иначе окажутся выше текста
        last = number == total and not problems
        sent = await bot.send_document(
            chat_id,
            BufferedInputFile(content, filename=filename),
            caption=caption,
            reply_markup=delivered_keyboard() if last else None,
        )
        delivered = sent.message_id

    if problems:
        sent = await bot.send_message(
            chat_id, "\n".join(problems), reply_markup=delivered_keyboard()
        )
        delivered = sent.message_id

    await finish_dialog(bot, chat_id, state, delivered)


@router.callback_query(
    StateFilter(FilesStates.waiting_types),
    F.data == ALL_TYPES_CALLBACK,
)
async def choose_all_types(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await _deliver(bot, callback.from_user.id, state, ALL_TYPES)


@router.callback_query(
    StateFilter(FilesStates.waiting_types),
    F.data == DOCUMENTS_CALLBACK,
)
async def choose_documents(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await _deliver(bot, callback.from_user.id, state, DOCUMENT_TYPES)


@router.callback_query(
    StateFilter(FilesStates.waiting_types),
    F.data == PICTURES_CALLBACK,
)
async def ask_about_voice(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Картинки выбраны, осталось решить судьбу голосовых и кружков."""
    await state.set_state(FilesStates.waiting_voice)
    await show_screen(
        bot, callback.from_user.id, state, VOICE_PROMPT, voice_keyboard()
    )
    await callback.answer()


@router.callback_query(
    StateFilter(FilesStates.waiting_types),
    F.data == PERIOD_CALLBACK,
)
async def back_from_types(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.set_state(FilesStates.waiting_period)
    await show_period_question(bot, callback.from_user.id, state, back=True)
    await callback.answer()


@router.callback_query(
    StateFilter(FilesStates.waiting_voice),
    F.data == VOICE_YES_CALLBACK,
)
async def pictures_with_voice(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await _deliver(bot, callback.from_user.id, state, PICTURE_TYPES | SOUND_TYPES)


@router.callback_query(
    StateFilter(FilesStates.waiting_voice),
    F.data == VOICE_NO_CALLBACK,
)
async def pictures_without_voice(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    await _deliver(bot, callback.from_user.id, state, PICTURE_TYPES)


@router.callback_query(
    StateFilter(FilesStates.waiting_voice),
    F.data == TYPES_CALLBACK,
)
async def back_to_types(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Экран с типами открывался по вводу периода, поэтому правим его на месте."""
    data = await state.get_data()

    db = SessionLocal()
    try:
        messages = get_messages_by_period(
            db, data["chat_pk"], data["date_from"], data["date_to"]
        )
        counts = _counts(messages)
    finally:
        db.close()

    await state.set_state(FilesStates.waiting_types)
    await show_screen(
        bot,
        callback.from_user.id,
        state,
        _counts_text(counts, data["text_label"]),
        types_keyboard(),
    )
    await callback.answer()


@router.message(StateFilter(FilesStates.waiting_types, FilesStates.waiting_voice))
async def ignore_typing(message: Message, bot: Bot):
    """На этих экранах отвечают кнопкой, поэтому присланный текст убираем.

    Без такого обработчика он доходил бы до заглушки, а та открывает меню и
    сбрасывает диалог: чат и период пришлось бы называть заново.
    """
    await delete_message(bot, message.chat.id, message.message_id)
