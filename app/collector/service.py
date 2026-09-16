"""Запись служебных событий чата.

Telegram присылает их обычными сообщениями: текста нет, вместо него заполнено
одно специальное поле. Для аудита состав участников и момент входа важны
не меньше самой переписки, поэтому такие сообщения не выбрасываются.
"""

import logging

from app.collector.saver import message_text, naive_utc, save_author
from app.db import crud
from app.db.db import SessionLocal
from app.db.models import ChatEventType

logger = logging.getLogger(__name__)

# Закреплённое сообщение целиком уже есть в базе, в событии хватит отрывка
PINNED_PREVIEW_LIMIT = 120


def _event(event_type, target_user=None, target_message_id=None, details=None):
    return {
        "event_type": event_type,
        "target_user": target_user,
        "target_message_id": target_message_id,
        "details": details,
    }


def detect_events(message):
    """Список событий сообщения. Пустой, если сообщение обычное.

    Одним сообщением Telegram добавляет сразу несколько участников,
    поэтому событий может быть больше одного.
    """
    if message.new_chat_members:
        return [
            _event(ChatEventType.MEMBER_JOINED, target_user=user)
            for user in message.new_chat_members
        ]

    if message.left_chat_member is not None:
        return [_event(ChatEventType.MEMBER_LEFT, target_user=message.left_chat_member)]

    if message.new_chat_title:
        return [_event(ChatEventType.TITLE_CHANGED, details=message.new_chat_title)]

    if message.new_chat_photo:
        return [_event(ChatEventType.PHOTO_CHANGED)]

    if message.delete_chat_photo:
        return [_event(ChatEventType.PHOTO_DELETED)]

    if message.pinned_message is not None:
        pinned = message.pinned_message
        preview = message_text(pinned) or ""
        if len(preview) > PINNED_PREVIEW_LIMIT:
            preview = preview[:PINNED_PREVIEW_LIMIT] + "…"
        return [
            _event(
                ChatEventType.MESSAGE_PINNED,
                target_message_id=pinned.message_id,
                details=preview or None,
            )
        ]

    if message.group_chat_created or message.supergroup_chat_created:
        return [_event(ChatEventType.CHAT_CREATED)]

    # Группа стала супергруппой: id чата меняется, старый больше не встретится
    if message.migrate_to_chat_id is not None:
        return [
            _event(ChatEventType.MIGRATED_TO, details=str(message.migrate_to_chat_id))
        ]

    if message.migrate_from_chat_id is not None:
        return [
            _event(
                ChatEventType.MIGRATED_FROM, details=str(message.migrate_from_chat_id)
            )
        ]

    timer = message.message_auto_delete_timer_changed
    if timer is not None:
        return [
            _event(
                ChatEventType.AUTO_DELETE_CHANGED,
                details=f"{timer.message_auto_delete_time} секунд",
            )
        ]

    return []


def save_service_events(message):
    """Пишет служебные события сообщения. Возвращает True, если оно служебное."""
    events = detect_events(message)
    if not events:
        return False

    db = SessionLocal()
    try:
        chat = crud.get_or_create_chat(db, message.chat.id, message.chat.title)
        # Кто совершил действие: добавивший участника, переименовавший чат.
        # У входа по ссылке-приглашению это сам вошедший
        actor = save_author(db, message)
        happened_at = naive_utc(message.date)

        for event in events:
            target = event["target_user"]
            target_pk = None
            if target is not None:
                stored = crud.get_or_create_user(
                    db,
                    target.id,
                    target.username,
                    target.first_name,
                    target.last_name,
                )
                target_pk = stored.id

            crud.get_or_create_chat_event(
                db,
                chat.id,
                message.message_id,
                event["event_type"],
                happened_at,
                actor_user_id=actor.id if actor is not None else None,
                target_user_id=target_pk,
                target_message_id=event["target_message_id"],
                details=event["details"],
            )

            if event["event_type"] == ChatEventType.TITLE_CHANGED:
                crud.set_chat_title(db, chat.id, event["details"])
            elif event["event_type"] == ChatEventType.MIGRATED_TO:
                logger.warning(
                    "Чат %s стал супергруппой, новый telegram_chat_id — %s. "
                    "Обновите COLLECTOR_CHAT_IDS в .env, иначе сбор по нему прекратится",
                    message.chat.id,
                    event["details"],
                )
    finally:
        db.close()

    return True
