"""Экран со списком команд: /start и возврат к нему из диалогов."""

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.admin_bot.handlers.common import MENU_CALLBACK, open_menu, show_menu

router = Router()


@router.message(Command("start", ignore_case=True))
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    await open_menu(bot, message, state)


@router.callback_query(F.data == MENU_CALLBACK)
async def back_to_menu(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Выход из диалога: окно диалога закрывается, и на виду остаётся меню."""
    await show_menu(bot, callback.from_user.id, state)
    await callback.answer()
