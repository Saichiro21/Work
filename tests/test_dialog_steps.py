"""Учёт экранов диалога.

Неудачные попытки ответить на вопрос не должны обрастать вереницей одинаковых
окон, а «Назад» обязан убрать за собой всё, что человек успел наговорить.
"""

import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import DeleteMessage, EditMessageText

from app.admin_bot.handlers import common
from app.admin_bot.handlers.common import (
    BAD_CHAT_ID,
    CHAT_QUESTION,
    COMMANDS_LIST,
    IDS_SCREEN,
    LIST_SCREEN,
    PERIOD_PROMPT,
    START_MESSAGE,
    STEPS_KEY,
    UNKNOWN_COMMAND,
    forget_menu,
    open_menu,
    reject_bad_chat_id,
    reject_period,
    show_screen,
)

CHAT_ID = 777

# Экраны выбора чата переключают состояние диалога, а какое именно — тут не важно
STATES = SimpleNamespace(waiting_chat_id=None)


class FakeBot:
    """Запоминает, что бот сделал с перепиской, вместо обращения к Telegram."""

    def __init__(self):
        self.next_message_id = 1
        self.sent = []
        self.deleted = []
        self.edited = []
        self.buttons_dropped = []
        self.texts = {}

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        message_id = self.next_message_id
        self.next_message_id += 1
        self.sent.append((message_id, text))
        self.texts[message_id] = text
        return SimpleNamespace(message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)

    async def edit_message_text(
        self, text, chat_id=None, message_id=None, reply_markup=None, parse_mode=None
    ):
        self.edited.append((message_id, text))
        # Правку на тот же текст Telegram считает ошибкой, а не пустой операцией
        if self.texts.get(message_id) == text:
            raise TelegramBadRequest(
                method=EditMessageText(
                    chat_id=chat_id, message_id=message_id, text=text
                ),
                message="Bad Request: message is not modified",
            )
        self.texts[message_id] = text

    async def edit_message_reply_markup(
        self, chat_id=None, message_id=None, reply_markup=None
    ):
        self.buttons_dropped.append(message_id)


@pytest.fixture
def bot():
    return FakeBot()


@pytest.fixture
def state():
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=CHAT_ID, user_id=1),
    )


def show(bot, state, text, **kwargs):
    asyncio.run(show_screen(bot, CHAT_ID, state, text, **kwargs))


def steps(state):
    data = asyncio.run(state.get_data())
    return data[STEPS_KEY]


def test_первый_экран_диалога_просто_присылается(bot, state):
    show(bot, state, "Из какого чата взять данные?")

    assert steps(state) == [[1, None]]
    assert bot.deleted == []


def test_переход_по_кнопке_правит_экран_на_месте(bot, state):
    show(bot, state, "Из какого чата взять данные?")
    show(bot, state, "Выберите чат из списка")

    # Переписка от нажатий не растёт: диалог живёт в одном сообщении
    assert steps(state) == [[1, None]]
    assert bot.edited == [(1, "Выберите чат из списка")]


def test_ответ_текстом_открывает_экран_под_вводом(bot, state):
    show(bot, state, "Из какого чата взять данные?")
    show(bot, state, "За какой период?", answers=100)

    assert steps(state) == [[1, None], [2, 100]]
    assert bot.deleted == []
    # У окна выше кнопки снимаются: вести дальше должно только нижнее
    assert bot.buttons_dropped == [1]


def test_каждый_ввод_получает_свой_экран(bot, state):
    """Прошлые попытки и ответы на них остаются в переписке как история."""
    show(bot, state, "За какой период?")
    show(bot, state, "Неверный формат.", answers=100)
    show(bot, state, "Дата начала позже окончания.", answers=101)

    assert steps(state) == [[1, None], [2, 100], [3, 101]]
    assert bot.deleted == []
    # Кнопки живут только в нижнем окне, выше они сняты
    assert bot.buttons_dropped == [1, 2]


def test_назад_убирает_экран_вместе_с_вводом(bot, state):
    show(bot, state, "Из какого чата взять данные?")
    show(bot, state, "За какой период?", answers=100)

    show(bot, state, "Из какого чата взять данные?", back=True)

    assert steps(state) == [[1, None]]
    # Ушли и экран, и ввод, на который он отвечал; прошлый экран ожил
    assert bot.deleted == [2, 100]
    assert bot.edited[-1] == (1, "Из какого чата взять данные?")


def test_назад_с_первого_экрана_никого_не_удаляет(bot, state):
    show(bot, state, "Из какого чата взять данные?")

    show(bot, state, "Из какого чата взять данные?", back=True)

    assert steps(state) == [[1, None]]
    assert bot.deleted == []


def reject(bot, state):
    asyncio.run(reject_bad_chat_id(bot, CHAT_ID, state, STATES, answers=100))


def test_где_чат_выбирают_кнопкой_случайный_текст_убирается(bot, state):
    """Номер там никто не просил: сказать о таком вводе нечего, кроме уборки."""
    show(bot, state, CHAT_QUESTION)
    asyncio.run(state.update_data(chat_asks_id=False))

    reject(bot, state)

    assert bot.deleted == [100]
    # Вопрос с кнопками остался нетронутым: ни нового окна, ни правки старого
    assert bot.sent == [(1, CHAT_QUESTION)]
    assert bot.edited == []
    assert steps(state) == [[1, None]]


def test_упёршееся_в_лимит_удаление_не_ломает_шаг(bot, state, monkeypatch):
    """Быстрый ввод подряд — много удалений, и Telegram может ответить отказом."""

    async def flood(chat_id, message_id):
        raise TelegramRetryAfter(
            method=DeleteMessage(chat_id=chat_id, message_id=message_id),
            message="Too Many Requests",
            retry_after=5,
        )

    monkeypatch.setattr(bot, "delete_message", flood)
    show(bot, state, CHAT_QUESTION)

    reject(bot, state)

    # Мусор остался в переписке, но обработчик дошёл до конца и диалог цел
    assert steps(state) == [[1, None]]


def test_замечание_встаёт_в_открытый_экран_а_не_копирует_его(bot, state, monkeypatch):
    """Кнопок хватило не на все чаты — на списке как раз просят прислать номер."""
    monkeypatch.setattr(
        common, "chat_list_view", lambda header: (f"{header}\n• чат", None, True)
    )
    show(bot, state, CHAT_QUESTION)
    asyncio.run(state.update_data(chat_screen=LIST_SCREEN, chat_asks_id=True))

    reject(bot, state)

    assert bot.deleted == [100]
    # Замечание встало на место приглашения, а не абзацем над ним
    assert bot.edited[-1] == (1, f"{BAD_CHAT_ID}\n• чат")
    # Второй копии списка в переписке не появилось
    assert len(bot.sent) == 1
    assert steps(state) == [[1, None]]


def test_то_же_замечание_второй_раз_не_плодит_экран(bot, state, monkeypatch):
    """Экран уже с замечанием: Telegram на такую правку отвечает отказом."""
    monkeypatch.setattr(common, "chat_ids_view", lambda header: (header, None))
    show(bot, state, CHAT_QUESTION)
    asyncio.run(state.update_data(chat_screen=IDS_SCREEN, chat_asks_id=True))

    reject(bot, state)
    asyncio.run(reject_bad_chat_id(bot, CHAT_ID, state, STATES, answers=101))

    assert bot.deleted == [100, 101]
    assert len(bot.sent) == 1
    assert steps(state) == [[1, None]]


def test_замечание_о_датах_приходит_под_присланной_строкой(bot, state):
    """Даты человек набирает сам: свои попытки и ответы на них остаются на виду."""
    show(bot, state, PERIOD_PROMPT)

    asyncio.run(reject_period(bot, CHAT_ID, state, "Неверный формат.", answers=100))
    asyncio.run(reject_period(bot, CHAT_ID, state, "Начало позже конца.", answers=101))

    assert bot.deleted == []
    assert steps(state) == [[1, None], [2, 100], [3, 101]]
    assert bot.sent[-1] == (3, "Начало позже конца.")


def test_номер_просят_только_когда_кнопок_на_все_чаты_не_хватило(monkeypatch):
    chats = [(-100 - number, f"чат {number}", 1, None) for number in range(25)]

    monkeypatch.setattr(common, "chats_overview", lambda: chats[:3])
    _, _, asks_id = common.chat_list_view()
    assert asks_id is False

    monkeypatch.setattr(common, "chats_overview", lambda: chats)
    text, _, asks_id = common.chat_list_view()
    assert asks_id is True
    # И сказано об этом в самом экране, иначе просьба была бы невидимой
    assert "telegram_chat_id" in text


class FakeMessage:
    """Присланное пользователем сообщение: боту от него нужны чат и ответ."""

    def __init__(self, bot, message_id):
        self._bot = bot
        self.message_id = message_id
        self.chat = SimpleNamespace(id=CHAT_ID)

    async def answer(self, text, reply_markup=None, parse_mode=None):
        return await self._bot.send_message(CHAT_ID, text, reply_markup=reply_markup)


def test_повторный_start_не_плодит_меню(bot, state):
    """Пока меню на виду, прежнее убирается вместе с вызвавшими его командами."""
    asyncio.run(state.update_data(menu_message_id=5, commands=[7]))

    asyncio.run(open_menu(bot, FakeMessage(bot, 100), state))

    assert bot.deleted == [5, 7]
    assert bot.sent[-1][1] == START_MESSAGE


def test_неизвестная_команда_не_трогает_то_что_выше(bot, state):
    """Непонятный ввод бот не присылал, вырезать его из переписки он не может."""
    asyncio.run(state.update_data(menu_message_id=5, commands=[7]))

    asyncio.run(forget_menu(state))
    asyncio.run(open_menu(bot, FakeMessage(bot, 100), state, UNKNOWN_COMMAND))

    assert bot.deleted == []
    # Объяснение и команды — одно сообщение, а не два
    assert bot.sent == [(1, UNKNOWN_COMMAND)]


def test_команды_перечислены_в_одном_месте():
    """Своя копия списка команд уже однажды отстала и потеряла /search."""
    assert COMMANDS_LIST in START_MESSAGE
    assert COMMANDS_LIST in UNKNOWN_COMMAND


def test_если_чаты_исчезли_показываем_первый_экран(bot, state, monkeypatch):
    """Список мог опустеть, пока человек печатал, — показывать его уже нечем."""
    monkeypatch.setattr(common, "chat_list_view", lambda header: (None, None, False))
    show(bot, state, CHAT_QUESTION)
    asyncio.run(state.update_data(chat_screen=LIST_SCREEN, chat_asks_id=True))

    reject(bot, state)

    # Открыт был список, поэтому текст про формат номера, а экран — первый
    assert bot.edited[-1] == (1, f"{BAD_CHAT_ID}\n\n{CHAT_QUESTION}")
