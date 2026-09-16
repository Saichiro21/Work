"""Ответ на ввод, который не подошёл ни одному обработчику.

Роутер подключается последним: он перехватывает любое сообщение,
поэтому перед ним должны стоять и команды, и шаги диалогов.
"""

from aiogram import Router
from aiogram.types import CallbackQuery, Message

router = Router()

HINT = (
    "Не понял. Доступные команды:\n"
    "/export — JSON с сообщениями чата за период\n"
    "/files — архив с вложениями этих сообщений"
)


@router.message()
async def unknown_input(message: Message):
    await message.answer(HINT)


@router.callback_query()
async def stale_button(callback: CallbackQuery):
    """Кнопки живут в переписке вечно, а диалог к ним — только до конца выбора."""
    await callback.answer(
        "Кнопка больше не активна. Начните заново: /export или /files",
        show_alert=True,
    )
