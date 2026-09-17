"""Работа с базой на временной SQLite: запись, правки, выборки, дедупликация."""

from datetime import datetime

from app.db import crud
from app.db.models import ChatEventType


def make_chat(db, telegram_chat_id=-100, title="Тест"):
    return crud.get_or_create_chat(db, telegram_chat_id, title)


def make_message(db, chat, telegram_message_id, text, sent_at, user=None):
    return crud.create_message(
        db,
        telegram_message_id,
        chat.id,
        user.id if user is not None else None,
        text,
        sent_at,
    )


def test_чат_не_дублируется(db):
    first = make_chat(db)
    second = make_chat(db)

    assert first.id == second.id


def test_название_чата_обновляется(db):
    chat = make_chat(db, title="Старое")

    crud.set_chat_title(db, chat.id, "Новое")

    assert make_chat(db).title == "Новое"


def test_пользователь_не_дублируется(db):
    first = crud.get_or_create_user(db, 1, "ivan", "Иван", None)
    second = crud.get_or_create_user(db, 1, "ivan", "Иван", None)

    assert first.id == second.id


def test_сообщение_находится_по_telegram_id(db):
    chat = make_chat(db)
    make_message(db, chat, 5, "Привет", datetime(2026, 9, 1, 9, 0))

    found = crud.get_message_by_telegram_id(db, chat.id, 5)

    assert found.text == "Привет"
    assert crud.get_message_by_telegram_id(db, chat.id, 6) is None


def test_правка_сохраняет_прежнюю_редакцию(db):
    chat = make_chat(db)
    stored = make_message(db, chat, 1, "Было", datetime(2026, 9, 1, 9, 0))

    crud.update_message_text(db, stored.id, "Стало", datetime(2026, 9, 1, 9, 30))

    assert stored.text == "Стало"
    assert [version.text for version in stored.versions] == ["Было"]


def test_повторная_правка_тем_же_текстом_не_плодит_версий(db):
    chat = make_chat(db)
    stored = make_message(db, chat, 1, "Текст", datetime(2026, 9, 1, 9, 0))

    crud.update_message_text(db, stored.id, "Текст", datetime(2026, 9, 1, 9, 30))

    assert stored.versions == []


def test_выборка_за_период_сортирована_и_ограничена(db):
    chat = make_chat(db)
    make_message(db, chat, 2, "второе", datetime(2026, 9, 2, 9, 0))
    make_message(db, chat, 1, "первое", datetime(2026, 9, 1, 9, 0))
    make_message(db, chat, 3, "вне периода", datetime(2026, 9, 5, 9, 0))

    found = crud.get_messages_by_period(
        db, chat.id, datetime(2026, 9, 1), datetime(2026, 9, 3)
    )

    assert [item.text for item in found] == ["первое", "второе"]


def test_поиск_по_подстроке(db):
    chat = make_chat(db)
    make_message(db, chat, 1, "отчёт за август", datetime(2026, 9, 1, 9, 0))
    make_message(db, chat, 2, "созвон в 15:00", datetime(2026, 9, 2, 9, 0))

    found = crud.search_messages(db, chat.id, "отчёт")

    assert [item.telegram_message_id for item in found] == [1]


def test_поиск_учитывает_период(db):
    chat = make_chat(db)
    make_message(db, chat, 1, "отчёт за июль", datetime(2026, 9, 1, 9, 0))
    make_message(db, chat, 2, "отчёт за август", datetime(2026, 9, 10, 9, 0))

    found = crud.search_messages(
        db, chat.id, "отчёт", datetime(2026, 9, 5), datetime(2026, 9, 30)
    )

    assert [item.telegram_message_id for item in found] == [2]


def test_поиск_не_выходит_за_пределы_чата(db):
    first = make_chat(db, -100)
    second = make_chat(db, -200, "Другой")
    make_message(db, first, 1, "отчёт", datetime(2026, 9, 1, 9, 0))
    make_message(db, second, 1, "отчёт", datetime(2026, 9, 1, 9, 0))

    assert len(crud.search_messages(db, first.id, "отчёт")) == 1


def test_служебное_событие_не_дублируется(db):
    chat = make_chat(db)
    moment = datetime(2026, 9, 1, 9, 0)

    first = crud.get_or_create_chat_event(
        db, chat.id, 7, ChatEventType.TITLE_CHANGED, moment, details="Новое"
    )
    second = crud.get_or_create_chat_event(
        db, chat.id, 7, ChatEventType.TITLE_CHANGED, moment, details="Новое"
    )

    assert first.id == second.id


def test_одно_сообщение_может_добавить_разных_участников(db):
    """Telegram добавляет нескольких человек одним сообщением — события разные."""
    chat = make_chat(db)
    maria = crud.get_or_create_user(db, 2, None, "Мария", None)
    petr = crud.get_or_create_user(db, 3, None, "Пётр", None)
    moment = datetime(2026, 9, 1, 9, 0)

    first = crud.get_or_create_chat_event(
        db, chat.id, 7, ChatEventType.MEMBER_JOINED, moment, target_user_id=maria.id
    )
    second = crud.get_or_create_chat_event(
        db, chat.id, 7, ChatEventType.MEMBER_JOINED, moment, target_user_id=petr.id
    )

    assert first.id != second.id


def test_события_за_период(db):
    chat = make_chat(db)
    crud.get_or_create_chat_event(
        db, chat.id, 1, ChatEventType.CHAT_CREATED, datetime(2026, 9, 1, 9, 0)
    )
    crud.get_or_create_chat_event(
        db, chat.id, 2, ChatEventType.PHOTO_CHANGED, datetime(2026, 9, 20, 9, 0)
    )

    found = crud.get_chat_events_by_period(
        db, chat.id, datetime(2026, 9, 1), datetime(2026, 9, 10)
    )

    assert [item.event_type for item in found] == [ChatEventType.CHAT_CREATED]


def test_права_админа(db):
    assert crud.is_admin(db, 42) is False

    crud.add_admin(db, 42)

    assert crud.is_admin(db, 42) is True


def test_обзор_чатов_считает_сообщения(db):
    chat = make_chat(db, title="Отдел")
    make_message(db, chat, 1, "раз", datetime(2026, 9, 1, 9, 0))
    make_message(db, chat, 2, "два", datetime(2026, 9, 2, 9, 0))

    telegram_chat_id, title, count, last_sent_at = crud.get_chats_overview(db)[0]

    assert (telegram_chat_id, title, count) == (-100, "Отдел", 2)
    assert last_sent_at == datetime(2026, 9, 2, 9, 0)
