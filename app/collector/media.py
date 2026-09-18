import logging
from pathlib import Path

from app.paths import ATTACHMENTS_DIR, BASE_DIR

logger = logging.getLogger(__name__)

# Bot API не отдаёт боту файлы больше 20 МБ
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


def detect_attachment(message):
    """Возвращает (тип, file_id, имя, размер, длительность) или None без вложения.

    Длительность есть только у того, что проигрывается; у фото и документов
    на её месте None. Стикеры намеренно не обрабатываются: для переписки они не
    несут содержания, поэтому сообщение со стикером целиком пропускается
    в saver.save_message.
    """
    if message.photo:
        # Telegram присылает несколько размеров одного фото, берём самый крупный
        largest = message.photo[-1]
        return "photo", largest.file_id, None, largest.file_size, None
    if message.animation:
        item = message.animation
        return "animation", item.file_id, item.file_name, item.file_size, item.duration
    if message.video:
        item = message.video
        return "video", item.file_id, item.file_name, item.file_size, item.duration
    if message.video_note:
        item = message.video_note
        return "video_note", item.file_id, None, item.file_size, item.duration
    if message.voice:
        item = message.voice
        return "voice", item.file_id, None, item.file_size, item.duration
    if message.audio:
        item = message.audio
        return "audio", item.file_id, item.file_name, item.file_size, item.duration
    if message.document:
        item = message.document
        return "document", item.file_id, item.file_name, item.file_size, None
    return None


async def download_attachment(bot, message, telegram_chat_id):
    """Скачивает вложение. Возвращает данные для create_attachment или None.

    Вернуть None значит «вложения не было». Если вложение есть, но файл получить
    не удалось, путь в ответе пустой: без такой записи выгрузка утверждала бы,
    что вложений у сообщения нет, а для аудита это хуже отсутствия самого файла.
    """
    found = detect_attachment(message)
    if found is None:
        return None

    file_type, file_id, original_filename, file_size, duration = found
    not_saved = file_type, None, original_filename, file_size, duration

    if file_size is not None and file_size > MAX_DOWNLOAD_BYTES:
        logger.warning(
            "Сообщение %s: файл %.1f МБ не скачан, Bot API отдаёт боту максимум 20 МБ",
            message.message_id,
            file_size / (1024 * 1024),
        )
        return not_saved

    try:
        file = await bot.get_file(file_id)
    except Exception:
        logger.exception(
            "Не удалось получить файл сообщения %s", message.message_id
        )
        return not_saved

    if not file.file_path:
        logger.warning("Telegram не вернул путь к файлу сообщения %s", message.message_id)
        return not_saved

    target_dir = ATTACHMENTS_DIR / str(telegram_chat_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    if original_filename:
        name = Path(original_filename).name
    else:
        name = f"{file_type}{Path(file.file_path).suffix}"
    target_path = target_dir / f"{message.message_id}_{name}"

    try:
        await bot.download_file(file.file_path, destination=target_path)
    except Exception:
        logger.exception("Не удалось скачать вложение сообщения %s", message.message_id)
        return not_saved

    # В БД храним путь относительно корня проекта, чтобы выгрузка не зависела от машины
    relative_path = target_path.resolve().relative_to(BASE_DIR)
    return file_type, str(relative_path), original_filename, file_size, duration
