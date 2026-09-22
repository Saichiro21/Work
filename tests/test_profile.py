"""Витрина бота в Telegram.

Описание и меню команд бот ставит сам при запуске. Telegram отвечает на них
отказом, если не сошлись ограничения, а происходит это молча в лог, — поэтому
рамки проверяем тут.
"""

import asyncio
import re

from aiogram.exceptions import TelegramAPIError

from app.admin_bot.bot import publish_profile
from app.admin_bot.handlers.common import (
    BOT_DESCRIPTION,
    BOT_SHORT_DESCRIPTION,
    COMMANDS,
    COMMANDS_LIST,
    MENU_COMMANDS,
    START_MESSAGE,
    UNKNOWN_COMMAND,
)

# Ограничения Bot API на setMyDescription, setMyShortDescription и setMyCommands
DESCRIPTION_LIMIT = 512
SHORT_DESCRIPTION_LIMIT = 120
COMMAND_NAME = re.compile(r"^[a-z0-9_]{1,32}$")
COMMAND_DESCRIPTION_LIMIT = 256


class FakeBot:
    """Запоминает, что бот рассказал о себе, вместо обращения к Telegram."""

    def __init__(self):
        self.calls = []

    async def set_my_short_description(self, short_description):
        self.calls.append(("short", short_description))

    async def set_my_description(self, description):
        self.calls.append(("description", description))

    async def set_my_commands(self, commands):
        self.calls.append(
            ("commands", [(command.command, command.description) for command in commands])
        )


def test_описание_влезает_в_экран_со_стартом():
    assert 0 < len(BOT_DESCRIPTION) <= DESCRIPTION_LIMIT
    assert 0 < len(BOT_SHORT_DESCRIPTION) <= SHORT_DESCRIPTION_LIMIT


def test_описание_перечисляет_те_же_команды():
    """Иначе на экране со «Стартом» обещано одно, а в приветствии другое."""
    assert COMMANDS_LIST in BOT_DESCRIPTION
    assert COMMANDS_LIST in START_MESSAGE
    assert COMMANDS_LIST in UNKNOWN_COMMAND


def test_команды_телеграм_примет():
    for name, about in MENU_COMMANDS:
        assert COMMAND_NAME.match(name), name
        assert 0 < len(about) <= COMMAND_DESCRIPTION_LIMIT


def test_в_меню_команд_есть_выход_в_главное_окно():
    """Из любого места диалога человек должен уметь вернуться к списку команд."""
    assert MENU_COMMANDS[0][0] == "start"
    assert set(COMMANDS) <= set(MENU_COMMANDS)


def test_витрина_уходит_в_телеграм_целиком():
    bot = FakeBot()

    asyncio.run(publish_profile(bot))

    assert [call[0] for call in bot.calls] == ["short", "description", "commands"]
    assert [name for name, _ in bot.calls[-1][1]] == ["start", "export", "files"]


def test_в_меню_команд_подписи_без_точки():
    """Там подпись в одну строку, точка на её конце выглядит опечаткой."""
    bot = FakeBot()

    asyncio.run(publish_profile(bot))

    for _, about in bot.calls[-1][1]:
        assert not about.endswith("."), about


def test_отказ_телеграма_не_валит_запуск():
    """Описание — витрина: без неё бот всё равно собирает и выгружает."""
    bot = FakeBot()

    async def refuse(**kwargs):
        raise TelegramAPIError(method=None, message="Too Many Requests")

    bot.set_my_description = refuse

    asyncio.run(publish_profile(bot))

    assert [call[0] for call in bot.calls] == ["short"]
