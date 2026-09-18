"""Перевод сообщений и служебных событий в JSON выгрузки.

Выгрузку читают люди, а не программы, поэтому ключи и значения русские, а
пустые места подписаны словами: null и true/false читателю ни о чём не говорят.
"""

from app.admin_bot.handlers.common import (
    file_type_label,
    format_datetime,
    format_duration,
    format_size,
    sender_name,
)
from app.db.models import ChatEventType, ForwardOriginType

# Текст родительского сообщения в ответе даём отрывком: целиком он и так есть в выгрузке
REPLY_PREVIEW_LIMIT = 120

# «Нет» на месте текста можно принять за само сообщение, поэтому пометка заметная
NO_TEXT = "— без текста —"
UNKNOWN_SENDER = "не определён"
NOT_A_REPLY = "не ответ на сообщение"
NOT_FORWARDED = "не пересылалось"
# Пустой путь значит, что файл скачать не удалось. Молчать об этом нельзя:
# читатель решил бы, что вложения не было вовсе. Причина — в логах коллектора.
FILE_NOT_SAVED = "файл не сохранён, в архив вложений не попадёт"

FORWARD_ORIGIN_LABELS = {
    ForwardOriginType.USER: "пользователь",
    ForwardOriginType.HIDDEN_USER: "скрытый пользователь",
    ForwardOriginType.CHAT: "чат",
    ForwardOriginType.CHANNEL: "канал",
}

EVENT_LABELS = {
    ChatEventType.TITLE_CHANGED: "чат переименовали",
    ChatEventType.PHOTO_CHANGED: "сменили фото чата",
    ChatEventType.PHOTO_DELETED: "удалили фото чата",
    ChatEventType.MESSAGE_PINNED: "закрепили сообщение",
    ChatEventType.CHAT_CREATED: "чат создан",
    ChatEventType.MIGRATED_TO: "группа стала супергруппой",
    ChatEventType.MIGRATED_FROM: "чат перенесён из группы",
    ChatEventType.AUTO_DELETE_CHANGED: "изменили таймер автоудаления",
}


def _yes_no(value):
    return "да" if value else "нет"


def _reply_info(message, by_telegram_id):
    """Описание родительского сообщения. Его может не быть в выборке."""
    if message.reply_to_message_id is None:
        return NOT_A_REPLY

    info = {"telegram_message_id": message.reply_to_message_id}
    parent = by_telegram_id.get(message.reply_to_message_id)
    if parent is None:
        info["примечание"] = "это сообщение не попало в выгрузку"
        return info

    preview = parent.text or ""
    if len(preview) > REPLY_PREVIEW_LIMIT:
        preview = preview[:REPLY_PREVIEW_LIMIT] + "…"

    info["отправитель"] = sender_name(parent.user) or UNKNOWN_SENDER
    info["текст"] = preview or NO_TEXT
    info["дата отправки"] = format_datetime(parent.sent_at)
    return info


def _forward_info(message):
    """Откуда пришла пересылка. В «отправителе» стоит тот, кто переслал."""
    if message.forward_origin_type is None:
        return NOT_FORWARDED

    kind = message.forward_origin_type
    return {
        "тип источника": FORWARD_ORIGIN_LABELS.get(kind, kind),
        "источник": message.forward_from_name or UNKNOWN_SENDER,
        # Скрытый пользователь свой id не отдаёт — показать нечего
        "id источника": message.forward_from_id or "скрыт",
        "дата оригинала": format_datetime(message.forward_origin_date) or "неизвестна",
        "автопересылка из канала": _yes_no(message.is_automatic_forward),
    }


def _event_label(event):
    """Вход и выход читаются по-разному: человек сделал это сам или это сделали с ним."""
    same_person = event.actor_user_id == event.target_user_id

    if event.event_type == ChatEventType.MEMBER_JOINED:
        return "участник вошёл сам" if same_person else "участника добавили"

    if event.event_type == ChatEventType.MEMBER_LEFT:
        return "участник вышел сам" if same_person else "участника исключили"

    return EVENT_LABELS.get(event.event_type, event.event_type)


def serialize_event(event):
    return {
        "тип": "служебное событие",
        "событие": _event_label(event),
        "дата": format_datetime(event.happened_at),
        "кто": sender_name(event.actor) or UNKNOWN_SENDER,
        "с кем": sender_name(event.target) or "никого",
        "подробности": event.details or "нет",
        "закреплённое сообщение": event.target_message_id or "нет",
    }


def _serialize_attachment(attachment):
    """Длительность добавляем только там, где она бывает: у фото её нет вовсе."""
    payload = {
        "тип": file_type_label(attachment.file_type),
        "файл": attachment.file_path or FILE_NOT_SAVED,
        # У фото, голосовых и кружков Telegram имени файла не присылает
        "имя файла": attachment.original_filename or "имени нет",
        "размер": format_size(attachment.file_size),
    }

    duration = format_duration(attachment.duration_seconds)
    if duration is not None:
        payload["длительность"] = duration

    return payload


def serialize_message(message, by_telegram_id):
    versions = [
        {
            "текст": version.text or NO_TEXT,
            "заменено": format_datetime(version.replaced_at),
        }
        for version in message.versions
    ]
    attachments = [_serialize_attachment(item) for item in message.attachments]

    return {
        "тип": "сообщение",
        "отправитель": sender_name(message.user) or UNKNOWN_SENDER,
        "текст": message.text or NO_TEXT,
        "дата отправки": format_datetime(message.sent_at),
        "была ли отредактирована": _yes_no(message.edited_at),
        "ответ на": _reply_info(message, by_telegram_id),
        "переслано из": _forward_info(message),
        "история правок": versions or "правок не было",
        "список вложений": attachments or "вложений нет",
    }


def timeline(messages, events=()):
    """Сообщения и служебные события одной хронологией, как в самом Telegram."""
    by_telegram_id = {item.telegram_message_id: item for item in messages}

    records = [
        (item.sent_at, item.telegram_message_id, serialize_message(item, by_telegram_id))
        for item in messages
    ]
    records += [
        (event.happened_at, event.telegram_message_id, serialize_event(event))
        for event in events
    ]
    records.sort(key=lambda record: (record[0], record[1]))
    return [record[2] for record in records]
