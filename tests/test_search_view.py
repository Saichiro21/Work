"""Как выглядит выдача поиска: отрывки, склонения, имя файла."""

from datetime import datetime

import pytest

from app.admin_bot.handlers.search import (
    PREVIEW_COUNT,
    _filename,
    _messages_word,
    _preview,
    _results_text,
)
from app.db.models import Message, TelegramUser

IVAN = TelegramUser(telegram_user_id=1, first_name="Иван", username="ivan")


def message(text, day=1):
    return Message(
        telegram_message_id=day,
        user=IVAN,
        text=text,
        sent_at=datetime(2026, 9, day, 9, 0),
    )


def test_найденное_слово_выделено():
    assert "<b>отчёт</b>" in _preview("Держи отчёт за август", "отчёт")


def test_регистр_в_отрывке_сохраняется():
    assert "<b>Отчёт</b>" in _preview("Отчёт готов", "отчёт")


def test_разметка_в_тексте_экранируется():
    preview = _preview("текст с <b>тегом</b> внутри", "тегом")

    assert "&lt;b&gt;" in preview
    assert preview.count("<b>") == 1


def test_длинный_текст_обрезается_с_двух_сторон():
    text = "а" * 200 + " отчёт " + "я" * 400

    preview = _preview(text, "отчёт")

    assert preview.startswith("…")
    assert preview.endswith("…")


def test_переносы_строк_схлопываются():
    assert "\n" not in _preview("первая строка\nвторая отчёт", "отчёт")


@pytest.mark.parametrize(
    "count, word",
    [(1, "сообщение"), (2, "сообщения"), (4, "сообщения"), (5, "сообщений"),
     (11, "сообщений"), (21, "сообщение"), (112, "сообщений")],
)
def test_склонение_числа_сообщений(count, word):
    assert _messages_word(count) == word


def test_заголовок_с_периодом():
    text = _results_text([message("отчёт")], "отчёт", "01.09.2026 - 10.09.2026")

    assert text.startswith("Нашёл 1 сообщение со словом «отчёт» за 01.09.2026")


def test_показываются_только_последние_совпадения():
    found = [message("отчёт", day=day) for day in range(1, PREVIEW_COUNT + 5)]

    text = _results_text(found, "отчёт", None)

    assert f"Нашёл {len(found)} сообщений" in text
    assert f"Показываю последние {PREVIEW_COUNT}" in text
    # Самые старые в список не попали, самое свежее — попало
    assert "01.09.2026" not in text
    assert f"{len(found):02d}.09.2026" in text


def test_время_показывается_без_секунд():
    text = _results_text([message("отчёт")], "отчёт", None)

    assert "01.09.2026 12:00 · Иван (@ivan)" in text


def test_имя_файла_без_пробелов_и_знаков():
    assert _filename(-100, "отчёт за август!") == "search_-100_отчёт_за_август.json"


def test_имя_файла_не_разрастается():
    assert len(_filename(-100, "слово " * 50)) < 60
