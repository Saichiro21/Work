"""Дедупликация вложений: одинаковый файл лежит на диске один раз.

Главный риск здесь не лишняя копия, а запись, которая ведёт в пустоту: удалить
файл, на который кто-то ещё ссылается, хуже, чем оставить дубль.
"""

from datetime import datetime

import pytest

from app import paths
from app.collector import dedup
from app.db import crud
from app.db.models import Attachment


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Каталог вложений на время теста; put кладёт файл и отдаёт путь как в базе."""
    directory = tmp_path / "storage" / "attachments"
    directory.mkdir(parents=True)
    monkeypatch.setattr(paths, "BASE_DIR", tmp_path)
    monkeypatch.setattr(paths, "ATTACHMENTS_DIR", directory)

    def put(name, content):
        path = directory / name
        path.write_bytes(content)
        return str(path.relative_to(tmp_path))

    put.root = tmp_path
    return put


def make_message(db, telegram_message_id):
    chat = crud.get_or_create_chat(db, -100, "Тест")
    return crud.create_message(
        db, telegram_message_id, chat.id, None, None, datetime(2026, 9, 1, 9, 0)
    )


def attach(db, telegram_message_id, file_path, sha256=None):
    message = make_message(db, telegram_message_id)
    return crud.create_attachment(
        db, message.id, "document", file_path, "смета.pdf", sha256=sha256
    )


def test_новый_файл_остаётся_на_месте_и_получает_хеш(db, storage):
    fresh = storage("1_смета.pdf", b"pdf")

    file_path, sha256 = dedup.share_existing(db, fresh)

    assert file_path == fresh
    assert sha256 == dedup.file_sha256(storage.root / fresh)
    assert (storage.root / fresh).is_file()


def test_повтор_ссылается_на_прежний_файл_а_копия_удаляется(db, storage):
    first = storage("1_смета.pdf", b"pdf")
    _, sha256 = dedup.share_existing(db, first)
    attach(db, 1, first, sha256)
    second = storage("2_смета.pdf", b"pdf")

    file_path, second_sha256 = dedup.share_existing(db, second)

    assert file_path == first
    assert second_sha256 == sha256
    assert not (storage.root / second).exists()
    assert (storage.root / first).is_file()


def test_разные_файлы_не_склеиваются(db, storage):
    first = storage("1_смета.pdf", b"pdf")
    _, sha256 = dedup.share_existing(db, first)
    attach(db, 1, first, sha256)
    second = storage("2_смета.pdf", b"other")

    file_path, _ = dedup.share_existing(db, second)

    assert file_path == second
    assert (storage.root / second).is_file()


def test_на_стёртый_с_диска_файл_не_ссылаемся(db, storage):
    """Запись о файле осталась, а сам он удалён: новая копия нужнее."""
    first = storage("1_смета.pdf", b"pdf")
    _, sha256 = dedup.share_existing(db, first)
    attach(db, 1, first, sha256)
    (storage.root / first).unlink()
    second = storage("2_смета.pdf", b"pdf")

    file_path, _ = dedup.share_existing(db, second)

    assert file_path == second
    assert (storage.root / second).is_file()


def test_без_файла_хеша_нет(db, storage):
    assert dedup.share_existing(db, None) == (None, None)


def test_старые_вложения_склеиваются_задним_числом(db, storage):
    first = storage("1_смета.pdf", b"pdf")
    second = storage("2_смета.pdf", b"pdf")
    other = storage("3_смета.pdf", b"other")
    attach(db, 1, first)
    attach(db, 2, second)
    attach(db, 3, other)

    hashed, removed = dedup.backfill(db)

    assert (hashed, removed) == (3, 1)
    rows = db.query(Attachment).order_by(Attachment.id).all()
    assert [row.file_path for row in rows] == [first, first, other]
    assert all(row.sha256 for row in rows)
    assert rows[0].sha256 == rows[1].sha256 != rows[2].sha256
    assert not (storage.root / second).exists()


def test_повторный_проход_ничего_не_трогает(db, storage):
    attach(db, 1, storage("1_смета.pdf", b"pdf"))
    attach(db, 2, storage("2_смета.pdf", b"pdf"))
    dedup.backfill(db)

    assert dedup.backfill(db) == (0, 0)


def test_вложение_без_файла_на_диске_пропускается(db, storage):
    attach(db, 1, "storage/attachments/нет_такого.pdf")

    assert dedup.backfill(db) == (0, 0)
    assert db.query(Attachment).one().sha256 is None


def test_пути_с_одним_хешем_без_повторов(db, storage):
    shared = storage("1_смета.pdf", b"pdf")
    attach(db, 1, shared, "abc")
    attach(db, 2, shared, "abc")
    attach(db, 3, None, "abc")

    assert crud.get_attachment_paths_by_sha256(db, "abc") == [shared]
