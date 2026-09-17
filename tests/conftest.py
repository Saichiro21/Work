"""Общие фикстуры тестов.

Настоящую базу не трогаем: каждый тест получает пустую SQLite в памяти.
Пояс показа фиксируем, иначе разбор периода зависел бы от машины.
"""

import os

os.environ["DISPLAY_TIMEZONE"] = "Europe/Moscow"

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.admin_bot.handlers.common import display_timezone  # noqa: E402
from app.db.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def fixed_timezone():
    display_timezone.cache_clear()
    yield
    display_timezone.cache_clear()


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
