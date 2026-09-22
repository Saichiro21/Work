"""Сборка HTML-страницы: её открывают в браузере, а текст в ней чужой.

Главное, что проверяем, — экранирование: текст сообщений пишут люди, и строка
вида `<script>` не должна становиться разметкой. Остальное про читаемость:
разделители дней, цитаты со ссылкой на оригинал, прежние версии правок.
"""

import re
from base64 import b64encode
from datetime import datetime

import pytest

from app import paths
from app.admin_bot import html_report
from app.admin_bot.html_report import render_report
from app.db.models import (
    Attachment,
    ChatEvent,
    ChatEventType,
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


def report(messages, events=()):
    return render_report(
        "Отдел разработки",
        -100123,
        "01.09.2026",
        messages,
        events,
        generated_at=datetime(2026, 9, 19, 1, 15),
    )


def test_переписка_выглядит_как_в_ночном_телеграме():
    """Тёмное полотно, пузыри по содержимому и светлые имена поверх них."""
    styles = html_report.STYLES

    assert ":root { color-scheme: dark; }" in styles
    assert "background: #0e1621;" in styles
    # Пузырь по тексту, а не во всю колонку, и со скруглением, как в чате
    assert "background: #182533;" in styles
    assert "width: fit-content;" in styles
    assert "border-radius: 14px;" in styles
    # Имена на тёмном должны быть светлыми, иначе их не прочесть
    for color in html_report.PALETTE:
        assert int(color[1:3], 16) + int(color[3:5], 16) + int(color[5:], 16) > 380


def test_страница_целая_и_с_кодировкой():
    """Файл открывают двойным щелчком: без charset русский текст превратится в кашу."""
    page = report([message()])

    assert page.startswith("<!DOCTYPE html>")
    assert '<meta charset="utf-8">' in page
    assert page.rstrip().endswith("</html>")


def test_чужая_разметка_в_тексте_остаётся_текстом():
    page = report([message(text="<script>alert(1)</script> и <b>жирный</b>")])

    assert "<script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_экранируется_и_имя_автора():
    """Имя человек задаёт себе сам, так что это такой же чужой текст."""
    page = report([message(user=TelegramUser(telegram_user_id=3, first_name="<b>Ко"))])

    assert "<b>Ко" not in page
    assert "&lt;b&gt;Ко" in page


def test_в_шапке_чат_период_и_счёт():
    page = report([message(), message(telegram_message_id=2)])

    assert "Отдел разработки" in page
    assert "01.09.2026" in page
    assert "<dt>Сообщений</dt><dd>2</dd>" in page
    assert "19.09.2026 01:15" in page


def test_каждый_день_отделён_датой():
    page = report(
        [
            message(sent_at=datetime(2026, 9, 1, 9, 0)),
            message(telegram_message_id=2, sent_at=datetime(2026, 9, 2, 9, 0)),
            message(telegram_message_id=3, sent_at=datetime(2026, 9, 2, 10, 0)),
        ]
    )

    assert page.count('class="day"') == 2


def test_ответ_становится_цитатой_со_ссылкой():
    """В JSON тут номер сообщения, который надо искать глазами по файлу."""
    parent = message(telegram_message_id=10, text="Пришли отчёт")
    child = message(telegram_message_id=11, user=MARIA, reply_to_message_id=10)

    page = report([parent, child])

    assert 'id="m10"' in page
    assert '<a class="reply" href="#m10">' in page
    assert "Пришли отчёт" in page


def test_переход_по_цитате_подсвечивает_оригинал():
    """Иначе после прыжка непонятно, на какое из сообщений ты попал."""
    parent = message(telegram_message_id=10, text="Пришли отчёт")
    child = message(telegram_message_id=11, user=MARIA, reply_to_message_id=10)
    page = report([parent, child])

    assert "aim.classList.add('flash')" in page
    assert "@keyframes flash" in html_report.STYLES
    # Разгорается и гаснет плавно, а не появляется в полную силу разом
    assert "0% { opacity: 0; }" in html_report.STYLES
    assert "100% { opacity: 0; }" in html_report.STYLES
    # Подсветка идёт полосой во всю ширину экрана, поэтому она на строке, не на пузыре
    assert '<div class="row" id="m10">' in page
    assert ".row:target::after, .row.flash::after {" in html_report.STYLES
    assert "width: 100vw;" in html_report.STYLES
    # Подсветку снимают и ставят заново, иначе второй щелчок её не повторит
    assert "aim.classList.remove('flash');\n  void aim.offsetWidth;" in page
    # Гасит подсветку конец анимации: таймер обрывал бы её от частых нажатий
    assert "addEventListener('animationend'" in page
    assert "setTimeout" not in html_report.JUMP_SCRIPT
    # Цитировать нечего — и вести никуда не надо, скрипт в страницу не идёт
    assert "classList.add('flash')" not in report([message()])


def test_цитата_набрана_тем_же_кеглем_что_и_ответ():
    """Разнобой в размере текста в переписке заметнее всего на цитатах."""
    parent = message(telegram_message_id=10, text="Пришли отчёт")
    child = message(telegram_message_id=11, user=MARIA, reply_to_message_id=10)
    report([parent, child])

    rule = html_report.STYLES.split("  .forward, .reply {")[1].split("}")[0]
    assert "font-size" not in rule
    # У пометки о пересылке шрифт по-прежнему мелкий: это служебная строка
    forward = html_report.STYLES.split("  .forward {")[1].split("}")[0]
    assert "font-size: 13px;" in forward


def test_цитата_стоит_на_полупрозрачной_подложке():
    """Как в переписке: полоса слева, подложка под текстом, углы справа круглые."""
    parent = message(telegram_message_id=10, text="Пришли отчёт")
    child = message(telegram_message_id=11, user=MARIA, reply_to_message_id=10)
    report([parent, child])

    rule = html_report.STYLES.split("  .forward, .reply {")[1].split("}")[0]
    assert "background: rgba(51, 144, 236, .16);" in rule
    assert "border-radius: 3px 8px 8px 3px;" in rule
    # Подложка есть всегда, и подсвечивать цитату под курсором уже незачем
    assert ".reply:hover" not in html_report.STYLES
    # Текст цитаты белый, как и сам ответ, а приглушено только пояснение
    assert ".reply-text { color: #e9edf0; }" in html_report.STYLES
    assert ".reply-lost { color: #8fa3b5; }" in html_report.STYLES


def test_длинная_цитата_обрывается_троеточием():
    """Строка без пробелов не переносилась и вылезала за край пузыря."""
    parent = message(telegram_message_id=10, text="а" * 400)
    child = message(telegram_message_id=11, user=MARIA, reply_to_message_id=10)
    report([parent, child])

    rule = html_report.STYLES.split("  .reply-author, .reply-text, .reply-kind {")[1]
    rule = rule.split("}")[0]
    assert "white-space: nowrap;" in rule
    assert "text-overflow: ellipsis;" in rule
    # Ужимать строку флекс не станет, пока ей не разрешат быть уже содержимого
    assert ".reply-body { min-width: 0; }" in html_report.STYLES
    # Ширину цитате ограничиваем, иначе пузырь растягивается во всю страницу
    assert "max-width: 440px;" in html_report.STYLES
    # А обрывается строка по имени автора: в расчёт ширины она не идёт вовсе
    assert ".reply-text, .reply-kind { width: 0; min-width: 100%; }" in html_report.STYLES


def test_узкое_окно_не_ездит_вбок():
    """Подсветка ответа шире окна на полосу прокрутки, и страницу вело вбок."""
    rule = html_report.STYLES.split("  body {")[1].split("}")[0]

    assert "overflow-x: hidden;" in rule
    # clip надёжнее hidden: он не делает из полотна ещё один слой прокрутки
    assert rule.index("overflow-x: hidden;") < rule.index("overflow-x: clip;")


def test_служебные_события_и_дата_светлее_полотна():
    """Тёмная плашка на тёмном полотне сливалась с ним, как в старой теме."""
    styles = html_report.STYLES

    for kind in ("  .event {", "  .day {"):
        rule = styles.split(kind)[1].split("}")[0]
        assert "background: rgba(26, 42, 58, .92);" in rule, kind
        assert "color: #d3e2f0;" in rule, kind
    assert ".event time { color: #9db3c8; }" in styles


def test_переписку_щипком_не_приближают():
    """От приближения она ездила в стороны, а читать её крупнее ни к чему."""
    styles = html_report.STYLES

    body = styles.split("  body {")[1].split("}")[0]
    assert "touch-action: pan-y;" in body
    # А снимок в просмотре приближают: за этим его и открывают
    view = styles.split("  .lightbox {")[1].split("}")[0]
    assert "touch-action: auto;" in view


def test_в_цитате_вместо_без_текста_сам_вид_вложения(attachments):
    """«Без текста» ничего не объясняет: в переписке там стоит вид вложения."""
    kinds = {
        "voice": "&bull; Голосовое сообщение",
        "video_note": "Видеосообщение",
        "video": "Видео",
        "photo": "Фото",
    }
    for index, (file_type, line) in enumerate(kinds.items()):
        parent = voice(attachments(f"8{index}.bin"), file_type=file_type)
        parent.telegram_message_id = 10
        child = message(telegram_message_id=11, user=MARIA, reply_to_message_id=10)

        page = report([parent, child])

        assert line in page
        assert "без текста" not in page


def test_мини_кадр_цитаты_берут_с_самой_страницы(attachments):
    """Вшивать те же байты второй раз ради рамки 34 на 34 точки незачем."""
    parent = voice(attachments("85.mp4"), file_type="video_note")
    parent.telegram_message_id = 10
    child = message(telegram_message_id=11, user=MARIA, reply_to_message_id=10)

    page = report([parent, child])

    # У кружка рамка круглая, и кадр для неё снимают с видео на полотно
    assert '<span class="reply-mini round" data-aim="m10">' in page
    assert "document.getElementById(mini.dataset.aim)" in page
    assert "sheet.toDataURL('image/jpeg', 0.7)" in page
    # Ответу на текст рамка не нужна, и скрипт к ней в страницу не идёт
    plain = report(
        [
            message(telegram_message_id=10, text="Пришли отчёт"),
            message(telegram_message_id=11, user=MARIA, reply_to_message_id=10),
        ]
    )
    assert 'class="reply-mini' not in plain
    assert "mini.dataset.aim" not in plain


def test_ответ_на_сообщение_вне_периода_объяснён():
    page = report([message(reply_to_message_id=777)])

    assert "href=" not in page
    assert "которого нет" in page


def test_время_стоит_в_нижнем_углу_пузыря():
    """Как в переписке: сверху имя, снизу справа время и пометка о правке."""
    page = report([message(edited_at=datetime(2026, 9, 1, 9, 30))])

    stamp = page.split('<span class="stamp">')[1].split("</span></p>")[0]
    assert "изменено" in stamp
    assert stamp.count("<time>") == 1
    # В шапке остаётся только имя автора
    assert "<time>" not in page.split("<header>")[1].split("</header>")[0]
    # Время внутри абзаца и обтекается текстом: своей строки пузырю не добавляет
    assert ".stamp { float: right;" in html_report.STYLES


def test_без_подписи_время_идёт_своей_строкой(attachments):
    """Приклеиться не к чему: плавающее время в пустом абзаце просто пропало бы."""
    page = report([shot(attachments("66.jpg", b"jpeg-bytes"))])

    assert '<div class="stamp alone">' in page
    assert ".stamp.alone { float: none;" in html_report.STYLES


def test_правки_лежат_под_щелчком():
    edited = message(edited_at=datetime(2026, 9, 1, 9, 30))
    edited.versions = [
        MessageVersion(text="Тект", replaced_at=datetime(2026, 9, 1, 9, 30))
    ]

    page = report([edited])

    assert "изменено" in page
    assert "<details" in page
    assert "Тект" in page


def test_пересылка_подписана_строкой():
    page = report(
        [
            message(
                forward_origin_type="channel",
                forward_from_name="Финансы",
                forward_origin_date=datetime(2026, 8, 30, 12, 0),
            )
        ]
    )

    assert "Переслано от: Финансы (канал)" in page


def test_вложение_описано_по_русски():
    item = message(text=None)
    item.attachments = [
        Attachment(
            file_type="voice",
            file_path="storage/attachments/a.ogg",
            file_size=52 * 1024,
            duration_seconds=42,
        )
    ]

    page = report([item])

    assert "голосовое сообщение" in page
    assert "0:42" in page
    assert "52 КБ" in page
    # Текста у голосового не бывает, и подписывать его «без текста» незачем
    assert "без текста" not in page


def test_не_скачанный_файл_помечен():
    item = message()
    item.attachments = [
        Attachment(file_type="video", file_path=None, file_size=41 * 1024 * 1024)
    ]

    page = report([item])

    assert "не сохранён" in page


def test_сообщение_без_текста_и_вложений_подписано():
    page = report([message(text=None)])

    assert "без текста" in page


def test_служебное_событие_строкой_по_центру():
    events = [
        ChatEvent(
            telegram_message_id=5,
            event_type=ChatEventType.MEMBER_JOINED,
            actor=IVAN,
            target=MARIA,
            # Вошёл сам или добавили — видно по тому, совпадают ли эти двое
            actor_user_id=1,
            target_user_id=2,
            happened_at=datetime(2026, 9, 1, 9, 30),
        )
    ]

    page = report([message()], events)

    assert 'class="event"' in page
    assert "Иван (@ivan) — участника добавили: Мария" in page
    assert "<dt>Служебных событий</dt><dd>1</dd>" in page


def test_события_и_сообщения_идут_по_времени():
    """Хронология общая с JSON, поэтому событие встаёт между сообщениями."""
    events = [
        ChatEvent(
            telegram_message_id=2,
            event_type=ChatEventType.TITLE_CHANGED,
            actor=IVAN,
            details="Новое название",
            happened_at=datetime(2026, 9, 1, 9, 30),
        )
    ]
    page = report(
        [
            message(text="первое"),
            message(telegram_message_id=3, text="второе", sent_at=datetime(2026, 9, 1, 10, 0)),
        ],
        events,
    )

    assert page.index("первое") < page.index("Новое название") < page.index("второе")


def test_пустой_период_не_даёт_пустой_страницы():
    """Бот такой файл не пришлёт, но страница всё равно должна что-то говорить."""
    page = report([])

    assert "записей нет" in page


def test_цвет_имени_у_автора_не_меняется():
    """Цвет помогает читать переписку только пока он у человека один."""
    page = report(
        [
            message(),
            message(telegram_message_id=2, sent_at=datetime(2026, 9, 1, 9, 5)),
            message(telegram_message_id=3, user=MARIA, sent_at=datetime(2026, 9, 1, 9, 6)),
        ]
    )

    authors = re.findall(r'class="author" style="color: (#\w+)">([^<]+)<', page)
    assert [name for _, name in authors] == [
        "Иван (@ivan)",
        "Иван (@ivan)",
        "Мария",
    ]
    assert authors[0][0] == authors[1][0]


@pytest.fixture
def attachments(tmp_path, monkeypatch):
    """Каталог вложений на время теста: страница читает файлы с диска."""
    directory = tmp_path / "storage" / "attachments"
    directory.mkdir(parents=True)
    monkeypatch.setattr(paths, "BASE_DIR", tmp_path)
    monkeypatch.setattr(paths, "ATTACHMENTS_DIR", directory)

    def put(name, content=b"media-bytes"):
        path = directory / name
        path.write_bytes(content)
        return str(path.relative_to(tmp_path))

    return put


def voice(path, file_type="voice", **kwargs):
    item = message(text=None)
    item.attachments = [
        Attachment(
            file_type=file_type,
            file_path=path,
            file_size=kwargs.get("file_size", 52 * 1024),
            duration_seconds=kwargs.get("duration_seconds", 42),
        )
    ]
    return item


def test_голосовое_играет_прямо_со_страницы(attachments):
    """Вшитый звук — главное отличие от JSON: слушать можно без архива."""
    page = report([voice(attachments("42.oga", b"ogg-bytes"))])

    assert f"data:audio/ogg;base64,{b64encode(b'ogg-bytes').decode()}" in page
    # Плеер браузера шире пузыря, поэтому кнопка и дорожка звука свои
    assert 'class="voice-play"' in page
    assert 'class="voice-wave"' in page
    assert '<span class="voice-line" data-idle="0:42, 52 КБ">0:42, 52 КБ</span>' in page


def test_кружок_остаётся_кружком(attachments):
    page = report([voice(attachments("43.mp4", b"mp4-bytes"), file_type="video_note")])

    assert '<video class="note-video"' in page
    assert "data:video/mp4;base64," in page


def test_у_кружка_нет_своих_кнопок(attachments):
    """Треугольник, громкость и разворот загораживали бы картинку."""
    page = report([voice(attachments("43.mp4"), file_type="video_note")])

    assert "controls" not in page
    # Вместо кнопок — кольцо по ободу: оно и показывает ход, и перематывает
    assert 'class="ring-bar"' in page
    assert 'class="ring-hit"' in page


def test_скрипт_приходит_вместе_с_кружком(attachments):
    """Без него щелчок по видео без кнопок ничего бы не делал."""
    page = report([voice(attachments("43.mp4"), file_type="video_note")])

    assert "<script>" in page
    assert "video.play()" in page


def test_страница_без_записей_обходится_без_скриптов():
    """Переписке из одного текста они ни к чему — и в файл не попадают."""
    page = report([message()])

    assert "<script" not in page


def test_у_голосового_и_кружка_скрипты_свои(attachments):
    """Страница берёт только те, без которых её плееры не заработают."""
    only_voice = report([voice(attachments("42.oga"))])
    only_note = report([voice(attachments("43.mp4"), file_type="video_note")])

    assert "querySelectorAll('.voice')" in only_voice
    assert "querySelectorAll('.note')" not in only_voice
    assert "querySelectorAll('.note')" in only_note
    assert "querySelectorAll('.voice')" not in only_note


def test_не_сыгравший_кружок_обходится_без_скрипта(attachments):
    page = report([voice(None, file_type="video_note")])

    assert "<script" not in page
    assert "не сохранён" in page


def test_тяжёлая_запись_в_страницу_не_лезет(attachments, monkeypatch):
    """Иначе одна длинная запись раздует файл до неотправляемого."""
    monkeypatch.setattr(html_report, "EMBED_FILE_LIMIT", 5)

    page = report([voice(attachments("44.oga", b"ogg-bytes"))])

    assert "base64" not in page
    assert "не поместился" in page


def test_запас_страницы_кончается_на_поздних(attachments, monkeypatch):
    """Что не влезло — остаётся строкой, а не выкидывается молча."""
    monkeypatch.setattr(html_report, "EMBED_TOTAL_LIMIT", 12)

    first = voice(attachments("45.oga", b"ogg-a"))
    second = voice(attachments("46.oga", b"ogg-b"))
    second.telegram_message_id = 2
    third = voice(attachments("47.oga", b"ogg-c"))
    third.telegram_message_id = 3

    page = report([first, second, third])

    assert page.count("base64") == 2
    assert page.count("не поместился") == 1


def test_не_скачанное_голосовое_подписано(attachments):
    page = report([voice(None)])

    assert "не сохранён" in page
    assert "<audio" not in page


def test_пропавший_с_диска_файл_подписан(attachments):
    page = report([voice("storage/attachments/нет-такого.oga")])

    assert "файла нет на диске" in page


def test_путь_за_пределы_вложений_не_читается(attachments):
    """Путь приходит из базы: читать по нему что попало со всего диска нельзя."""
    page = report([voice("storage/attachments/../../../etc/passwd")])

    assert "base64" not in page
    assert "файла нет на диске" in page


def test_незнакомый_браузеру_формат_остаётся_ссылкой(attachments):
    page = report([voice(attachments("48.amr"), file_type="audio")])

    assert "base64" not in page
    assert "файл в архиве" in page


def test_документ_рядом_с_голосовым_идёт_карточкой(attachments):
    """Показывать таблицу нечем, но сохранить её можно прямо со страницы."""
    item = voice(attachments("49.oga"))
    item.attachments.append(
        Attachment(
            file_type="document",
            file_path=attachments("отчёт.xlsx", b"table"),
            original_filename="отчёт.xlsx",
            file_size=1024,
        )
    )

    page = report([item])

    assert 'class="voice-wave"' in page
    assert '<a class="doc" href="data:application/octet-stream;base64,' in page
    assert 'download="отчёт.xlsx"' in page
    # Под именем стоит вес файла, а тип и так виден по расширению
    assert '<span class="meta">1 КБ</span>' in page
    assert "документ ·" not in page


def test_документ_идёт_без_своей_плашки(attachments):
    """Пузырь у сообщения один: вложенная карточка только дробила бы его."""
    item = message(text=None)
    item.attachments = [
        Attachment(
            file_type="document",
            file_path=attachments("смета.pdf", b"pdf"),
            original_filename="смета.pdf",
            file_size=2048,
        )
    ]

    page = report([item])

    assert '<article class="msg by-doc">' in page
    # Ни фона, ни полей: кружок со значком начинается там же, где имя автора
    rule = html_report.STYLES.split("  .doc {")[1].split("}")[0]
    assert "background" not in rule
    assert "padding" not in rule
    # Время стоит сбоку, на одной высоте с весом файла
    assert ".msg.by-doc .stamp.alone {" in html_report.STYLES


def test_файл_скачивается_только_после_вопроса(attachments):
    """Щелчок сразу кладёт файл на диск, и о таком лучше спросить заранее."""
    item = message(text=None)
    item.attachments = [
        Attachment(
            file_type="document",
            file_path=attachments("смета.pdf", b"pdf"),
            original_filename="смета.pdf",
            file_size=2048,
        )
    ]

    page = report([item])

    assert "Вы действительно хотите скачать этот файл?" in page
    assert '<button type="button" class="ask-yes">Скачать</button>' in page
    assert "event.preventDefault();" in page
    # Без единого файла спрашивать не о чем, и окно в страницу не идёт
    assert 'class="ask"' not in report([message()])


def test_вес_документа_берут_с_диска_если_его_нет_в_базе(attachments):
    """Старые вложения писались без веса, а файл рядом и всё про себя знает."""
    item = message(text=None)
    item.attachments = [
        Attachment(
            file_type="document",
            file_path=attachments("смета.pdf", b"x" * 3000),
            original_filename="смета.pdf",
            file_size=None,
        )
    ]

    page = report([item])

    assert '<span class="meta">2,9 КБ</span>' in page
    assert "неизвестен" not in page


def test_у_документа_свой_значок_листа(attachments):
    """Скрепка из набора символов рисуется по-разному, лист рисуем сами."""
    item = message(text=None)
    item.attachments = [
        Attachment(
            file_type="document",
            file_path=attachments("смета.pdf", b"pdf"),
            original_filename="смета.pdf",
            file_size=2048,
        )
    ]

    page = report([item])

    assert '<span class="doc-icon"><svg viewBox="0 0 24 24"' in page
    assert 'fill-rule="evenodd"' in page
    assert "&#128206;" not in page


def test_пропавший_документ_остаётся_строкой_с_причиной(attachments):
    """Файла нет — об этом честнее сказать, чем показать пустую карточку."""
    item = message(text=None)
    item.attachments = [
        Attachment(
            file_type="document",
            file_path="storage/attachments/отчёт.xlsx",
            original_filename="отчёт.xlsx",
            file_size=1024,
        )
    ]

    page = report([item])

    assert '<ul class="files">' in page
    assert "файла нет на диске" in page


def test_примечание_в_шапке_не_становится_кружком(attachments):
    """Оба звались note, и стили кружка сжимали абзац в шапке до 200 пикселей."""
    page = report([voice(attachments("43.mp4"), file_type="video_note")])
    head = page[page.index('<div class="head"') : page.index("</div>")]

    assert 'class="note"' not in head
    assert "Вложения вшиты в страницу" in head


def test_скрипт_ищет_то_что_страница_рисует(attachments):
    """Переименуй класс в разметке — и кружок молча перестанет включаться."""
    page = report([voice(attachments("43.mp4"), file_type="video_note")])

    for selector in re.findall(r"querySelector(?:All)?\('([^']+)'\)", html_report.NOTES_SCRIPT):
        if selector.startswith("."):
            assert f'class="{selector[1:]}"' in page, selector
        elif selector.startswith("["):
            assert f"{selector[1:-1]}=" in page, selector
        else:
            assert f"<{selector} " in page, selector


def test_состояния_кружка_описаны_в_стилях():
    """Скрипт вешает классы, стили их рисуют: разойдутся — кружок замрёт молча."""
    pattern = r"classList\.(?:add|remove|toggle)\(([^)]+)\)"
    toggled = set(re.findall(pattern, html_report.NOTES_SCRIPT))
    names = set(re.findall(r"'(\w+)'", " ".join(toggled)))

    assert names == {"playing", "paused", "seeking"}
    for name in names:
        assert f".note.{name}" in html_report.STYLES


def test_кольцо_поджимается_и_на_паузе_и_на_перемотке(attachments):
    """Взялись за кольцо — оно ведёт себя так же, как на паузе."""
    page = report([voice(attachments("43.mp4"), file_type="video_note")])

    assert 'class="ring-knob"' in page
    # Размер самого кружка при этом не меняется, поджимается только кольцо
    assert ".note.paused .ring, .note.seeking .ring { transform: scale(" in page
    assert ".note.paused .ring-knob, .note.seeking .ring-knob { opacity: 1; }" in page


def test_бегунок_заметно_толще_линии(attachments):
    """Иначе за него не взяться ни глазами, ни мышью."""
    page = report([voice(attachments("43.mp4"), file_type="video_note")])

    knob = float(re.search(r'class="ring-knob"[^>]*r="([\d.]+)"', page).group(1))
    line = float(re.search(r".note.paused .ring-bar[^{]*\{ stroke-width: ([\d.]+)", page).group(1))

    assert knob * 2 >= line * 3


def scrub_parts():
    """Куски скрипта: нажатие на кольцо и отпускание — в них вся перемотка."""
    script = html_report.NOTES_SCRIPT
    return (
        script[script.index("'pointerdown'") : script.index("'pointermove'")],
        script[script.index("'lostpointercapture'") :],
    )


def test_перемотка_идёт_молча_и_возвращает_звук():
    """Со звуком на ходу перемотка идёт обрывками и трещит — ведём только кадры."""
    press, release = scrub_parts()

    assert "video.pause();" in press
    assert "roll();" in release


def test_кольцо_до_первого_запуска_просто_включает():
    """Пока кружок не смотрели, перематывать в нём нечего, и бегунка нет."""
    press, _ = scrub_parts()

    assert "if (!started) { start(); return; }" in press


def test_бегунок_залит_белым():
    """Заливку сбивало правило на все circle: бегунок оставался пустым контуром."""
    styles = html_report.STYLES

    assert ".ring circle" not in styles
    assert ".ring-track, .ring-bar, .ring-hit { fill: none; }" in styles
    assert ".ring-knob {\n    fill: #fff;" in styles


def test_кольцо_ждёт_первого_щелчка(attachments):
    """До запуска кружок стоит чистым: в переписке кольца там тоже нет."""
    page = report([voice(attachments("43.mp4"), file_type="video_note")])

    assert ".note.playing .ring, .note.paused .ring, .note.seeking .ring { opacity: 1; }" in page
    assert "opacity: 0;\n    transform-origin: 50% 50%;" in page


def test_бегунок_не_срезается_краем_кольца(attachments):
    """Он выходит за обод, а всё лишнее за краем svg обрезает по умолчанию."""
    page = report([voice(attachments("43.mp4"), file_type="video_note")])

    assert "overflow: visible;" in page


def test_бегунок_идёт_покадрово_а_не_рывками():
    """timeupdate приходит раз в четверть секунды — на ходу это заметные скачки."""
    script = html_report.NOTES_SCRIPT

    assert "requestAnimationFrame(follow)" in script
    assert "cancelAnimationFrame(frame)" in script


def test_табличка_времени_висит_с_самого_начала(attachments):
    """До щелчка она показывает длину записи, как кружок в переписке."""
    page = report([voice(attachments("43.mp4"), file_type="video_note", duration_seconds=24)])

    assert '<span class="note-time">0:24</span>' in page
    assert "clock.textContent = clocked(video.duration);" in page


def test_у_кружка_две_таблички_и_никакой_подписи(attachments):
    """Ход записи и время стоят на картинке, а подпись повторяла бы её форму."""
    item = voice(attachments("43.mp4"), file_type="video_note", duration_seconds=24)
    page = report([item])

    assert '<span class="note-time">0:24</span>' in page
    # Справа — время сообщения: вес спросят в окне перед скачиванием
    when = html_report.format_time(item.sent_at)
    assert f'<span class="note-when">{when}</span>' in page
    # Имя для скачивания «видеосообщение.mp4» остаётся, а подписи под кружком нет
    assert '<span class="meta">' not in page


def test_кружок_обходится_без_пузыря(attachments):
    """В переписке его не обводят: кружок висит прямо на полотне."""
    page = report([voice(attachments("43.mp4"), file_type="video_note")])

    assert '<article class="msg by-note">' in page
    assert ".msg.by-note { background: none; }" in html_report.STYLES
    # Время уже стоит на картинке, второй раз его под кружком не пишут
    assert ".msg.by-note .stamp.alone, .msg.by-film .stamp.alone { display: none; }" in (
        html_report.STYLES
    )


def test_не_сыгравший_кружок_пузырь_оставляет(attachments):
    """Картинки нет, а значит, нет и таблички с временем — пузырь тут нужен."""
    page = report([voice(None, file_type="video_note")])

    assert 'class="msg by-note"' not in page
    assert '<div class="stamp alone">' in page


def test_под_троеточием_скачивание_и_скорость(attachments):
    """Своих кнопок у записей нет, а скачать и ускорить их иногда нужно."""
    circle = report([voice(attachments("43.mp4"), file_type="video_note")])
    sound = report([voice(attachments("42.oga"))])

    for page in (circle, sound):
        assert 'class="more"' in page
        assert 'class="sheet-save"' in page
        assert 'data-rate="1.5"' in page
        # Скорость применяется к самой записи, а выбранная остаётся подсвеченной
        assert "media.playbackRate = parseFloat(" in page
    assert 'download="видеосообщение.mp4"' in circle
    assert 'download="голосовое.oga"' in sound


def test_ссылка_на_скачивание_не_удваивает_страницу(attachments):
    """Вшить тот же файл вторым адресом — значит разом удвоить выгрузку."""
    page = report([voice(attachments("42.oga", b"ogg-bytes"))])

    assert page.count("base64") == 1
    # Поэтому адрес ставит скрипт, беря его из самой записи
    assert "save.href = media.currentSrc" in page


def test_голосовое_идёт_без_своей_плашки(attachments):
    """Пузырь у сообщения один: вложенная плашка только дробила бы его."""
    page = report([voice(attachments("50.oga", b"ogg-bytes"))])

    assert '<article class="msg by-voice">' in page
    # Ни фона, ни полей: кнопка начинается там же, где имя автора
    rule = html_report.STYLES.split("  .voice {")[1].split("}")[0]
    assert "background" not in rule
    assert "padding" not in rule
    # Время стоит сбоку, на одной высоте с длиной записи
    assert ".msg.by-voice .stamp.alone {" in html_report.STYLES
    # Длина и вес набраны тем же кеглем, что и время рядом с ними
    assert ".voice-line { font-size: 13px; margin-top: 0; }" in html_report.STYLES
    assert ".msg time, .edited { color: #708499; font-size: 13px; }" in html_report.STYLES


def test_запись_тоже_спрашивает_перед_скачиванием(attachments):
    """Сохранение есть и под троеточием, а спрашивать надо везде одинаково."""
    page = report([voice(attachments("51.oga", b"ogg-bytes"))])

    assert "Вы действительно хотите скачать этот файл?" in page
    assert "querySelectorAll('.doc, .sheet-save')" in page


def test_меню_анимации_тоже_получает_свой_скрипт(attachments):
    """Троеточие есть и у гифки, а скрипт к нему приходил только с записями."""
    page = report([shot(attachments("52.mp4", b"mp4-bytes"), file_type="animation")])

    assert 'class="sheet"' in page
    assert "save.href = media.currentSrc" in page


def test_меню_раскрывается_в_сторону_от_записи():
    """Оно не должно ложиться ни на сам кадр, ни на соседнее сообщение."""
    # У кружка — вверх и вправо, у голосового — вниз и вправо
    assert ".note .sheet { left: calc(100% - 18px); bottom: 28px; }" in html_report.STYLES
    assert ".voice .sheet { left: calc(100% - 28px); top: 100%; }" in html_report.STYLES
    assert ".sheet.open { display: block; }" in html_report.STYLES


def test_крестик_останавливает_запись(attachments):
    """Пауза оставляет запись на месте, а крестик возвращает её к началу."""
    circle = report([voice(attachments("53.mp4"), file_type="video_note")])
    sound = report([voice(attachments("54.oga"))])

    for page in (circle, sound):
        assert '<button class="stop" type="button" aria-label="Остановить">' in page
    # Крестик всегда над троеточием, а троеточие — в нижнем углу
    assert ".note .stop { right: -10px; top: 0; }" in html_report.STYLES
    assert ".note .more { position: absolute; right: -10px; bottom: 0; }" in html_report.STYLES
    assert ".voice .stop { right: 0; top: -2px; }" in html_report.STYLES
    assert ".voice .more { position: absolute; right: 0; bottom: -2px; }" in html_report.STYLES


def test_крестик_держится_и_на_паузе(attachments):
    """Остановиться хочется чаще как раз с паузы, а не на ходу."""
    page = report([voice(attachments("55.oga"))])

    assert ".note.playing .stop, .note.paused .stop, .note.seeking .stop," in page
    assert ".voice.started .stop { opacity: 1; pointer-events: auto; }" in page
    # Класс снимают только в конце записи и по самому крестику
    assert "function rest() { started = false; voice.classList.remove('started'); }" in page


def test_у_не_скачанного_кружка_длительность_уходит_в_подпись(attachments):
    """Картинки с табличкой нет, и кроме подписи длину записи показать негде."""
    page = report([voice(None, file_type="video_note", duration_seconds=24)])

    assert "видеосообщение, кружок · 0:24 · 52 КБ" in page


def test_кружок_крутится_молча_до_щелчка(attachments):
    """В переписке он тоже не стоит кадром: содержание видно до запуска."""
    page = report([voice(attachments("43.mp4"), file_type="video_note")])
    script = html_report.NOTES_SCRIPT

    assert "playsinline muted loop" in page
    assert "video.loop = true;" in script and "video.muted = true;" in script
    # А щелчок переводит кружок в обычный просмотр: со звуком и с начала
    start = script[script.index("function start()") : script.index("function seek(")]
    assert "video.muted = false;" in start
    assert "video.currentTime = 0;" in start


def test_кружки_за_краем_экрана_не_крутятся():
    """Иначе длинная выгрузка греет процессор десятком записей разом."""
    script = html_report.NOTES_SCRIPT

    assert "IntersectionObserver" in script
    assert "video.pause();" in script[script.index("IntersectionObserver") :]


def test_кольцо_появляется_с_первого_щелчка():
    """Кружок уже крутится немым, и play() события play тут не поднимет."""
    script = html_report.NOTES_SCRIPT
    start = script[script.index("function start()") : script.index("function seek(")]

    assert "live();" in start


def test_немой_кружок_помечен_перечёркнутым_динамиком(attachments):
    """До щелчка он крутится без звука, и в переписке об этом говорит значок."""
    page = report([voice(attachments("43.mp4"), file_type="video_note")])

    assert 'class="note-mute"' in page
    # Значок уходит на время просмотра и возвращается, когда кружок досмотрели
    assert ".note.playing .note-mute, .note.paused .note-mute, .note.seeking .note-mute" in page
    # Щелчок по нему должен включать кружок, а не упираться в сам значок
    mute = page[page.index("  .note-mute {") : page.index("  .note-mute svg")]
    assert "pointer-events: none;" in mute


def test_скрипт_голосового_ищет_то_что_страница_рисует(attachments):
    """Переименуй класс в разметке — и кнопка молча перестанет включать звук."""
    page = report([voice(attachments("42.oga"))])

    for selector in re.findall(r"querySelector(?:All)?\('([^']+)'\)", html_report.VOICE_SCRIPT):
        if selector.startswith("."):
            assert f'class="{selector[1:]}"' in page, selector
        else:
            assert f"<{selector} " in page, selector


def test_дорожка_голосового_рисуется_по_самой_записи():
    """Готовых громкостей Bot API не даёт, поэтому запись разбирает браузер."""
    script = html_report.VOICE_SCRIPT

    assert "decodeAudioData" in script
    # Разбор тяжёлый, поэтому он один на запись и только для видимых на экране
    assert "if (asked) { return; }" in script
    assert "IntersectionObserver" in script


def test_на_ходу_кнопка_показывает_паузу(attachments):
    """Скрипт вешает класс, стили меняют знак: разойдутся — знак замрёт."""
    page = report([voice(attachments("42.oga"))])

    assert "voice.classList.add('playing');" in page
    assert ".voice.playing .voice-play .pause { display: block; }" in page


def test_строка_голосового_меняется_на_ходу():
    """До запуска это длина и вес, дальше — сколько прошло из общей длины."""
    script = html_report.VOICE_SCRIPT

    assert "line.getAttribute('data-idle')" in script
    assert "clocked(audio.currentTime) + ' / ' + clocked(audio.duration)" in script


def test_вес_записи_ждёт_окна_подтверждения(attachments):
    """На кадре место дорого, а перед скачиванием вес как раз и нужен."""
    page = report([voice(attachments("43.mp4"), file_type="video_note", file_size=478632)])

    assert 'data-size="467,4 КБ"' in page
    assert '<p class="ask-size"></p>' in page
    assert "link.getAttribute('data-size')" in page


def shot(path, file_type="photo", **kwargs):
    """Сообщение с картинкой или видео — как voice(), только про изображение."""
    item = message(text=None)
    item.attachments = [
        Attachment(
            file_type=file_type,
            file_path=path,
            file_size=kwargs.get("file_size", 120 * 1024),
            original_filename=kwargs.get("original_filename"),
            duration_seconds=kwargs.get("duration_seconds"),
        )
    ]
    return item


def test_фото_видно_прямо_в_переписке(attachments):
    """Ради этого страницу и открывают: смотреть, не распаковывая архив."""
    page = report([shot(attachments("50.jpg", b"jpeg-bytes"))])

    assert f"data:image/jpeg;base64,{b64encode(b'jpeg-bytes').decode()}" in page
    assert '<figure class="shot">' in page
    # Щелчок разворачивает снимок во весь экран, для этого и окно, и скрипт
    assert '<div class="lightbox">' in page
    assert "querySelectorAll('.shot img, .film video')" in page


def test_без_фото_и_видео_окна_просмотра_нет(attachments):
    """Пустое окно поверх страницы ей ни к чему, как и скрипт к нему."""
    page = report([voice(attachments("42.oga"))])

    assert '<div class="lightbox">' not in page
    assert "querySelectorAll('.shot img, .film video')" not in page


def test_видео_открывается_окном_просмотра_а_гифка_крутится_сама(attachments):
    """Щелчок по кадру разворачивает его, как снимок, а гифка просто идёт по кругу."""
    clip = report([shot(attachments("51.mp4"), file_type="video")])
    gif = report([shot(attachments("52.mp4"), file_type="animation")])

    assert '<div class="film" title="Нажмите, чтобы открыть">' in clip
    # На полотне кадр крутится молча и без кнопок, звук приходит в просмотре
    assert "playsinline muted loop" in clip
    assert "querySelectorAll('.film')" in clip
    assert '<div class="lightbox">' in clip
    assert '<div class="view-play" hidden><video class="view-film" playsinline></video>' in clip
    # Ни пузыря, ни троеточия: кадр висит на полотне, как кружок
    assert '<article class="msg by-film">' in clip
    assert 'class="more"' not in clip
    assert "autoplay muted loop" in gif
    assert "controls" not in gif
    # Гифке плеер ни к чему: она и так крутится без звука
    assert "querySelectorAll('.film')" not in gif


def test_у_видео_дорожка_внизу_и_табличка_в_углу(attachments):
    """Кольцо кружку по форме, а прямоугольнику — прямая дорожка по краю."""
    item = shot(attachments("53.mp4"), file_type="video", duration_seconds=3)
    page = report([item])

    # Счётчик идёт с ведущим нулём, как в переписке
    assert '<span class="film-time">00:03</span>' in page
    assert "(mins < 10 ? '0' + mins : mins)" in page
    assert 'class="film-mute"' in page
    # Счётчик и динамик стоят одной табличкой в верхнем углу
    badge = page[page.index('<div class="film-badge">') : page.index('<span class="film-when">')]
    assert badge.count("film-time") == 1 and "film-mute" in badge
    # Дорожка идёт по нижнему краю от угла до угла, и она синяя, как в переписке
    assert '<div class="film-line"><div class="film-bar"></div></div>' in page
    assert ".film-line {\n    position: absolute;\n    left: 0;\n    right: 0;" in page
    assert ".film-bar { width: 0; height: 100%; background: #3390ec; }" in page
    # Время сообщения — в нижнем углу, как у кружка
    when = html_report.format_time(item.sent_at)
    assert f'<span class="film-when">{when}</span>' in page


def test_под_видео_в_просмотре_свой_остров(attachments):
    """Кнопки браузера у всех разные, поэтому их рисует сама страница."""
    page = report([shot(attachments("55.mp4"), file_type="video")])

    assert '<div class="view-bar">' in page
    for act in ("play", "sound", "wide", "mini", "rate"):
        assert f'data-act="{act}"' in page
    # Слева ход записи, справа остаток до конца, между ними дорожка
    assert '<span class="bar-now">00:00</span>' in page
    assert "'\\u2212' + clocked(whole - film.currentTime)" in page
    assert '<div class="bar-track"><div class="bar-fill"></div></div>' in page
    # Знак «играть» сменяется паузой, а динамик — перечёркнутым
    assert html_report.VIEW_BAR.count('class="sign-off"') == 2


def test_остров_стоит_под_кадром_а_не_на_нём():
    """Поверх картинки кнопки закрывали нижний край видео."""
    styles = html_report.STYLES

    stage = styles.split(".view-play {")[1].split("}")[0]
    assert "flex-direction: column;" in stage
    bar = styles.split(".view-bar {")[1].split("}")[0]
    assert "position: relative;" in bar
    # Высоту кадр делит с островом, иначе столбик не влезет в окно
    assert "max-height: calc(100vh - var(--band, 56px) * 2 - 92px);" in styles
    # Ширина у острова своя: в полный экран он растягивался во всё окно
    assert "align-self: center;" in bar and "width: 360px;" in bar


def test_в_полный_экран_кадр_уходит_со_своим_крестиком():
    """Крестик окна остаётся снаружи, и выйти было нечем, кроме Esc."""
    styles = html_report.STYLES

    assert 'data-act="shut"' in html_report.VIEWER
    assert ".view-shut { position: absolute; top: 10px; right: 14px; display: none; }" in styles
    assert ".view-play:fullscreen .view-shut { display: flex; }" in styles
    # Общее правило кнопок идёт выше, иначе крестик виден бы был всегда
    assert styles.index(".view-btn {") < styles.index(".view-shut {")
    assert "if (act === 'shut') { wide(); }" in html_report.VIEW_SCRIPT


def test_паузу_рисуют_две_палки_а_не_чужой_класс():
    """Класс off уже занят под «файл не скачан», и пауза краснела от него."""
    pause = html_report.VIEW_ICONS["pause"]

    assert pause.count("<rect") == 2
    assert 'class="off"' not in html_report.VIEW_BAR
    assert ".off { color: #ff8e86;" in html_report.STYLES


def test_кадр_с_подписью_возвращается_в_пузырь(attachments):
    """Без пузыря текст под видео висел сам по себе, во всю ширину страницы."""
    item = shot(attachments("57.mp4"), file_type="video")
    item.text = "Проверка связи"

    with_text = report([item])
    item.text = None
    without = report([item])

    assert '<article class="msg by-shot">' in with_text
    assert '<article class="msg by-film">' in without
    # Время у кадра с подписью идёт после текста, на самом кадре его уже нет
    assert ".msg.by-shot .film-when { display: none; }" in html_report.STYLES


def test_шире_кадр_делают_только_ради_подписи():
    """Без подписи расширять его незачем: кадр остаётся как в переписке."""
    styles = html_report.STYLES

    clip = styles.split(".clip { position: relative;")[1].split("}")[0]
    assert "width: 300px;" in clip
    assert ".msg.by-shot .clip { width: 360px; }" in styles


def test_у_кадра_без_подписи_пузырь_такой_же_как_с_ней(attachments):
    """Раньше пузыря не было вовсе, и имя висело прямо на полотне."""
    page = report([shot(attachments("58.mp4"), file_type="video")])
    styles = html_report.STYLES

    assert '<article class="msg by-film">' in page
    # Тот же пузырь по кадру, что и у снимка: имя с полями, кадр от края до края
    assert ".msg.by-shot, .msg.by-film {" in styles
    assert ".msg.by-film > :not(.clip) { padding-left: 12px; padding-right: 12px; }" in styles
    # Время стоит табличкой на кадре, второй раз его под ним не пишут
    assert ".msg.by-note .stamp.alone, .msg.by-film .stamp.alone { display: none; }" in styles


def test_пересылка_у_кружка_уходит_в_бок(attachments):
    """Сверху её место занято именем, и двумя строками они смотрятся лестницей."""
    circle = voice(attachments("70.mp4"), file_type="video_note")
    circle.forward_origin_type = "user"
    circle.forward_from_name = "Илья"

    page = report([circle])

    # У кружка пузыря нет, и пометка встаёт сбоку от него самого
    assert '<div class="aside">' in page
    assert page.index('class="aside"') > page.index("</header>")
    assert page.index('class="aside"') < page.index("Переслано от: Илья")

    # Из потока пометку вынимаем: иначе она растягивает пузырь вместе с временем
    aside = html_report.STYLES.split(".aside .forward {")[1].split("}")[0]
    assert "position: absolute;" in aside and "left: calc(100% + 10px);" in aside
    # А документу пересылка достаётся обычной строкой сверху
    paper = message(text=None)
    paper.attachments = [
        Attachment(file_type="document", file_path=attachments("72.pdf", b"pdf"), file_size=3)
    ]
    paper.forward_origin_type = "user"
    paper.forward_from_name = "Илья"
    assert 'class="aside"' not in report([paper])


def test_пересылка_у_записи_снимка_и_кадра_встаёт_сбоку_от_пузыря(attachments):
    """Внутри пузыря её обрезает скругление, а от края записи она свисает вниз."""
    sound = voice(attachments("71.oga"))
    sound.forward_origin_type = "user"
    sound.forward_from_name = "Илья"
    picture = shot(attachments("73.jpg", b"jpeg-bytes"))
    picture.forward_origin_type = "user"
    picture.forward_from_name = "Илья"
    movie = shot(attachments("74.mp4", b"mp4-bytes"), file_type="video")
    movie.forward_origin_type = "user"
    movie.forward_from_name = "Илья"

    for page in (report([sound]), report([picture]), report([movie])):
        # Пометка идёт после всего пузыря, а не строкой внутри него
        assert '<div class="aside">' in page
        assert page.index('class="aside"') < page.index('class="msg')
        assert page.index("Переслано от: Илья") > page.index("</article>")

    # Без пересылки обёртка пузырю ни к чему
    assert 'class="aside"' not in report([shot(attachments("75.jpg", b"jpeg-bytes"))])


def test_кружок_стоит_вровень_с_остальной_перепиской(attachments):
    """Без полей пузыря кружок и его имя съезжали левее всех сообщений."""
    page = report([voice(attachments("59.mp4"), file_type="video_note")])

    assert '<article class="msg by-note">' in page
    assert ".msg.by-note { background: none; }" in html_report.STYLES


def test_подпись_к_видео_не_ужимает_кадр():
    """Полоса под подпись отнимала у кадра вдвое больше своей высоты."""
    script = html_report.VIEW_SCRIPT
    styles = html_report.STYLES

    assert "var band = movie || note.hidden ? TOP" in script
    # Вместо полосы подпись висит поверх нижнего края кадра, над островом
    assert "stage.insertBefore(note, bar)" in script
    assert "foot.insertBefore(note, tools)" in script
    note = styles.split(".view-play .view-note {")[1].split("}")[0]
    assert "position: absolute;" in note and "bottom: 104px;" in note


def test_шестерёнка_открывает_список_скоростей(attachments):
    """Долгую запись смотрят быстрее, а неразборчивую — медленнее."""
    page = report([shot(attachments("56.mp4"), file_type="video")])

    for value, _, title in html_report.VIEW_RATES:
        assert f'data-rate="{value}"' in page
        assert title in page
    assert "film.playbackRate = value" in page
    # Выбранную скорость отмечает галочка, и держится она до следующей смены
    assert "row.classList.toggle('picked', Number(row.dataset.rate) === value)" in page
    assert "paced(pace)" in page


def test_остров_ищет_то_что_просмотр_рисует():
    """Переименуй класс в разметке — и кнопки молча перестанут работать."""
    for selector in re.findall(r"querySelector\('\.([a-z-]+)'\)", html_report.VIEW_SCRIPT):
        assert re.search(rf'class="[^"]*\b{selector}\b', html_report.VIEWER), selector


def test_скрипт_видео_ищет_то_что_страница_рисует(attachments):
    """Переименуй класс в разметке — и щелчок молча перестанет включать звук."""
    page = report([shot(attachments("54.mp4"), file_type="video")])

    for selector in re.findall(r"querySelector(?:All)?\('([^']+)'\)", html_report.CLIPS_SCRIPT):
        if selector.startswith("."):
            assert f'class="{selector[1:]}"' in page, selector


def test_тяжёлое_фото_остаётся_строкой_с_причиной(attachments, monkeypatch):
    """Страницу шлёт бот, а он не отправит тяжелее 50 МБ — запас важнее полноты."""
    monkeypatch.setattr(html_report, "EMBED_FILE_LIMIT", 5)

    page = report([shot(attachments("53.jpg", b"jpeg-bytes"))])

    assert "base64" not in page
    assert "не поместился" in page


def test_снимки_листаются_стрелками():
    """Иначе каждое фото приходится открывать и закрывать по отдельности."""
    script = html_report.VIEW_SCRIPT

    assert "event.key === 'Escape'" in script
    assert "show(at + 1)" in script and "show(at - 1)" in script


def test_подпись_идёт_под_фото(attachments):
    """Как в переписке: сначала снимок, под ним то, что к нему написали."""
    item = shot(attachments("60.jpg", b"jpeg-bytes"))
    item.text = "сервер упал"

    page = report([item])

    assert page.index('<figure class="shot">') < page.index("сервер упал")


def test_снимок_несёт_подпись_автора_и_время(attachments):
    """Просмотр один на страницу, и берёт он это только из самого снимка."""
    item = shot(attachments("61.jpg", b"jpeg-bytes"))
    item.text = "сервер упал"

    page = report([item])

    assert 'data-from="Иван (@ivan) &bull; 01.09.2026 в' in page
    assert 'data-text="сервер упал"' in page
    assert 'data-name="фото.jpg"' in page


def test_в_просмотре_есть_всё_чем_им_пользуются(attachments):
    """Закрыть, поворот, скачивание и счётчик со стрелками."""
    page = report([shot(attachments("62.jpg", b"jpeg-bytes"))])

    for act in ("close", "turn", "back", "next"):
        assert f'data-act="{act}"' in page
    assert "view-save" in page
    assert 'class="view-count"' in page
    assert 'class="view-text"' in page


def test_стрелка_есть_только_туда_куда_есть_куда_листать():
    """У первого снимка нет левой, у последнего правой, у единственного обеих."""
    script = html_report.VIEW_SCRIPT

    assert "box.classList.toggle('at-start', at === 0)" in script
    assert "box.classList.toggle('at-end', at === shots.length - 1)" in script
    assert "(movie ? 'Видео ' : 'Фото ') + (at + 1) + ' из ' + shots.length" in script
    assert ".lightbox.at-start .view-back" in html_report.STYLES


def test_длинная_подпись_не_лезет_на_снимок():
    """Под подпись и кнопки в просмотре отведена своя полоса, снимок в неё не входит."""
    styles = html_report.STYLES
    script = html_report.VIEW_SCRIPT

    assert "padding: var(--band, 56px) 70px;" in styles
    assert "overflow-y: auto;" in styles
    # Потолок плашки в стилях и в скрипте один и тот же, иначе замер разойдётся с видом
    assert "max-height: 132px;" in styles
    assert "var CAP = 132;" in script
    # Полоса под подпись со стрелкой, но не уже места под кнопки сверху
    assert "Math.max(TOP, height + 76)" in script


def test_текст_подписи_стоит_на_месте():
    """Плашка липнет к тексту, но от полосы прокрутки он ездил вбок."""
    styles = html_report.STYLES

    # Пара слов — маленькая плашка, длинная подпись упирается в половину экрана
    assert "flex: 0 1 auto;" in styles
    assert "max-width: 50%;" in styles
    assert "scrollbar-gutter: stable;" in styles


def test_лупы_на_странице_нет():
    """Она сбивала с толку и на снимке в переписке, и на сером фоне просмотра."""
    styles = html_report.STYLES

    assert "zoom-in" not in styles
    assert "zoom-out" not in styles


def test_сверху_в_просмотре_только_крестик():
    """Чёрточка повторяла крестик, а квадрат уводил в полный экран без нужды."""
    for act in ("hide", "size"):
        assert f'data-act="{act}"' not in html_report.VIEWER
    # Снимок разворачивать во весь экран незачем, а вот видео — бывает нужно
    assert html_report.VIEWER.count('data-act="close"') == 1
    assert html_report.VIEWER.count('data-act="wide"') == 1


def test_снимок_в_просмотре_не_плющит():
    """С заданной высотой и потолком по ширине браузер сминал повёрнутый снимок."""
    rule = html_report.STYLES.split(".lightbox img, .view-play video {")[1].split("}")[0]

    # Только потолки: заданная ширина или высота ломает пропорции при повороте
    assert "width:" not in rule.replace("max-width:", "")
    assert "height:" not in rule.replace("max-height:", "")


def test_пузырь_со_снимком_меряется_по_снимку(attachments):
    """Иначе длинная подпись растягивает пузырь, и снимок сидит в его углу."""
    item = shot(attachments("65.jpg", b"jpeg-bytes"))
    item.text = "Подпись к снимку " * 20
    styles = html_report.STYLES

    assert '<article class="msg by-shot"' in report([item])
    assert "width: min-content;" in styles
    # Кадр от края до края: поля пузыря достаются имени и подписи, но не ему
    assert ".msg.by-shot > :not(.shot):not(.clip)," in styles
    assert ".msg.by-film > :not(.clip) { padding-left: 12px; padding-right: 12px; }" in styles
    assert ".msg.by-shot .shot img, .msg.by-shot .film, .msg.by-shot .clip video" in styles
    # Сообщению без снимка мерить себя не по чему, пузырь остаётся по тексту
    assert '<article class="msg"' in report([message()])


def test_у_снимка_троеточия_нет(attachments):
    """Скачивают снимок стрелкой из просмотра, второй кнопки для этого не нужно."""
    page = report([shot(attachments("64.jpg", b"jpeg-bytes"))])

    assert 'class="more"' not in page
    assert 'class="sheet"' not in page


def test_снимок_в_переписке_такой_каким_снят():
    """Общая ширина в 300px растягивала мелкие снимки и жала крупные."""
    styles = html_report.STYLES

    # Рамка по снимку, а не наоборот: иначе он не по центру подписи в просмотре
    assert ".shot { position: relative; margin: 8px 0 0; width: fit-content;" in styles
    assert "max-height: 480px;" in styles
    # Потолок в пикселях: в проценты пузырь, меряясь по снимку, подставит ноль
    assert "max-width: 480px;" in styles
    assert "@media (max-width: 560px) {" in styles


def test_снимок_стоит_посередине_экрана():
    """С широким низом и узким верхом снимок сидел заметно выше середины."""
    script = html_report.VIEW_SCRIPT

    # Одна и та же полоса сверху и снизу: экран делится поровну вокруг снимка
    assert "padding: var(--band, 56px) 70px;" in html_report.STYLES
    assert "box.style.setProperty('--band', band + 'px')" in script


def test_поворот_не_меняет_размер_снимка():
    """В Telegram кадр от поворота только разворачивается, размер у него прежний."""
    # Своих размеров у повёрнутого снимка нет: пересчёт и ужимал его до полоски
    assert ".lightbox.turned" not in html_report.STYLES
    assert "'turned'" not in html_report.VIEW_SCRIPT


def test_подпись_убирается_стрелкой():
    """Длинная подпись закрывает низ экрана — её прячут щелчком по стрелке."""
    assert 'data-act="fold"' in html_report.VIEWER
    assert "box.classList.toggle('folded', !open)" in html_report.VIEW_SCRIPT
    # Без подписи прятать нечего, и стрелки быть не должно
    assert "note.hidden = !shot.dataset.text" in html_report.VIEW_SCRIPT


def test_снимок_не_прыгает_от_убранной_подписи():
    """Полоса под подпись остаётся занятой, а плашка уезжает вниз плавно."""
    styles = html_report.STYLES
    script = html_report.VIEW_SCRIPT

    # Полосу меряют по самой подписи, а не по тому, убрали её стрелкой или нет
    assert "var band = movie || note.hidden ? TOP : Math.max(TOP, height + 76)" in script
    # Высоту меряют по содержимому: от потолка в 112px короткая подпись дёргалась бы
    assert "Math.min(full, CAP)" in script
    assert "transition: max-height .25s ease" in styles
    assert ".view-fold svg { transition: transform .25s ease; }" in styles
    # У скрытого окна высота строки нулевая, и подпись не открылась бы вовсе
    assert script.index("classList.add('open')") < script.index("tell(!box.classList")


def test_открытая_подпись_при_листании_не_выезжает_заново():
    """Она уже открыта: ехать ей надо только от стрелки, а не от каждого снимка."""
    script = html_report.VIEW_SCRIPT

    assert "caption.style.transition = quick ? 'none' : ''" in script
    assert "tell(!box.classList.contains('folded'), true)" in script
    # От стрелки — обычным ходом, с переходом
    assert "tell(box.classList.contains('folded'))" in script


def test_у_короткой_подписи_полосы_прокрутки_нет():
    """Замер по самой плашке рос с каждым открытием, и у пары слов вылезал ползунок."""
    script = html_report.VIEW_SCRIPT

    # Строка внутри плашки: её высоту потолок плашки не подрезает, замер не плывёт
    assert '<div class="view-text"><div class="view-line"></div></div>' in html_report.VIEWER
    assert "line.getBoundingClientRect().height" in script
    assert "caption.scrollHeight" not in script
    assert "caption.style.overflowY = full > CAP ? 'auto' : 'hidden'" in script


def test_снимок_разворачивается_по_четверти():
    """Четыре нажатия на кнопку поворота возвращают снимок в исходное положение."""
    assert "turn = (turn + 90) % 360" in html_report.VIEW_SCRIPT
    assert "rotate(' + turn + 'deg)" in html_report.VIEW_SCRIPT
