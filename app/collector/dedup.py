"""Дедупликация вложений: одинаковый файл хранится на диске один раз.

Одни и те же документы и картинки пересылают в чаты снова и снова, и без этого
каждая пересылка ложилась бы на диск новой копией. Сравниваем по SHA-256
содержимого, а не по file_unique_id Telegram: хеш узнаёт и файл, который
загрузили заново, а не переслали.

Запуск `python -m app.collector.dedup` проставляет хеши вложениям, сохранённым
до появления дедупликации, и склеивает среди них одинаковые.
"""

import hashlib
import logging

from app.db import crud
from app.db.db import SessionLocal
from app.paths import resolve_attachment

logger = logging.getLogger(__name__)


def file_sha256(path):
    with open(path, "rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _present_copy(db, sha256, exclude):
    """Путь к уже лежащему на диске файлу с тем же содержимым или None.

    Запись о файле ещё не значит, что он на месте: его могли стереть руками,
    и тогда ссылаться на него нельзя — новая копия нужнее.
    """
    for file_path in crud.get_attachment_paths_by_sha256(db, sha256):
        if file_path == exclude:
            continue
        path = resolve_attachment(file_path)
        if path is not None and path.is_file():
            return file_path
    return None


def _remove(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Не удалось удалить копию вложения %s", path)
        return False
    return True


def share_existing(db, file_path):
    """Возвращает (путь для записи, хеш) только что скачанного файла.

    Если такой файл уже есть, свежая копия удаляется и возвращается путь
    к прежней. Хеш пустой, когда файла нет или его не удалось прочитать.
    """
    path = resolve_attachment(file_path)
    if path is None or not path.is_file():
        return file_path, None

    try:
        sha256 = file_sha256(path)
    except OSError:
        logger.exception("Не удалось посчитать хеш вложения %s", path)
        return file_path, None

    shared = _present_copy(db, sha256, exclude=file_path)
    if shared is None:
        return file_path, sha256

    if not _remove(path):
        # Лишняя копия на диске лучше записи, которая ведёт не туда
        return file_path, sha256
    logger.info("Вложение %s совпало с %s, копия удалена", file_path, shared)
    return shared, sha256


def backfill(db):
    """Хеширует старые вложения и склеивает одинаковые.

    Возвращает (сколько вложений получили хеш, сколько копий удалено с диска).
    Запись переводится на общий файл раньше, чем стирается её копия: оборвись
    работа посередине, ни одна запись не останется без файла.
    """
    hashed = removed = 0
    for attachment in crud.get_unhashed_attachments(db):
        own = attachment.file_path
        path = resolve_attachment(own)
        if path is None or not path.is_file():
            continue

        try:
            sha256 = file_sha256(path)
        except OSError:
            logger.exception("Не удалось посчитать хеш вложения %s", path)
            continue

        shared = _present_copy(db, sha256, exclude=own)
        attachment.sha256 = sha256
        if shared is not None:
            attachment.file_path = shared
        db.commit()
        hashed += 1

        if shared is not None and not crud.is_attachment_path_used(db, own):
            removed += _remove(path)

    return hashed, removed


def main():
    logging.basicConfig(level=logging.INFO)
    db = SessionLocal()
    try:
        hashed, removed = backfill(db)
    finally:
        db.close()
    print(f"Хеш посчитан у вложений: {hashed}, удалено одинаковых копий: {removed}")


if __name__ == "__main__":
    main()
