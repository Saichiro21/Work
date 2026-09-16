"""Пути проекта.

Общие для всех модулей: коллектор складывает вложения в ATTACHMENTS_DIR,
админский бот читает их оттуда же. В БД пути хранятся относительно BASE_DIR.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
ATTACHMENTS_DIR = BASE_DIR / "storage" / "attachments"
