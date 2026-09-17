import asyncio
import io
import logging
import zipfile

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
    delete_message,
    delivered_keyboard,
    finish_dialog,
    format_datetime,
    format_day,
    open_dialog,
    parse_period,
    sender_name,
    show_chat_ids,
    show_chat_list,
    show_chat_question,
    show_empty_result,
    show_period_question,
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


class FilesStates(StatesGroup):
    waiting_chat_id = State()
    waiting_period = State()


def _resolve(file_path):
    """Путь из БД должен вести внутрь storage/attachments и никуда больше."""
    path = (BASE_DIR / file_path).resolve()
    if ATTACHMENTS_DIR.resolve() not in path.parents:
        return None
    return path


def _collect(messages):
    """Разбирает вложения периода на «можно отправить», «нет файла», «слишком большой»."""
    found = []
    missing = []
    oversized = []

    for message in messages:
        day = format_day(message.sent_at) or "без-даты"
        sent = format_datetime(message.sent_at) or "дата неизвестна"

        for attachment in message.attachments:
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
                    attachment.file_type,
                    arcname,
                    attachment.original_filename or path.name,
                ]
            )
            found.append((arcname, path, size, description))

    return found, missing, oversized


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


def _problem_lines(missing, oversized):
    lines = []
    if missing:
        lines.append(
            f"Нет файлов на диске: {len(missing)} шт. — "
            "скорее всего, их удалили из storage/attachments."
        )
    if oversized:
        names = ", ".join(
            f"{name} ({size / (1024 * 1024):.1f} МБ)" for name, size in oversized
        )
        lines.append(f"Слишком большие, бот не может их отправить: {names}")
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
        await show_chat_list(
            bot,
            message.chat.id,
            state,
            FilesStates,
            notice="Выберите чат кнопкой или отправьте его telegram_chat_id.",
            answers=message.message_id,
        )
        return

    if not await accept_chat(state, telegram_chat_id, FilesStates.waiting_period):
        await show_chat_list(
            bot,
            message.chat.id,
            state,
            FilesStates,
            notice="Чат с таким telegram_chat_id не найден в базе.",
            answers=message.message_id,
        )
        return

    await show_period_question(bot, message.chat.id, state, answers=message.message_id)


@router.message(StateFilter(FilesStates.waiting_period))
async def receive_period(message: Message, state: FSMContext, bot: Bot):
    try:
        date_from, date_to, label_from, label_to = parse_period(message.text)
    except PeriodError as error:
        await show_period_question(
            bot, message.chat.id, state, notice=str(error), answers=message.message_id
        )
        return

    data = await state.get_data()
    telegram_chat_id = data["telegram_chat_id"]

    db = SessionLocal()
    try:
        messages = get_messages_by_period(db, data["chat_pk"], date_from, date_to)
        found, missing, oversized = _collect(messages)
    finally:
        db.close()

    problems = _problem_lines(missing, oversized)

    if not found:
        if problems:
            notice = "Отправить нечего.\n" + "\n".join(problems)
        elif not messages:
            notice = "За этот период сообщений не найдено."
        else:
            notice = "За этот период вложений нет."
        await show_empty_result(
            bot, message.chat.id, state, notice, answers=message.message_id
        )
        return

    progress = await message.answer(f"Собираю архив, вложений: {len(found)}")

    # Упаковка десятков мегабайт блокирует event loop, поэтому уводим её в поток
    archives = await asyncio.to_thread(_build_archives, found)

    # Своё же уведомление о сборке после архива читать незачем
    await delete_message(bot, message.chat.id, progress.message_id)

    total = len(archives)
    delivered = None
    for number, content in enumerate(archives, start=1):
        suffix = "" if total == 1 else f"_часть{number}"
        filename = (
            f"attachments_{telegram_chat_id}_{label_from}_{label_to}{suffix}.zip"
        )
        if total == 1:
            caption = f"Вложений в архиве: {len(found)}"
        else:
            caption = f"Часть {number} из {total}"
        # Кнопки нужны под самым последним сообщением, иначе окажутся выше текста
        last = number == total and not problems
        sent = await message.answer_document(
            BufferedInputFile(content, filename=filename),
            caption=caption,
            reply_markup=delivered_keyboard() if last else None,
        )
        delivered = sent.message_id

    if problems:
        sent = await message.answer(
            "\n".join(problems), reply_markup=delivered_keyboard()
        )
        delivered = sent.message_id

    await finish_dialog(bot, message.chat.id, state, delivered)
