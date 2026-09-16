from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.db.crud import is_admin
from app.db.db import SessionLocal


class AdminMiddleware(BaseMiddleware):
    """Пропускает дальше только администраторов из таблицы admins.

    Подключается и к сообщениям, и к нажатиям кнопок: кнопку можно переслать
    вместе с сообщением, поэтому проверять права только на тексте недостаточно.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        db = SessionLocal()
        try:
            allowed = is_admin(db, user.id)
        finally:
            db.close()

        if allowed:
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer("У вас нет доступа", show_alert=True)
        elif isinstance(event, Message):
            await event.answer("У вас нет доступа")
        return None
