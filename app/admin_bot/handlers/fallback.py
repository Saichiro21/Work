"""Ответ на ввод, который не подошёл ни одному обработчику.

Роутер подключается последним: он перехватывает любое сообщение,
поэтому перед ним должны стоять и команды, и шаги диалогов.
"""

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.admin_bot.handlers.common import UNKNOWN_COMMAND, open_menu

router = Router()


@router.message()
async def unknown_input(message: Message, state: FSMContext, bot: Bot):
    """Сюда попадает только ввод вне диалога: шаги диалогов ловят свой текст сами.

    Объяснение и список команд приходят одним сообщением — оно же и становится
    меню.
    """
    await open_menu(bot, message, state, UNKNOWN_COMMAND)


@router.callback_query()
async def stale_button(callback: CallbackQuery):
    """Кнопки живут в переписке вечно, а диалог к ним — только до конца выбора."""
    await callback.answer(
        "Кнопка больше не активна. Откройте меню командой /start",
        show_alert=True,
    )
