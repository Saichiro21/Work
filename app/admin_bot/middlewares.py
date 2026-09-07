from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from app.db.crud import is_admin
from app.db.db import SessionLocal


class AdminMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.from_user is None:
            return await handler(event, data)

        db = SessionLocal()
        try:
            allowed = is_admin(db, event.from_user.id)
        finally:
            db.close()

        if not allowed:
            await event.answer("У вас нет доступа")
            return

        return await handler(event, data)
