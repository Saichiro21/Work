"""Выбор типов вложений в /files.

Главное здесь — чтобы «Все вложения» действительно означало все: новый тип,
забытый в группах, тихо исчезал бы из архивов, и заметить это было бы нечем.
"""

from datetime import datetime

from app import paths
from app.admin_bot.handlers import files
from app.admin_bot.handlers.common import FILE_TYPE_LABELS
from app.db.models import Attachment, Message

BUCKETS = (files.DOCUMENT_TYPES, files.PICTURE_TYPES, files.SOUND_TYPES)


def message_with(*attachments):
    return Message(
        telegram_message_id=1,
        sent_at=datetime(2026, 9, 1, 9, 0),
        attachments=list(attachments),
    )


def test_каждый_тип_попал_ровно_в_одну_группу():
    for file_type in FILE_TYPE_LABELS:
        groups = sum(file_type in bucket for bucket in BUCKETS)
        assert groups == 1, f"{file_type} попал в {groups} групп вместо одной"


def test_все_вложения_это_действительно_все():
    assert files.ALL_TYPES == set(FILE_TYPE_LABELS)


def test_у_каждого_типа_есть_подпись_для_подсчёта():
    assert set(files.COUNT_LABELS) == set(FILE_TYPE_LABELS)


def test_кружок_относится_к_звуку_а_не_к_видео():
    """В переписке кружок звучит как голосовое, поэтому и выбирается вместе с ним."""
    assert "video_note" in files.SOUND_TYPES
    assert "video_note" not in files.PICTURE_TYPES


def test_подсчёт_по_типам():
    messages = [
        message_with(
            Attachment(file_type="photo", file_path="a"),
            Attachment(file_type="document", file_path="b"),
        ),
        message_with(Attachment(file_type="photo", file_path="c")),
    ]

    assert files._counts(messages) == {"photo": 2, "document": 1}


def test_в_перечислении_названия_во_множественном_числе():
    text = files._counts_text({"voice": 3}, "01.09.2026 — 17.09.2026")

    assert "За 01.09.2026 — 17.09.2026 найдено:" in text
    assert "• голосовые — 3" in text


def test_о_невыбранных_типах_не_сообщаем():
    """Жалоба на файл, которого человек не просил, только сбивает с толку."""
    messages = [
        message_with(
            # Путь пустой: файл скачать не удалось, такие попадают в отчёт о проблемах
            Attachment(file_type="voice", file_path=None, file_size=1024),
            Attachment(file_type="document", file_path=None, file_size=2048),
        )
    ]

    _, _, _, not_saved = files._collect(messages, files.DOCUMENT_TYPES)

    assert [name for name, _ in not_saved] == ["документ"]


def test_общий_файл_в_архиве_под_именем_своего_сообщения(tmp_path, monkeypatch):
    """После дедупликации два сообщения делят файл, но в архиве тёзками не становятся."""
    directory = tmp_path / "storage" / "attachments"
    directory.mkdir(parents=True)
    (directory / "3_photo.jpg").write_bytes(b"jpg")
    monkeypatch.setattr(paths, "BASE_DIR", tmp_path)
    monkeypatch.setattr(paths, "ATTACHMENTS_DIR", directory)
    shared = "storage/attachments/3_photo.jpg"

    first = message_with(Attachment(file_type="photo", file_path=shared))
    first.telegram_message_id = 3
    second = message_with(
        Attachment(file_type="photo", file_path=shared, original_filename="фасад.jpg")
    )
    second.telegram_message_id = 8

    found, _, _, _ = files._collect([first, second], files.PICTURE_TYPES)

    assert [arcname for arcname, _, _, _ in found] == [
        "2026-09-01/3_photo.jpg",
        "2026-09-01/8_фасад.jpg",
    ]
