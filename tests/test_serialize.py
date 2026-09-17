"""Сборка JSON: выгрузку читают люди, технических значений в ней быть не должно."""

import json
from datetime import datetime

import pytest

from app.admin_bot.serialize import serialize_message, timeline
from app.db.models import (
    Attachment,
    ChatEvent,
    ChatEventType,
    ForwardOriginType,
    Message,
    MessageVersion,
    TelegramUser,
)

IVAN = TelegramUser(telegram_user_id=1, first_name="Иван", username="ivan")
MARIA = TelegramUser(telegram_user_id=2, first_name="Мария")


def message(**kwargs):
    defaults = {
        "telegram_message_id": 1,
        "user": IVAN,
        "text": "Текст",
        "sent_at": datetime(2026, 9, 1, 9, 0),
    }
    return Message(**{**defaults, **kwargs})


def test_ни_null_ни_true_ни_false():
    payload = timeline(
        [
            message(),
            message(telegram_message_id=2, user=None, text=None, reply_to_message_id=99),
        ],
        [
            ChatEvent(
                telegram_message_id=3,
                event_type=ChatEventType.TITLE_CHANGED,
                actor=IVAN,
                details="Новое название",
                happened_at=datetime(2026, 9, 1, 10, 0),
            )
        ],
    )
    text = json.dumps(payload, ensure_ascii=False)

    for forbidden in ("null", "true", "false"):
        assert f": {forbidden}" not in text


def test_сообщение_без_текста_подписано():
    result = serialize_message(message(text=None), {})

    assert result["текст"] == "— без текста —"


def test_отправитель_неизвестен_у_анонимных():
    result = serialize_message(message(user=None), {})

    assert result["отправитель"] == "не определён"


def test_ответ_на_сообщение_из_выборки():
    parent = message(telegram_message_id=10, text="Пришли отчёт")
    child = message(telegram_message_id=11, user=MARIA, reply_to_message_id=10)

    result = serialize_message(child, {10: parent})

    assert result["ответ на"]["отправитель"] == "Иван (@ivan)"
    assert result["ответ на"]["текст"] == "Пришли отчёт"


def test_ответ_на_сообщение_вне_выборки():
    result = serialize_message(message(reply_to_message_id=777), {})

    assert result["ответ на"]["telegram_message_id"] == 777
    assert "не попало в выгрузку" in result["ответ на"]["примечание"]


def test_обычное_сообщение_не_ответ():
    assert serialize_message(message(), {})["ответ на"] == "не ответ на сообщение"


def test_длинный_текст_родителя_обрезается():
    parent = message(telegram_message_id=10, text="а" * 500)
    child = message(telegram_message_id=11, reply_to_message_id=10)

    preview = serialize_message(child, {10: parent})["ответ на"]["текст"]

    assert preview.endswith("…")
    assert len(preview) < 200


def test_пересылка_от_скрытого_пользователя():
    result = serialize_message(
        message(
            forward_origin_type=ForwardOriginType.HIDDEN_USER,
            forward_from_name="Кто-то",
            forward_origin_date=None,
        ),
        {},
    )

    assert result["переслано из"]["тип источника"] == "скрытый пользователь"
    assert result["переслано из"]["id источника"] == "скрыт"
    assert result["переслано из"]["дата оригинала"] == "неизвестна"


def test_пересылка_из_канала():
    result = serialize_message(
        message(
            forward_origin_type=ForwardOriginType.CHANNEL,
            forward_from_name="РБК",
            forward_from_id=-100,
            forward_origin_date=datetime(2026, 8, 1, 9, 0),
            is_automatic_forward=True,
        ),
        {},
    )

    assert result["переслано из"]["тип источника"] == "канал"
    assert result["переслано из"]["автопересылка из канала"] == "да"


def test_история_правок():
    edited = message(
        edited_at=datetime(2026, 9, 1, 9, 30),
        versions=[
            MessageVersion(text="Было", replaced_at=datetime(2026, 9, 1, 9, 30))
        ],
    )

    result = serialize_message(edited, {})

    assert result["была ли отредактирована"] == "да"
    assert result["история правок"][0]["текст"] == "Было"


def test_без_правок_и_вложений():
    result = serialize_message(message(), {})

    assert result["история правок"] == "правок не было"
    assert result["список вложений"] == "вложений нет"


def test_вложение_без_имени():
    with_photo = message(
        attachments=[Attachment(file_type="photo", file_path="2026-09-01/photo.jpg")]
    )

    result = serialize_message(with_photo, {})

    assert result["список вложений"][0]["original_filename"] == "имени нет"


def test_события_и_сообщения_идут_одной_хронологией():
    payload = timeline(
        [
            message(telegram_message_id=1, sent_at=datetime(2026, 9, 1, 9, 0)),
            message(telegram_message_id=3, sent_at=datetime(2026, 9, 1, 11, 0)),
        ],
        [
            ChatEvent(
                telegram_message_id=2,
                event_type=ChatEventType.MEMBER_JOINED,
                actor=IVAN,
                target=MARIA,
                happened_at=datetime(2026, 9, 1, 10, 0),
            )
        ],
    )

    assert [item["тип"] for item in payload] == [
        "сообщение",
        "служебное событие",
        "сообщение",
    ]


@pytest.mark.parametrize(
    "actor, target, expected",
    [
        (IVAN, IVAN, "участник вошёл сам"),
        (IVAN, MARIA, "участника добавили"),
    ],
)
def test_вход_сам_или_добавили(actor, target, expected):
    event = ChatEvent(
        telegram_message_id=1,
        event_type=ChatEventType.MEMBER_JOINED,
        actor=actor,
        target=target,
        actor_user_id=actor.telegram_user_id,
        target_user_id=target.telegram_user_id,
        happened_at=datetime(2026, 9, 1, 9, 0),
    )

    assert timeline([], [event])[0]["событие"] == expected
