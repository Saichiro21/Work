"""Скачивание вложений: важен не только успех, но и честный отказ.

Файл больше 20 МБ Bot API боту не отдаёт. Вложение всё равно должно попасть
в базу с пустым путём, иначе выгрузка скажет, что вложений у сообщения нет.
"""

import asyncio
from types import SimpleNamespace

from app.collector import media

MEGABYTE = 1024 * 1024


def fake_message(**kwargs):
    """Заготовка апдейта: detect_attachment проверяет каждое поле по очереди."""
    fields = {
        "message_id": 1,
        "photo": None,
        "animation": None,
        "video": None,
        "video_note": None,
        "voice": None,
        "audio": None,
        "document": None,
    }
    return SimpleNamespace(**{**fields, **kwargs})


def document(file_size, file_name="совещание.mp4"):
    return SimpleNamespace(
        file_id="AgAC", file_name=file_name, file_size=file_size
    )


def voice(duration=42, file_size=118 * 1024):
    return SimpleNamespace(file_id="AwAC", file_size=file_size, duration=duration)


class FakeBot:
    def __init__(self, file_path="documents/file_1.mp4", fail_on=None):
        self.file_path = file_path
        self.fail_on = fail_on
        self.get_file_calls = 0

    async def get_file(self, file_id):
        self.get_file_calls += 1
        if self.fail_on == "get_file":
            raise RuntimeError("429 Too Many Requests")
        return SimpleNamespace(file_path=self.file_path)

    async def download_file(self, file_path, destination):
        if self.fail_on == "download":
            raise RuntimeError("оборвалось соединение")
        destination.write_bytes(b"file-bytes")


def download(bot, message):
    return asyncio.run(media.download_attachment(bot, message, -100500))


def test_без_вложения_ничего_не_возвращаем():
    assert download(FakeBot(), fake_message()) is None


def test_у_голосового_берём_длительность():
    found = media.detect_attachment(fake_message(voice=voice(duration=42)))

    file_type, _, original_filename, file_size, duration = found

    assert file_type == "voice"
    # Имени у голосовых Telegram не присылает
    assert original_filename is None
    assert (file_size, duration) == (118 * 1024, 42)


def test_у_документа_длительности_нет():
    found = media.detect_attachment(fake_message(document=document(3 * MEGABYTE)))

    assert found[4] is None


def test_слишком_большой_файл_записан_без_пути():
    bot = FakeBot()

    result = download(bot, fake_message(document=document(45 * MEGABYTE)))

    assert result == ("document", None, "совещание.mp4", 45 * MEGABYTE, None)
    # За файлом, который всё равно не отдадут, ходить незачем
    assert bot.get_file_calls == 0


def test_сбой_запроса_файла_не_теряет_вложение():
    bot = FakeBot(fail_on="get_file")

    result = download(bot, fake_message(voice=voice()))

    # Длительность известна из апдейта, даже когда самого файла не получить
    assert result == ("voice", None, None, 118 * 1024, 42)


def test_обрыв_загрузки_не_теряет_вложение():
    bot = FakeBot(fail_on="download")

    result = download(bot, fake_message(document=document(3 * MEGABYTE)))

    assert result[1] is None


def test_скачанный_файл_описан_путём_от_корня_проекта(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "BASE_DIR", tmp_path)
    monkeypatch.setattr(media, "ATTACHMENTS_DIR", tmp_path / "storage" / "attachments")

    file_type, file_path, name, size, duration = download(
        FakeBot(), fake_message(document=document(3 * MEGABYTE))
    )

    assert file_type == "document"
    assert file_path == "storage/attachments/-100500/1_совещание.mp4"
    assert name == "совещание.mp4"
    assert (size, duration) == (3 * MEGABYTE, None)
    assert (tmp_path / file_path).read_bytes() == b"file-bytes"
