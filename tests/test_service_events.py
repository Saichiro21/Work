"""Распознавание служебных сообщений.

Собираем объекты так же, как их собирает aiogram из ответа Telegram, — из сырого
словаря. Иначе легко проверить не то поле, что приходит на самом деле.
"""

from aiogram.types import Message

from app.collector.service import detect_events
from app.db.models import ChatEventType

CHAT = {"id": -1001, "type": "supergroup", "title": "Отдел"}
IVAN = {"id": 1, "is_bot": False, "first_name": "Иван"}
MARIA = {"id": 2, "is_bot": False, "first_name": "Мария"}


def build(**fields):
    return Message.model_validate(
        {"message_id": 7, "date": 1757000000, "chat": CHAT, "from": IVAN, **fields}
    )


def test_обычное_сообщение_не_событие():
    assert detect_events(build(text="Привет")) == []


def test_вход_нескольких_участников():
    events = detect_events(build(new_chat_members=[MARIA, {**IVAN, "id": 3}]))

    assert [item["event_type"] for item in events] == [
        ChatEventType.MEMBER_JOINED,
        ChatEventType.MEMBER_JOINED,
    ]
    assert [item["target_user"].id for item in events] == [2, 3]


def test_выход_участника():
    events = detect_events(build(left_chat_member=MARIA))

    assert events[0]["event_type"] == ChatEventType.MEMBER_LEFT
    assert events[0]["target_user"].id == 2


def test_переименование_чата():
    events = detect_events(build(new_chat_title="Новое название"))

    assert events[0]["event_type"] == ChatEventType.TITLE_CHANGED
    assert events[0]["details"] == "Новое название"


def test_удаление_фото_чата():
    events = detect_events(build(delete_chat_photo=True))

    assert events[0]["event_type"] == ChatEventType.PHOTO_DELETED


def test_закрепление_сообщения():
    pinned = {
        "message_id": 5,
        "date": 1757000000,
        "chat": CHAT,
        "from": MARIA,
        "text": "Важное объявление",
    }

    events = detect_events(build(pinned_message=pinned))

    assert events[0]["event_type"] == ChatEventType.MESSAGE_PINNED
    assert events[0]["target_message_id"] == 5
    assert events[0]["details"] == "Важное объявление"


def test_закреплённый_текст_обрезается():
    pinned = {
        "message_id": 5,
        "date": 1757000000,
        "chat": CHAT,
        "from": MARIA,
        "text": "а" * 500,
    }

    details = detect_events(build(pinned_message=pinned))[0]["details"]

    assert details.endswith("…")
    assert len(details) < 200


def test_переезд_в_супергруппу():
    events = detect_events(build(migrate_to_chat_id=-1002))

    assert events[0]["event_type"] == ChatEventType.MIGRATED_TO
    assert events[0]["details"] == "-1002"


def test_создание_чата():
    assert (
        detect_events(build(group_chat_created=True))[0]["event_type"]
        == ChatEventType.CHAT_CREATED
    )


def test_таймер_автоудаления():
    events = detect_events(
        build(message_auto_delete_timer_changed={"message_auto_delete_time": 86400})
    )

    assert events[0]["event_type"] == ChatEventType.AUTO_DELETE_CHANGED
    assert events[0]["details"] == "86400 секунд"
