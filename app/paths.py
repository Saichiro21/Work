"""Пути проекта.

Общие для всех модулей: коллектор складывает вложения в ATTACHMENTS_DIR,
админский бот читает их оттуда же. В БД пути хранятся относительно BASE_DIR.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
ATTACHMENTS_DIR = BASE_DIR / "storage" / "attachments"


def resolve_attachment(file_path):
    """Абсолютный путь к вложению или None, если он ведёт не туда.

    Путь приходит из БД, а туда — из имени файла в Telegram. Проверка одна на
    всех, кто открывает вложения: и архив, и страница выгрузки читают файлы
    только из storage/attachments.
    """
    if not file_path:
        return None

    path = (BASE_DIR / file_path).resolve()
    if ATTACHMENTS_DIR.resolve() not in path.parents:
        return None
    return path
