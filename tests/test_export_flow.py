"""Шаги /export от присланного периода до отправленного файла.

Тесты на отдельные функции такое не ловят: сломаться может сама склейка шагов —
например, обработчик возьмёт из разбора периода не то значение. Поэтому здесь
обработчики вызываются целиком, а вместо Telegram и базы подставлены заглушки.
"""

import asyncio
from types import SimpleNamespace

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.admin_bot.handlers import export
from app.admin_bot.handlers.export import (
    ExportStates,
    deliver_html,
    deliver_json,
    receive_period,
)
from app.db.models import Message, TelegramUser

CHAT_ID = 555
CHAT_PK = 7
TELEGRAM_CHAT_ID = -100123

IVAN = TelegramUser(telegram_user_id=1, first_name="Иван", username="ivan")


class FakeBot:
    def __init__(self):
        self.next_message_id = 1
        self.sent = []
        self.documents = []

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.sent.append(text)
        return self._message()

    async def send_document(self, chat_id, document, caption=None, reply_markup=None):
        self.documents.append((document.filename, document.data, caption))
        return self._message()

    async def edit_message_text(self, text, chat_id, message_id, **kwargs):
        self.sent.append(text)
        return self._message()

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        return self._message()

    async def delete_message(self, chat_id, message_id):
        return True

    def _message(self):
        message_id = self.next_message_id
        self.next_message_id += 1
        return SimpleNamespace(message_id=message_id)


class FakeMessage:
    """Присланный человеком текст: обработчику нужны только эти три вещи."""

    def __init__(self, text, message_id=100):
        self.text = text
        self.message_id = message_id
        self.chat = SimpleNamespace(id=CHAT_ID)


class FakeCallback:
    def __init__(self):
        self.answered = False
        self.from_user = SimpleNamespace(id=CHAT_ID)

    async def answer(self, *args, **kwargs):
        self.answered = True


@pytest.fixture
def bot():
    return FakeBot()


@pytest.fixture
def state():
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=CHAT_ID, user_id=CHAT_ID)
    return FSMContext(storage=storage, key=key)


@pytest.fixture
def chosen(state):
    """Диалог, в котором чат уже выбран и бот ждёт период."""
    asyncio.run(state.set_state(ExportStates.waiting_period))
    asyncio.run(
        state.update_data(
            chat_pk=CHAT_PK,
            telegram_chat_id=TELEGRAM_CHAT_ID,
            chat_title="Отдел разработки",
            steps=[[1, None]],
            commands=[],
        )
    )
    return state


@pytest.fixture
def stored(monkeypatch):
    """В базе одно сообщение за 16.09.2026 и никаких служебных событий."""
    from datetime import datetime

    item = Message(
        telegram_message_id=11,
        user=IVAN,
        text="Отчёт готов",
        sent_at=datetime(2026, 9, 16, 9, 0),
    )
    item.attachments = []
    item.versions = []

    monkeypatch.setattr(export, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(export, "get_messages_by_period", lambda *args: [item])
    monkeypatch.setattr(export, "get_chat_events_by_period", lambda *args: [])
    return item


def test_после_периода_спрашивают_формат(bot, chosen, stored):
    asyncio.run(receive_period(FakeMessage("16.09.2026 - 18.09.2026"), chosen, bot))

    assert "В каком виде выгрузить?" in bot.sent[-1]
    # Период в вопросе стоит словами: по нему видно, что бот понял правильно
    assert "16.09.2026 — 18.09.2026" in bot.sent[-1]
    assert "сообщений: 1" in bot.sent[-1]
    assert asyncio.run(chosen.get_state()) == ExportStates.waiting_format


def test_неверный_период_до_формата_не_доходит(bot, chosen, stored):
    asyncio.run(receive_period(FakeMessage("вчера"), chosen, bot))

    assert "Неверный формат" in bot.sent[-1]
    assert asyncio.run(chosen.get_state()) == ExportStates.waiting_period


def test_пустой_период_отвечает_без_файла(bot, chosen, stored, monkeypatch):
    monkeypatch.setattr(export, "get_messages_by_period", lambda *args: [])

    asyncio.run(receive_period(FakeMessage("16.09.2026"), chosen, bot))

    assert "не найдено" in bot.sent[-1]
    assert bot.documents == []


def test_кнопка_html_присылает_страницу(bot, chosen, stored):
    asyncio.run(receive_period(FakeMessage("16.09.2026"), chosen, bot))
    asyncio.run(deliver_html(FakeCallback(), chosen, bot))

    filename, content, caption = bot.documents[-1]
    page = content.decode("utf-8")

    assert filename == "export_-100123_16-09-2026.html"
    assert page.startswith("<!DOCTYPE html>")
    assert "Отчёт готов" in page
    assert "браузере" in caption
    # Кнопки под файлом ведут к периоду, туда же возвращается и диалог
    assert asyncio.run(chosen.get_state()) == ExportStates.waiting_period


def test_кнопка_json_присылает_данные(bot, chosen, stored):
    import json

    asyncio.run(receive_period(FakeMessage("16.09.2026"), chosen, bot))
    asyncio.run(deliver_json(FakeCallback(), chosen, bot))

    filename, content, _ = bot.documents[-1]
    payload = json.loads(content.decode("utf-8"))

    assert filename == "export_-100123_16-09-2026.json"
    assert payload[0]["текст"] == "Отчёт готов"


def test_неподъёмную_страницу_бот_не_шлёт(bot, chosen, stored, monkeypatch):
    """Telegram отказал бы сам, а человеку нужен понятный ответ, что делать."""
    monkeypatch.setattr(export, "SEND_LIMIT", 10)

    asyncio.run(receive_period(FakeMessage("16.09.2026"), chosen, bot))
    asyncio.run(deliver_html(FakeCallback(), chosen, bot))

    assert bot.documents == []
    assert "тяжелее 45 МБ" in bot.sent[-1]
