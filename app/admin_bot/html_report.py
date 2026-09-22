"""Выгрузка переписки страницей для чтения глазами.

Данные те же, что в JSON, и хронология та же — из `chronology()`. Разница в
назначении: JSON отдают системе, HTML открывают и читают, поэтому здесь
переписка выглядит перепиской, а не деревом полей.

Четыре вещи, которые в JSON читаются плохо, тут показаны по-человечески:
ответ становится цитатой со ссылкой на оригинал, правки — раскрывающимся
списком прежних версий, пересылка — строчкой над сообщением, служебные события
— серыми строками по центру, как в самом Telegram.

Страница получается одним файлом: ни внешних стилей, ни запросов в сеть.
Вложения вшиты в неё целиком — звук играет, фото открывается во весь экран,
документ сохраняется щелчком. Запас на это ограничен: бот не отправит файл
тяжелее 50 МБ, поэтому что не поместилось, остаётся строкой с причиной, а сам
файл присылает `/files` за тот же период.

Скрипты на странице есть, но только ради плееров: кружок включается щелчком
и перематывается кольцом по ободу, голосовое рисует дорожку звука по самой
записи, а под троеточием у обоих скачивание и скорость. Готовых кнопок у них
нет — в переписке их тоже не бывает. В выгрузку попадают лишь те скрипты, без
которых её плееры не заработают: страница с одним текстом обходится без них.

Весь текст здесь пользовательский, поэтому в разметку он попадает только через
escape: иначе достаточно написать в чат `<script>`, чтобы сломать выгрузку.
"""

import logging
from base64 import b64encode
from datetime import datetime
from html import escape
from pathlib import PurePosixPath
from zlib import crc32

from app.admin_bot.handlers.common import (
    DATETIME_FORMAT,
    display_timezone,
    file_type_label,
    format_date,
    format_datetime,
    format_duration,
    format_size,
    format_time,
    sender_name,
)
from app.admin_bot.serialize import (
    FILE_NOT_SAVED,
    FORWARD_ORIGIN_LABELS,
    NO_TEXT,
    REPLY_PREVIEW_LIMIT,
    UNKNOWN_SENDER,
    chronology,
    event_label,
    moment,
)
from app.db.models import Message
from app.paths import resolve_attachment

logger = logging.getLogger(__name__)

# Цвета имён — как в Telegram, чтобы в длинной переписке авторы различались
# взглядом. Тёмные: имя стоит на белом пузыре
# Имена на тёмном фоне: цвета светлые, как в ночной теме Telegram
PALETTE = (
    "#71bdff",
    "#ff8e86",
    "#7fd6a2",
    "#ffb27d",
    "#c7a6ff",
    "#72d4d4",
)

ATTACHMENTS_NOTE = (
    "Вложения вшиты в страницу: их видно и слышно без интернета, а щелчок по "
    "троеточию сохраняет файл. Что не поместилось, присылает команда /files "
    "архивом за тот же период."
)

# Каждому типу — свой вид: звук пузырём с дорожкой, кружок кружком, фото
# картинкой, видео плеером, остальное карточкой со скачиванием
PLAYER_TYPES = ("voice", "audio", "video_note")
PICTURE_TYPES = ("photo", "sticker")
CLIP_TYPES = ("video", "animation")

# base64 раздувает файл на треть, а бот не отправит больше 50 МБ. Поэтому
# вшиваем, пока укладываемся в запас, и не даём одному тяжёлому файлу съесть
# его целиком: что не влезло, останется строкой со ссылкой на архив
EMBED_FILE_LIMIT = 12 * 1024 * 1024
EMBED_TOTAL_LIMIT = 28 * 1024 * 1024

# Тип нужен браузеру, чтобы показать файл, а не предложить его скачать
MEDIA_TYPES = {
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    # Снятое на телефон приходит в .mov, но video/quicktime браузеры играть
    # отказываются. Внутри тот же H.264, что и в mp4, и под этим типом он идёт
    ".mov": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Документ показать нельзя, его можно только сохранить, и тип тут любой
ANY_FILE = "application/octet-stream"

IN_ARCHIVE = "файл в архиве /files"
FILE_MISSING = "файла нет на диске"
TOO_HEAVY = "не поместился в страницу, файл в архиве /files"

STYLES = """
  :root { color-scheme: dark; }
  body {
    margin: 0;
    padding: 24px 16px 64px;
    /* Ночная тема Telegram: тёмное полотно, а переписка — пузырями поверх него */
    background: #0e1621;
    color: #e9edf0;
    font: 15px/1.45 -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    /* Подсветка ответа идёт во всю ширину окна, а полоса прокрутки ещё и
       отнимает её часть: без этого узкое окно ездит вбок, и пузыри с подсветкой
       разъезжаются в разные стороны. clip надёжнее hidden, но знают его не все */
    overflow-x: hidden;
    overflow-x: clip;
    /* Переписку щипком не приближают: от этого она и ездила в стороны. Листать
       её при этом можно как обычно, а приближают уже сам снимок в просмотре */
    touch-action: pan-y;
  }
  .page { max-width: 760px; margin: 0 auto; }
  .head {
    background: #17212b;
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 20px;
  }
  .head h1 { margin: 0 0 6px; font-size: 20px; }
  .head dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; margin: 0; }
  .head dt { color: #708499; }
  .head dd { margin: 0; }
  .head .hint { margin: 12px 0 0; color: #708499; font-size: 13px; }
  /* Дата дня и служебные события — плашками посередине, как в чате, и обе
     светлее полотна: тёмная на тёмном с ним попросту сливалась */
  .day {
    width: fit-content;
    max-width: 90%;
    margin: 22px auto 12px;
    padding: 4px 12px;
    border-radius: 12px;
    background: rgba(26, 42, 58, .92);
    color: #d3e2f0;
    font-size: 13px;
    font-weight: 600;
  }
  .event {
    width: fit-content;
    max-width: 90%;
    margin: 10px auto;
    padding: 4px 12px;
    border-radius: 12px;
    background: rgba(26, 42, 58, .92);
    text-align: center;
    color: #d3e2f0;
    font-size: 13px;
  }
  .event time { color: #9db3c8; }
  /* Строка во всю колонку: по ней и идёт подсветка, когда прыгают из цитаты */
  .row { position: relative; }
  .msg {
    /* Пузырь по содержимому, а не во всю колонку: короткой строке ширина ни к чему */
    width: fit-content;
    max-width: 100%;
    box-sizing: border-box;
    background: #182533;
    border-radius: 14px;
    padding: 8px 12px;
    margin: 8px 0;
  }
  /* Со снимком и видео пузырь меряется по кадру: тот идёт от края до края, без
     полей по бокам, а имя с подписью отступают от краёв сами */
  .msg.by-shot, .msg.by-film {
    width: min-content;
    padding-left: 0;
    padding-right: 0;
    /* Скруглением пузыря и обрезаем кадр, поэтому своего у него больше нет */
    overflow: hidden;
  }
  .msg.by-shot > :not(.shot):not(.clip),
  .msg.by-film > :not(.clip) { padding-left: 12px; padding-right: 12px; }
  .msg.by-shot .shot img, .msg.by-shot .film, .msg.by-shot .clip video,
  .msg.by-film .film, .msg.by-film .clip video { border-radius: 0; }
  .msg.by-shot .text { margin-top: 6px; }
  /* Шире кадр делают только ради подписи: по его ширине она и переносится,
     а без подписи расширять его незачем — кадр остаётся как в переписке */
  .msg.by-shot .clip { width: 360px; }
  /* У кадра с подписью время идёт после текста, и на кадре оно уже ни к чему */
  .msg.by-shot .film-when { display: none; }
  /* А под кадром без подписи писать нечего: пузырь кончается ровно на нём */
  .msg.by-film { padding-bottom: 0; }
  /* Прыжок из цитаты подсвечивает сообщение и отпускает, как в Telegram.
     Подсветка идёт полосой от края до края экрана, а не по одному пузырю */
  .row:target::after, .row.flash::after {
    content: "";
    position: absolute;
    top: -4px;
    bottom: -4px;
    left: 50%;
    width: 100vw;
    margin-left: -50vw;
    background: rgba(51, 144, 236, .22);
    pointer-events: none;
    /* До анимации полоса не видна, иначе она моргнёт в полную силу */
    opacity: 0;
    animation: flash 2.6s ease-in-out forwards;
  }
  /* Подсветка разгорается, держится и так же плавно гаснет */
  @keyframes flash {
    0% { opacity: 0; }
    25%, 50% { opacity: 1; }
    100% { opacity: 0; }
  }
  /* Время прижато к правому нижнему углу и встаёт в конец последней строки:
     своей строки оно не занимает, иначе пузырь вырастает на ровном месте */
  .stamp { float: right; margin: 4px 0 0 10px; }
  .stamp .edited { margin-right: 6px; }
  /* Без текста приклеиться не к чему, и время идёт отдельной строкой */
  .stamp.alone { float: none; display: block; text-align: right; margin: 2px 0 0; }
  /* Прежние версии правок обтекать время не должны */
  .edits { clear: both; }
  .msg header { display: flex; gap: 8px; align-items: baseline; margin-bottom: 4px; }
  .author { font-weight: 600; }
  .msg time, .edited { color: #708499; font-size: 13px; }
  /* Полоса слева и полупрозрачная подложка под ней, как в переписке.
     Справа углы скруглены, слева их держит прямыми сама полоса */
  .forward, .reply {
    display: block;
    border-left: 3px solid #3390ec;
    border-radius: 3px 8px 8px 3px;
    padding: 3px 8px;
    margin: 0 0 6px;
    background: rgba(51, 144, 236, .16);
  }
  /* Цитату читают наравне с ответом, поэтому кегль у неё общий с текстом.
     А пометка о пересылке — служебная строка, ей мелкий шрифт и положен */
  .forward {
    border-left-color: #4b5a6b;
    background: rgba(255, 255, 255, .06);
    color: #a3b1bf;
    font-size: 13px;
  }
  /* Цитата в строку: слева мини-кадр, справа имя и сама строка. Ширину ей
     ограничиваем, иначе ответ на длинное сообщение растягивает пузырь во всю
     страницу, а пузырь по снимку — и вовсе за её край */
  .reply {
    display: flex;
    gap: 8px;
    align-items: center;
    max-width: 440px;
    color: inherit;
    text-decoration: none;
  }
  /* Без этого длинная строка не даёт себя обрезать: флекс её не ужимает */
  .reply-body { min-width: 0; }
  .reply-author, .reply-text, .reply-kind {
    display: block;
    /* Длинную цитату в переписке обрывают троеточием, а не тянут за край */
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  /* Ширину цитате задаёт имя автора, а строка сообщения обрывается по нему.
     Нулевая ширина с потолком в сотню процентов — как раз про это: в расчёт
     ширины строка не идёт, а по готовой растягивается до имени */
  .reply-text, .reply-kind { width: 0; min-width: 100%; }
  .reply-author { font-weight: 600; color: #71bdff; }
  /* Текст цитаты читают наравне с ответом, поэтому он такой же белый */
  .reply-text { color: #e9edf0; }
  /* А вид вложения — не чьи-то слова, и цветом он ближе к имени, как в чате */
  .reply-kind { color: #71bdff; }
  /* Кадр цитате подставляет скрипт у самого сообщения: вшивать те же байты
     второй раз незачем, а у кружка он к тому же круглый */
  .reply-mini {
    flex: none;
    width: 34px;
    height: 34px;
    border-radius: 6px;
    background: #0f172a center / cover no-repeat;
  }
  .reply-mini.round { border-radius: 50%; }
  /* А пропажу оригинала приглушаем: это не чьи-то слова, а пояснение */
  .reply-lost { color: #8fa3b5; }
  .text { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
  .no-text { color: #708499; }
  .files { margin: 8px 0 0; padding-left: 18px; color: #c5d0da; font-size: 13px; }
  .files .lost { color: #ff8e86; }
  /* Фото прямо в переписке, щелчок разворачивает его во весь экран */
  .shot { position: relative; margin: 8px 0 0; width: fit-content; max-width: 100%; }
  .clip { position: relative; margin: 8px 0 0; width: 300px; max-width: 100%; }
  .shot img, .clip video {
    display: block;
    border-radius: 14px;
    background: #0f172a;
  }
  .clip video { width: 100%; }
  /* Видео ведёт себя как кружок, только в прямоугольнике: своих кнопок у него
     нет, а дорожка и таблички лежат поверх кадра и обрезаются его углами */
  .film { position: relative; overflow: hidden; border-radius: 14px; }
  .film video { border-radius: 0; cursor: pointer; }
  /* Одна табличка на всё сразу: счётчик, немой динамик и крестик в ряд */
  .film-badge {
    position: absolute;
    top: 10px;
    left: 10px;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 8px;
    border-radius: 12px;
    background: rgba(15, 23, 42, .65);
    color: #fff;
    font-size: 12px;
  }
  /* Значок немого показывает, что звук придёт со щелчком, — как у кружка */
  .film-mute { display: flex; pointer-events: none; }
  .film-mute svg { width: 15px; height: 15px; fill: #fff; }
  .film-mute .cross { fill: none; stroke: #fff; stroke-width: 2; stroke-linecap: round; }
  /* Дорожка идёт по нижнему краю от угла до угла и ведёт немой прокрут */
  .film-line {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 3px;
    background: rgba(255, 255, 255, .28);
  }
  .film-bar { width: 0; height: 100%; background: #3390ec; }
  .shot img {
    /* Снимок такой, каким снят, только не крупнее этих границ: мелкий растягивать
       незачем — он станет мыльным. Потолок в пикселях, без процентов: пузырь
       меряется по снимку, а проценты в таком расчёте считаются нулём */
    max-width: 480px;
    max-height: 480px;
    cursor: pointer;
  }
  /* На узком экране снимку не до своей ширины: пузырь растягивается во всю
     колонку, и кадр ужимается по ней */
  @media (max-width: 560px) {
    .msg.by-shot, .msg.by-film { width: auto; }
    .shot img { max-width: 100%; }
  }
  .clip .more { position: absolute; right: -30px; bottom: 0; }
  .clip .sheet { right: -30px; top: 100%; }
  /* Документ строкой прямо в пузыре: своей плашки ему не надо, а кружок
     со значком начинается там же, где имя автора, — как в переписке */
  .doc {
    display: flex;
    gap: 10px;
    align-items: center;
    margin: 6px 0 0;
    max-width: 100%;
    box-sizing: border-box;
    color: inherit;
    text-decoration: none;
  }
  .doc-icon {
    flex: none;
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: #3390ec;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .doc-icon svg { width: 22px; height: 22px; fill: #fff; }
  .doc-body { min-width: 0; }
  .doc-name { display: block; overflow-wrap: anywhere; }
  /* Вес файла у самой строки крупнее прочих подписей — как в переписке */
  .doc .meta { font-size: 14px; }
  /* Время встаёт вровень с размером файла, а не строкой под карточкой */
  .msg.by-doc { position: relative; }
  .msg.by-doc .doc { padding-right: 46px; }
  .msg.by-doc .stamp.alone {
    position: absolute;
    right: 12px;
    bottom: 8px;
    margin: 0;
  }
  /* Щелчок по файлу сразу кладёт его на диск, поэтому сперва спрашиваем */
  .ask {
    position: fixed;
    inset: 0;
    z-index: 30;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, .6);
  }
  .ask[hidden] { display: none; }
  .ask-box {
    width: 340px;
    max-width: calc(100% - 32px);
    box-sizing: border-box;
    padding: 20px;
    border-radius: 14px;
    background: #17212b;
  }
  .ask-head { margin: 0; font-weight: 600; }
  .ask-name { margin: 8px 0 0; overflow-wrap: anywhere; }
  .ask-size { margin: 2px 0 0; color: #8fa3b5; font-size: 14px; }
  .ask-row { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
  .ask-row button {
    padding: 8px 14px;
    border: 0;
    border-radius: 8px;
    background: none;
    color: #3390ec;
    font: inherit;
    font-weight: 600;
    cursor: pointer;
  }
  .ask-row button:hover { background: rgba(51, 144, 236, .12); }
  /* Фото во весь экран: поверх всей страницы, закрывается щелчком или Esc */
  .lightbox {
    position: fixed;
    inset: 0;
    display: none;
    align-items: center;
    justify-content: center;
    /* Поля сверху и снизу равны, иначе снимок сидит выше середины экрана.
       Ширину полосы считает скрипт: ровно столько, сколько занимает подпись */
    padding: var(--band, 56px) 70px;
    box-sizing: border-box;
    background: rgba(60, 64, 72, .88);
    z-index: 5;
    /* А вот снимок и кадр в просмотре приближать можно: за ними и открывают */
    touch-action: auto;
  }
  .lightbox.open { display: flex; }
  /* Видео смотрят тем же окном, что и фото, и места ему отведено столько же.
     Кадру его считают по самому экрану: от полей окна проценты тут пошли бы
     по кругу — кадр меряется по обёртке, а обёртка по кадру */
  .lightbox img, .view-play video {
    max-width: 100%;
    max-height: 100%;
    border-radius: 4px;
  }
  /* Кадр и остров идут столбиком: на самом видео кнопки закрывали бы картинку */
  .view-play {
    position: relative;
    display: flex;
    flex-direction: column;
    flex: 0 1 auto;
    min-width: 0;
    gap: 8px;
  }
  .view-play video {
    display: block;
    margin: 0 auto;
    max-width: calc(100vw - 140px);
    /* Высоту кадр делит с островом, иначе столбик не влезет в окно */
    max-height: calc(100vh - var(--band, 56px) * 2 - 92px);
  }
  .view-play[hidden], .lightbox img[hidden] { display: none; }
  /* В полный экран уходит сам кадр с островом, а не всё окно просмотра */
  .view-play:fullscreen { justify-content: center; background: #000; padding: 0 0 10px; }
  .view-play:fullscreen video { max-width: 100vw; max-height: calc(100vh - 100px); }
  .view-top { position: absolute; top: 10px; right: 14px; display: flex; gap: 2px; }
  .view-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 36px;
    height: 36px;
    border: none;
    border-radius: 50%;
    background: none;
    color: #fff;
    cursor: pointer;
  }
  .view-btn:hover { background: rgba(255, 255, 255, .16); }
  .view-step { position: absolute; top: 50%; transform: translateY(-50%); width: 48px; height: 48px; }
  .view-back { left: 18px; }
  .view-next { right: 18px; }
  /* У первого снимка листать некуда назад, у последнего — вперёд */
  .lightbox.at-start .view-back,
  .lightbox.at-end .view-next { display: none; }
  .view-tools .view-btn { width: 44px; height: 44px; }
  .view-foot {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 16px;
    display: flex;
    gap: 16px;
    align-items: flex-end;
    padding: 0 20px;
    /* Мимо кнопок и подписи щелчок должен закрывать просмотр */
    pointer-events: none;
  }
  .view-who {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: 12px;
  }
  .view-count { color: #fff; font-weight: 600; }
  .view-from { color: #cbd5e1; }
  .view-note {
    /* Плашка по тексту: паре слов широкая ни к чему, длинная подпись упрётся
       в половину экрана и дальше пойдёт строками */
    flex: 0 1 auto;
    max-width: 50%;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    pointer-events: auto;
  }
  .view-note[hidden] { display: none; }
  .view-fold { width: 28px; height: 28px; background: rgba(0, 0, 0, .45); }
  .view-fold:hover { background: rgba(0, 0, 0, .7); }
  /* Подпись убирают стрелкой, когда она закрывает нужное на снимке */
  .view-fold svg { transition: transform .25s ease; }
  .lightbox.folded .view-fold svg { transform: rotate(180deg); }
  .lightbox.folded .view-text {
    /* Плашка уезжает вниз за стрелкой, а не пропадает разом */
    max-height: 0;
    padding-top: 0;
    padding-bottom: 0;
    opacity: 0;
    pointer-events: none;
  }
  .view-text {
    /* Широкая плашка вместо узкой и высокой: длинная подпись не лезет на снимок */
    max-width: 100%;
    max-height: 132px;
    overflow-y: auto;
    /* Место под полосу прокрутки держат всегда, иначе от неё текст дёргается вбок */
    scrollbar-gutter: stable;
    scrollbar-width: thin;
    scrollbar-color: rgba(255, 255, 255, .45) transparent;
    padding: 8px 14px;
    border-radius: 12px;
    background: rgba(0, 0, 0, .45);
    color: #fff;
    font-size: 14px;
    overflow-wrap: anywhere;
    pointer-events: auto;
    transition: max-height .25s ease, padding .25s ease, opacity .25s ease;
  }
  /* Строка внутри плашки: её высоту меряет скрипт, и потолок плашки ей не мешает */
  .view-line { overflow-wrap: anywhere; }
  .view-tools { flex: 1 1 0; display: flex; justify-content: flex-end; gap: 2px; pointer-events: auto; }
  /* Остров под кадром: кнопки браузера у всех свои, а эти везде одинаковы.
     Идёт он после общих правил кнопок, иначе те переспорят его размеры */
  /* Подпись к видео висит поверх нижнего края кадра, над самым островом:
     своей полосы ей не отводят, иначе кадр с подписью выходит меньше */
  .view-play .view-note {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 104px;
    max-width: 100%;
  }
  /* Крестик окна в полный экран не уходит, поэтому свой — в углу кадра.
     Правило идёт после общих, иначе кнопка так и осталась бы видимой */
  .view-shut { position: absolute; top: 10px; right: 14px; display: none; }
  .view-play:fullscreen .view-shut { display: flex; }
  /* Ширина у острова своя и постоянная: в полный экран он растягивался
     во всё окно, а кнопки разъезжались по углам */
  .view-bar {
    position: relative;
    align-self: center;
    width: 360px;
    max-width: 100%;
    box-sizing: border-box;
    padding: 6px 14px 10px;
    border-radius: 14px;
    background: rgba(13, 13, 13, .82);
    color: #fff;
  }
  /* Три полосы поровну: знак «играть» встаёт ровно по середине кадра */
  .bar-top { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; }
  .bar-loud { display: flex; align-items: center; gap: 10px; }
  .bar-side { display: flex; justify-content: flex-end; gap: 4px; }
  .bar-btn { width: 40px; height: 40px; }
  .bar-play { width: 52px; height: 52px; }
  /* Знак у кнопки меняется по ходу дела: их два, и видно всегда только один */
  .bar-btn .sign-off,
  .view-play.playing .bar-play .sign-on,
  .view-play.quiet .bar-loud .sign-on { display: none; }
  .view-play.playing .bar-play .sign-off,
  .view-play.quiet .bar-loud .sign-off { display: block; }
  .bar-vol {
    -webkit-appearance: none;
    appearance: none;
    width: 86px;
    height: 3px;
    border-radius: 2px;
    background: rgba(255, 255, 255, .35);
    cursor: pointer;
  }
  .bar-vol::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 11px;
    height: 11px;
    border-radius: 50%;
    background: #fff;
  }
  .bar-vol::-moz-range-thumb {
    border: 0;
    width: 11px;
    height: 11px;
    border-radius: 50%;
    background: #fff;
  }
  .bar-line { display: flex; align-items: center; gap: 12px; font-size: 13px; }
  /* Дорожка тонкая, а хватать её надо мышью: жила у неё шире самой полосы */
  .bar-track {
    flex: 1;
    padding: 7px 0;
    cursor: pointer;
    touch-action: none;
    background: linear-gradient(rgba(255, 255, 255, .35), rgba(255, 255, 255, .35))
      0 50% / 100% 3px no-repeat;
  }
  .bar-fill { height: 3px; width: 0; border-radius: 2px; background: #fff; }
  /* Скорости раскрываются над шестерёнкой: под островом для них места нет */
  .bar-rate {
    position: absolute;
    right: 10px;
    bottom: calc(100% + 8px);
    padding: 6px;
    border-radius: 12px;
    background: #1f2733;
    box-shadow: 0 8px 24px rgba(0, 0, 0, .45);
  }
  .bar-rate[hidden] { display: none; }
  .rate-row {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    padding: 7px 10px;
    border: 0;
    border-radius: 8px;
    background: none;
    color: #e9edf0;
    font: inherit;
    white-space: nowrap;
    cursor: pointer;
  }
  .rate-row:hover { background: rgba(255, 255, 255, .08); }
  /* Число слева от названия, и колонка под него всегда одной ширины */
  .rate-mark { width: 38px; text-align: right; font-weight: 600; font-size: 13px; }
  .rate-name { flex: 1; text-align: left; }
  /* Галочку видно только у выбранной скорости, место под неё держат всегда */
  .rate-tick { visibility: hidden; color: #3390ec; }
  .rate-row.picked { color: #3390ec; }
  .rate-row.picked .rate-tick { visibility: visible; }
  /* Голосовое строкой прямо в пузыре: своей плашки ей не надо, а кнопка
     начинается там же, где имя автора, — как в переписке */
  .voice {
    position: relative;
    display: flex;
    gap: 10px;
    align-items: center;
    margin: 6px 0 0;
    width: 300px;
    max-width: 100%;
    box-sizing: border-box;
  }
  /* Время встаёт вровень с длиной записи, а троеточие уходит к верхнему краю:
     нижний угол теперь занят временем */
  .msg.by-voice { position: relative; }
  .msg.by-voice .voice { padding-right: 34px; }
  /* Время встаёт под самым концом дорожки, слева от троеточия */
  .msg.by-voice .stamp.alone {
    position: absolute;
    right: 46px;
    bottom: 8px;
    margin: 0;
  }
  /* Кружок висит прямо на полотне: в переписке его пузырём не обводят. Поля
     пузыря ему всё же оставлены, иначе он и имя съезжают левее всей переписки */
  .msg.by-note { background: none; }
  /* Время у кадра и кружка уже стоит табличкой на картинке */
  .msg.by-note .stamp.alone, .msg.by-film .stamp.alone { display: none; }
  /* Пометка о пересылке у всего, что показано картинкой, идёт сбоку, а не над
     ней. Из потока её вынимаем: иначе она растягивает пузырь вместе со снимком,
     и время съезжает следом. У кружка с записью сбоку встаёт сама пометка, а у
     снимка с кадром — сбоку от всего пузыря: внутри его обрезает скругление */
  .aside { position: relative; width: fit-content; max-width: 100%; }
  .aside .forward {
    position: absolute;
    left: calc(100% + 10px);
    top: 0;
    width: max-content;
    /* Шире плашку делать нечего, но и уже нельзя: в узкой длинное имя
       источника расходится строк на пять и свисает ниже самого сообщения */
    max-width: 240px;
    line-height: 1.3;
    margin: 0;
  }
  /* Сбоку от снимка пометке нужно ещё двести точек, и на узком экране их взять
     негде — там она возвращается на своё обычное место над пузырём */
  @media (max-width: 760px) {
    .aside { display: flex; flex-direction: column-reverse; }
    .aside .forward {
      position: static;
      width: auto;
      max-width: 100%;
      margin: 0 0 6px;
    }
  }
  .voice .icon, .voice-play {
    flex: none;
    width: 40px;
    height: 40px;
    padding: 0;
    border: none;
    border-radius: 50%;
    background: #3390ec;
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .voice-play { cursor: pointer; }
  .voice-play svg { width: 20px; height: 20px; fill: #fff; }
  /* Кнопка одна, а знака два: на ходу треугольник сменяется паузой */
  .voice-play .pause, .voice.playing .voice-play .play { display: none; }
  .voice.playing .voice-play .pause { display: block; }
  .voice-body { flex: 1; min-width: 0; }
  /* Дорожку рисует скрипт по самой записи, поэтому у неё только размеры */
  .voice-wave {
    display: block;
    width: 100%;
    height: 24px;
    cursor: pointer;
    touch-action: none;
  }
  .voice audio { display: none; }
  .meta, .voice-line { display: block; color: #8fa3b5; font-size: 12px; margin-top: 2px; }
  /* Длина записи и её вес — вровень со временем рядом, и жмутся к дорожке */
  .voice-line { font-size: 13px; margin-top: 0; }
  .off { color: #ff8e86; font-size: 13px; }
  /* Кружок и остаётся кружком: видео обрезано по кругу, слева, как в переписке */
  /* Подпись держим по ширине картинки, чтобы она стояла под её серединой */
  .circle { margin: 8px 0 0; width: 220px; }
  .circle .meta { text-align: center; }
  .note { position: relative; width: 220px; height: 220px; }
  .note-video, .circle-off {
    width: 100%;
    height: 100%;
    border-radius: 50%;
    object-fit: cover;
    background: #0f172a;
  }
  .note-video { display: block; cursor: pointer; transition: filter .18s ease; }
  /* Картинка притухает, пока смотрят не на неё, а на кольцо */
  .note.paused .note-video, .note.seeking .note-video { filter: brightness(.55); }
  .circle-off {
    width: 220px;
    height: 220px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0 24px;
    box-sizing: border-box;
    color: #e2e8f0;
    font-size: 13px;
    text-align: center;
  }
  /* Кольцо прокрутки по ободу: показывает, сколько проиграно, и перематывает */
  .ring {
    position: absolute;
    inset: 0;
    pointer-events: none;
    /* Бегунок выходит за край кольца: без этого его срезает по краю картинки */
    overflow: visible;
    opacity: 0;
    transform-origin: 50% 50%;
    transition: opacity .18s ease, transform .18s ease;
  }
  /* До первого щелчка кольца нет вовсе, как у непросмотренного кружка в чате */
  .note.playing .ring, .note.paused .ring, .note.seeking .ring { opacity: 1; }
  /* Кольцо сходит с обода внутрь, когда за него берутся: на паузе и на перемотке */
  .note.paused .ring, .note.seeking .ring { transform: scale(.86); }
  /* Заливку гасим поимённо: правило на все circle перебило бы белый бегунок */
  .ring-track, .ring-bar, .ring-hit { fill: none; }
  .ring-track { stroke: rgba(226, 232, 240, .45); stroke-width: 1.6; }
  .ring-bar {
    stroke: rgba(255, 255, 255, .95);
    stroke-width: 1.6;
    stroke-linecap: round;
    transition: stroke-width .18s ease;
  }
  .note.paused .ring-bar, .note.seeking .ring-bar { stroke-width: 2.4; }
  /* Бегунок заметно толще линии — иначе за него не взяться глазами и мышью */
  .ring-knob {
    fill: #fff;
    stroke: none;
    opacity: 0;
    filter: drop-shadow(0 1px 2px rgba(15, 23, 42, .45));
    transition: opacity .18s ease;
  }
  .note.paused .ring-knob, .note.seeking .ring-knob { opacity: 1; }
  /* Толстая прозрачная жила: попасть в тонкое кольцо мышью иначе тяжело */
  .ring-hit { stroke: transparent; stroke-width: 14; pointer-events: stroke; cursor: pointer; }
  /* Немой кружок помечен сверху, как в переписке: звук придёт со щелчком */
  .note-mute {
    position: absolute;
    top: 8px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    padding: 5px;
    border-radius: 50%;
    background: rgba(15, 23, 42, .65);
    transition: opacity .18s ease;
    /* Щелчок по значку должен включать кружок, а не упираться в сам значок */
    pointer-events: none;
  }
  .note-mute svg { width: 16px; height: 16px; fill: #fff; }
  .note-mute .cross { fill: none; stroke: #fff; stroke-width: 2; stroke-linecap: round; }
  .note.playing .note-mute, .note.paused .note-mute, .note.seeking .note-mute {
    opacity: 0;
  }
  /* Таблички по нижнему краю: слева ход записи, справа время сообщения */
  .note-time, .note-when, .film-when {
    position: absolute;
    bottom: 12px;
    padding: 2px 8px;
    border-radius: 10px;
    background: rgba(15, 23, 42, .65);
    color: #fff;
    font-size: 12px;
    pointer-events: none;
  }
  .note-time { left: 12px; }
  .note-when, .film-when { right: 12px; }
  /* Троеточие в самом углу, за ободом: там оно не закрывает картинку */
  /* Троеточие у записи: одно и то же и у кружка, и у голосового */
  .more {
    flex: none;
    width: 28px;
    height: 28px;
    padding: 0;
    border: none;
    border-radius: 50%;
    background: none;
    color: #8fa3b5;
    font-size: 20px;
    line-height: 1;
    cursor: pointer;
  }
  .more:hover { background: rgba(255, 255, 255, .12); }
  /* Крестик останавливает запись. Виден он с запуска и до конца: пауза его не
     убирает — остановиться хочется чаще как раз с паузы */
  .stop {
    position: absolute;
    width: 28px;
    height: 28px;
    padding: 0;
    border: none;
    border-radius: 50%;
    background: none;
    color: #8fa3b5;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    pointer-events: none;
    transition: opacity .15s ease;
    cursor: pointer;
  }
  .stop svg {
    width: 15px;
    height: 15px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
  }
  .stop:hover { background: rgba(255, 255, 255, .12); }
  .note.playing .stop, .note.paused .stop, .note.seeking .stop,
  .voice.started .stop { opacity: 1; pointer-events: auto; }
  /* У кружка крестик в верхнем углу кадра, ровно над троеточием */
  .note .stop { right: -10px; top: 0; }
  /* У голосового крестик встаёт над троеточием, как и у кружка */
  .voice .stop { right: 0; top: -2px; }
  /* У кружка — в углу кадра, низом вровень с его нижним краем */
  .note .more { position: absolute; right: -10px; bottom: 0; }
  /* У голосового — в нижнем углу строки, время идёт слева от него */
  .voice .more { position: absolute; right: 0; bottom: -2px; }
  .sheet {
    position: absolute;
    display: none;
    padding: 6px;
    border-radius: 12px;
    background: #17212b;
    box-shadow: 0 6px 20px rgba(0, 0, 0, .55);
    font-size: 13px;
    z-index: 2;
  }
  /* У кружка раскрывается вверх и вправо: вниз оно легло бы на подпись
     следующего сообщения, а влево — на сам кадр */
  .note .sheet { left: calc(100% - 18px); bottom: 28px; }
  /* У голосового — вниз и вправо: сверху стоят сами знаки */
  .voice .sheet { left: calc(100% - 28px); top: 100%; }
  .sheet.open { display: block; }
  .sheet-save {
    display: block;
    padding: 6px 10px;
    border-radius: 8px;
    color: #71bdff;
    text-decoration: none;
    white-space: nowrap;
  }
  .sheet-save:hover { background: rgba(255, 255, 255, .08); }
  .sheet-speed { display: flex; gap: 4px; margin-top: 4px; }
  .sheet-speed button {
    padding: 4px 8px;
    border: none;
    border-radius: 8px;
    background: rgba(255, 255, 255, .08);
    color: #d6dee6;
    font-size: 12px;
    cursor: pointer;
  }
  /* Выбранная скорость подсвечена: иначе непонятно, на чём запись сейчас идёт */
  .sheet-speed .picked { background: #3390ec; color: #fff; }
  .edits { margin: 6px 0 0; font-size: 13px; }
  .edits summary { color: #8fa3b5; cursor: pointer; }
  .edits ul { margin: 6px 0 0; padding-left: 18px; }
  .edits li { margin-bottom: 4px; }
  .version-time { display: block; color: #708499; font-size: 12px; }
  .version-text { white-space: pre-wrap; }
  .empty { text-align: center; color: #8fa3b5; }
"""


# Единственный скрипт на странице, и он только про кружки: включение щелчком,
# пауза вторым щелчком и перемотка кольцом. Уходит в файл, лишь когда кружки
# в выгрузке есть, — остальным страницам он ни к чему
NOTES_SCRIPT = """
document.querySelectorAll('.note').forEach(function (note) {
  var video = note.querySelector('video');
  var bar = note.querySelector('.ring-bar');
  var knob = note.querySelector('.ring-knob');
  var hit = note.querySelector('.ring-hit');
  var clock = note.querySelector('.note-time');
  var stop = note.querySelector('.stop');
  var length = bar.getTotalLength();
  var radius = bar.r.baseVal.value;
  var started = false;
  var resume = false;
  var frame = 0;
  // До первого щелчка кружок крутится молча по кругу, как в переписке
  var preview = true;
  bar.style.strokeDasharray = length;
  bar.style.strokeDashoffset = length;

  function clocked(seconds) {
    var whole = Math.floor(seconds || 0);
    var rest = whole % 60;
    return Math.floor(whole / 60) + ':' + (rest < 10 ? '0' + rest : rest);
  }

  function roll() {
    var wish = video.play();
    // Браузер вправе не пустить автозапуск — тогда кружок просто ждёт щелчка
    if (wish && wish.catch) { wish.catch(function () {}); }
  }

  function draw() {
    if (preview) {
      // Кольца на немом прокруте нет, а на табличке стоит длина записи
      clock.textContent = clocked(video.duration);
      return;
    }

    var part = video.duration ? video.currentTime / video.duration : 0;
    bar.style.strokeDashoffset = length * (1 - part);
    // Бегунок ставим на конец дуги: отсчёт идёт от верха по часовой стрелке
    var angle = part * 2 * Math.PI - Math.PI / 2;
    knob.setAttribute('cx', 50 + radius * Math.cos(angle));
    knob.setAttribute('cy', 50 + radius * Math.sin(angle));
    clock.textContent = clocked(video.currentTime);
  }

  function follow() {
    // timeupdate приходит раз в четверть секунды, и бегунок от него дёргается,
    // поэтому пока запись идёт, ведём его к каждому кадру страницы
    draw();
    frame = requestAnimationFrame(follow);
  }

  function halt() {
    if (frame) { cancelAnimationFrame(frame); }
    frame = 0;
    draw();
  }

  function idle() {
    // Непросмотренный и досмотренный кружок ведут себя одинаково: немой круг
    preview = true;
    started = false;
    video.loop = true;
    video.muted = true;
    video.currentTime = 0;
    note.classList.remove('playing', 'paused', 'seeking');
    halt();
    roll();
  }

  function live() {
    started = true;
    note.classList.add('playing');
    note.classList.remove('paused');
    if (!frame) { follow(); }
  }

  function start() {
    // Щелчок переводит кружок из немого круга в просмотр со звуком, с начала
    preview = false;
    video.loop = false;
    video.muted = false;
    video.currentTime = 0;
    // Кружок и так крутится, и play() события тут не поднимет: включаем сами
    live();
    roll();
  }

  function seek(event) {
    if (!video.duration) { return; }
    var box = note.getBoundingClientRect();
    var angle = Math.atan2(
      event.clientY - box.top - box.height / 2,
      event.clientX - box.left - box.width / 2
    );
    var part = ((angle + Math.PI / 2) / (2 * Math.PI) + 1) % 1;
    video.currentTime = part * video.duration;
    draw();
  }

  video.addEventListener('click', function () {
    if (preview) { start(); }
    else if (video.paused) { roll(); }
    else { video.pause(); }
  });
  video.addEventListener('play', function () {
    // Немой прокрут ни кольца, ни счёта не касается
    if (preview) { return; }

    live();
  });
  video.addEventListener('pause', function () {
    if (preview) { return; }

    note.classList.remove('playing');
    note.classList.add('paused');
    halt();
  });
  video.addEventListener('ended', idle);
  stop.addEventListener('click', function (event) {
    // Крестик возвращает кружок в немой прокрут, с паузы в том числе
    event.stopPropagation();
    idle();
  });
  video.addEventListener('timeupdate', draw);
  video.addEventListener('loadedmetadata', draw);

  hit.addEventListener('pointerdown', function (event) {
    // Пока кружок не смотрели, перематывать нечего: щелчок его запускает
    if (!started) { start(); return; }

    hit.setPointerCapture(event.pointerId);
    // Звук на перемотке идёт обрывками и трещит, поэтому ведём кадры молча,
    // а воспроизведение возвращаем, когда кольцо отпустят
    resume = !video.paused;
    video.pause();
    // Кольцо поджимается, едва за него взялись, — так видно, что оно в руках
    note.classList.add('seeking');
    seek(event);
  });
  hit.addEventListener('pointermove', function (event) {
    // Кольцо ведут пальцем или мышью с зажатой кнопкой — перематываем следом
    if (hit.hasPointerCapture(event.pointerId)) { seek(event); }
  });
  hit.addEventListener('lostpointercapture', function () {
    note.classList.remove('seeking');
    if (resume) {
      resume = false;
      roll();
    }
  });

  // Кружки за краем экрана крутить незачем: это только греет процессор впустую
  new IntersectionObserver(function (entries) {
    if (!preview) { return; }
    if (entries[0].isIntersecting) { roll(); } else { video.pause(); }
  }, { threshold: .2 }).observe(note);

  idle();
});
"""


# Видео живёт по тем же правилам, что и кружок, только дорожка у него прямая:
# молчаливый прокрут до щелчка, звук и счёт с запуска, перемотка по нижнему краю
CLIPS_SCRIPT = """
document.querySelectorAll('.film').forEach(function (film) {
  var video = film.querySelector('video');
  var bar = film.querySelector('.film-bar');
  var clock = film.querySelector('.film-time');
  var frame = 0;

  function clocked(seconds) {
    // Счётчик на кадре идёт с ведущим нулём: «00:03», как в переписке
    var whole = Math.floor(seconds || 0);
    var rest = whole % 60;
    var mins = Math.floor(whole / 60);
    return (mins < 10 ? '0' + mins : mins) + ':' + (rest < 10 ? '0' + rest : rest);
  }

  function roll() {
    var wish = video.play();
    // Браузер вправе не пустить автозапуск — тогда видео просто ждёт щелчка
    if (wish && wish.catch) { wish.catch(function () {}); }
  }

  function draw() {
    var part = video.duration ? video.currentTime / video.duration : 0;
    bar.style.width = (part * 100) + '%';
  }

  function follow() {
    // timeupdate приходит раз в четверть секунды, и дорожка от него дёргается
    draw();
    frame = requestAnimationFrame(follow);
  }

  video.addEventListener('play', function () { if (!frame) { follow(); } });
  video.addEventListener('pause', function () {
    if (frame) { cancelAnimationFrame(frame); }
    frame = 0;
    draw();
  });
  video.addEventListener('loadedmetadata', function () {
    // Длину берут из базы, но у старых записей её там нет — спросим сам файл
    if (video.duration) { clock.textContent = clocked(video.duration); }
    draw();
  });

  // Видео за краем экрана крутить незачем: это только греет процессор впустую
  new IntersectionObserver(function (entries) {
    if (entries[0].isIntersecting) { roll(); } else { video.pause(); }
  }, { threshold: .2 }).observe(film);

  roll();
});
"""


def _author_color(name):
    """У одного автора цвет всегда один и тот же, иначе он не помогает читать.

    Считаем через crc32, а не сумму байт: у русских имён одной длины суммы
    сходятся слишком часто, и половина чата оказывалась одного цвета.
    """
    return PALETTE[crc32(name.encode()) % len(PALETTE)]


# В цитате вместо «без текста» стоит сам вид вложения, как в переписке
REPLY_KINDS = {
    "photo": "Фото",
    "animation": "GIF-анимация",
    "video": "Видео",
    "video_note": "Видеосообщение",
    "voice": "Голосовое сообщение",
    "audio": "Аудиофайл",
    "document": "Документ",
    "sticker": "Стикер",
}

# Мини-кадр цитате даёт только то, что видно глазом
REPLY_MINIS = ("photo", "sticker", "video", "video_note", "animation")


def _preview(text):
    """Отрывок для цитаты: целиком родительское сообщение есть на своём месте."""
    if len(text) > REPLY_PREVIEW_LIMIT:
        return text[:REPLY_PREVIEW_LIMIT] + "…"
    return text


def _reply_line(parent):
    """Строка цитаты: свои слова, а если их нет — сам вид вложения, как в чате."""
    if parent.text:
        return f'<span class="reply-text">{escape(_preview(parent.text))}</span>'

    kind = parent.attachments[0].file_type if parent.attachments else None
    if kind is None:
        return f'<span class="reply-text">{escape(NO_TEXT)}</span>'

    name = REPLY_KINDS.get(kind) or file_type_label(kind).capitalize()
    # У записи вид отмечают точкой: показать её нечем, и строка выходит пустовата
    dot = "&bull; " if kind in ("voice", "audio") else ""
    return f'<span class="reply-kind">{dot}{escape(name)}</span>'


def _reply_block(message, by_telegram_id):
    if message.reply_to_message_id is None:
        return ""

    parent = by_telegram_id.get(message.reply_to_message_id)
    if parent is None:
        # Ответ на сообщение вне периода: цитировать нечего, но молчать нельзя —
        # иначе непонятно, к чему относится ответ
        return (
            '<div class="reply reply-lost">Ответ на сообщение, которого нет '
            "в этой выгрузке</div>"
        )

    author = escape(sender_name(parent.user) or UNKNOWN_SENDER)
    kind = parent.attachments[0].file_type if parent.attachments else None
    mini = ""
    if kind in REPLY_MINIS:
        round_off = " round" if kind == "video_note" else ""
        mini = (
            f'<span class="reply-mini{round_off}"'
            f' data-aim="m{parent.telegram_message_id}"></span>'
        )

    return (
        f'<a class="reply" href="#m{parent.telegram_message_id}">{mini}'
        f'<span class="reply-body"><span class="reply-author">{author}</span>'
        f"{_reply_line(parent)}</span></a>"
    )


def _forward_block(message):
    if message.forward_origin_type is None:
        return ""

    kind = FORWARD_ORIGIN_LABELS.get(
        message.forward_origin_type, message.forward_origin_type
    )
    line = f"Переслано от: {message.forward_from_name or UNKNOWN_SENDER} ({kind})"

    origin_date = format_datetime(message.forward_origin_date)
    if origin_date:
        line += f", {origin_date}"
    if message.is_automatic_forward:
        line += ", автопересылка из канала"

    return f'<div class="forward">{escape(line)}</div>'


def _edits_block(message):
    """Прежние версии под щелчком: обычно читают последнюю, правки нужны редко."""
    if not message.versions:
        return ""

    items = "".join(
        f'<li><span class="version-time">{escape(format_datetime(version.replaced_at) or "")}'
        f'</span><span class="version-text">{escape(version.text or NO_TEXT)}</span></li>'
        for version in message.versions
    )
    return (
        f'<details class="edits"><summary>прежних версий: '
        f"{len(message.versions)}</summary><ul>{items}</ul></details>"
    )


# Второй и последний скрипт страницы — про голосовые: кнопка, дорожка звука
# и перемотка по ней. Уходит в файл, только если вшитые записи есть
VOICE_SCRIPT = """
(function () {
  var shared = null;

  function reader() {
    // Разбор звука один на страницу: таких вкладке дают всего несколько
    if (shared === null) {
      var Maker = window.AudioContext || window.webkitAudioContext;
      shared = Maker ? new Maker() : false;
    }
    return shared;
  }

  function clocked(seconds) {
    var whole = Math.floor(seconds || 0);
    var rest = whole % 60;
    return Math.floor(whole / 60) + ':' + (rest < 10 ? '0' + rest : rest);
  }

  function loudness(sound) {
    // Из всей записи оставляем две сотни громкостей: столько дорожка и вмещает
    var data = sound.getChannelData(0);
    var count = 200;
    var step = Math.floor(data.length / count) || 1;
    var list = [];
    var top = 0;
    for (var i = 0; i < count; i++) {
      var peak = 0;
      for (var j = i * step; j < (i + 1) * step && j < data.length; j++) {
        var value = Math.abs(data[j]);
        if (value > peak) { peak = value; }
      }
      list.push(peak);
      if (peak > top) { top = peak; }
    }
    return list.map(function (value) { return top ? value / top : 0; });
  }

  document.querySelectorAll('.voice').forEach(function (voice) {
    var audio = voice.querySelector('audio');
    if (!audio) { return; }

    var wave = voice.querySelector('.voice-wave');
    var line = voice.querySelector('.voice-line');
    var button = voice.querySelector('.voice-play');
    var stop = voice.querySelector('.stop');
    var brush = wave.getContext('2d');
    var height = 24;
    var peaks = null;
    var asked = false;
    var started = false;
    var resume = false;
    var frame = 0;

    function roll() {
      var wish = audio.play();
      if (wish && wish.catch) { wish.catch(function () {}); }
    }

    // Запись считается начатой до самого конца или до крестика: пауза её
    // не заканчивает, и строка с ходом записи на паузе никуда не девается
    function wake() { started = true; voice.classList.add('started'); }
    function rest() { started = false; voice.classList.remove('started'); }

    function paint() {
      var ratio = window.devicePixelRatio || 1;
      var width = wave.clientWidth;
      if (wave.width !== Math.round(width * ratio)) {
        wave.width = Math.round(width * ratio);
        wave.height = Math.round(height * ratio);
      }
      brush.setTransform(ratio, 0, 0, ratio, 0, 0);
      brush.clearRect(0, 0, width, height);

      var part = audio.duration ? audio.currentTime / audio.duration : 0;
      var step = 4;
      var count = Math.max(1, Math.floor((width - 2) / step));
      brush.lineWidth = 2;
      brush.lineCap = 'round';
      for (var i = 0; i < count; i++) {
        // Пока запись не разобрана, дорожка ровная: врать про громкость незачем
        var value = peaks ? peaks[Math.floor(i * peaks.length / count)] : .22;
        var tall = Math.max(2, value * (height - 4));
        var x = i * step + 2;
        brush.strokeStyle = (i + .5) / count <= part ? '#3390ec' : '#4f6a85';
        brush.beginPath();
        brush.moveTo(x, (height - tall) / 2);
        brush.lineTo(x, (height + tall) / 2);
        brush.stroke();
      }

      // С запуска строка показывает ход записи, до него — её длину и вес
      if (!started) { line.textContent = line.getAttribute('data-idle'); }
      else if (audio.duration) {
        line.textContent = clocked(audio.currentTime) + ' / ' + clocked(audio.duration);
      }
    }

    function follow() {
      // timeupdate приходит раз в четверть секунды, и дорожка от него дёргается
      paint();
      frame = requestAnimationFrame(follow);
    }

    function halt() {
      if (frame) { cancelAnimationFrame(frame); }
      frame = 0;
      paint();
    }

    function analyse() {
      // Разбираем запись, только когда до неё долистали, и только один раз
      if (asked) { return; }
      asked = true;

      var maker = reader();
      if (!maker) { return; }
      fetch(audio.src)
        .then(function (answer) { return answer.arrayBuffer(); })
        .then(function (raw) { return maker.decodeAudioData(raw); })
        .then(function (sound) { peaks = loudness(sound); paint(); })
        .catch(function () {});
    }

    function seek(event) {
      if (!audio.duration) { return; }
      var box = wave.getBoundingClientRect();
      var part = (event.clientX - box.left) / box.width;
      wake();
      audio.currentTime = Math.min(1, Math.max(0, part)) * audio.duration;
      paint();
    }

    button.addEventListener('click', function () {
      if (audio.paused) { roll(); } else { audio.pause(); }
    });
    audio.addEventListener('play', function () {
      wake();
      voice.classList.add('playing');
      button.setAttribute('aria-label', 'Пауза');
      if (!frame) { follow(); }
    });
    audio.addEventListener('pause', function () {
      voice.classList.remove('playing');
      button.setAttribute('aria-label', 'Включить');
      halt();
    });
    audio.addEventListener('ended', function () {
      // Дослушанное снова показывает свою длину, как в переписке
      rest();
      audio.currentTime = 0;
      halt();
    });
    stop.addEventListener('click', function () {
      // Крестик не просто останавливает, а возвращает запись к началу
      audio.pause();
      audio.currentTime = 0;
      rest();
      halt();
    });
    audio.addEventListener('loadedmetadata', paint);
    window.addEventListener('resize', paint);

    wave.addEventListener('pointerdown', function (event) {
      wave.setPointerCapture(event.pointerId);
      // Звук на перемотке идёт обрывками и трещит, поэтому ведём её молча
      resume = !audio.paused;
      audio.pause();
      seek(event);
    });
    wave.addEventListener('pointermove', function (event) {
      if (wave.hasPointerCapture(event.pointerId)) { seek(event); }
    });
    wave.addEventListener('lostpointercapture', function () {
      if (resume) {
        resume = false;
        roll();
      }
    });

    new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) { analyse(); }
    }, { threshold: .1 }).observe(voice);

    paint();
  });
})();
"""


# Меню под троеточием одно на обе записи, поэтому и скрипт у него общий:
# сам находит свою запись — видео у кружка, звук у голосового
MENU_SCRIPT = """
document.querySelectorAll('.sheet').forEach(function (sheet) {
  var holder = sheet.parentNode;
  var media = holder.querySelector('video, audio');
  var more = holder.querySelector('.more');
  var save = sheet.querySelector('.sheet-save');
  var speeds = sheet.querySelectorAll('[data-rate]');

  // Файл уже лежит в самой записи, второй раз его в страницу не вшивают
  save.href = media.currentSrc || media.getAttribute('src');

  more.addEventListener('click', function (event) {
    // Щелчок по троеточию не должен доходить до записи и включать её
    event.stopPropagation();
    sheet.classList.toggle('open');
  });
  sheet.addEventListener('click', function (event) { event.stopPropagation(); });
  document.addEventListener('click', function () { sheet.classList.remove('open'); });

  speeds.forEach(function (item) {
    item.addEventListener('click', function () {
      media.playbackRate = parseFloat(item.getAttribute('data-rate'));
      speeds.forEach(function (other) {
        other.classList.toggle('picked', other === item);
      });
      sheet.classList.remove('open');
    });
  });
});
"""


# Прыжок из цитаты к оригиналу: подвести к нему и подсветить, как в переписке
# Файл уходит на диск, а не открывается, — о таком принято спрашивать заранее
ASK = (
    '<div class="ask" hidden>'
    '<div class="ask-box">'
    '<p class="ask-head">Вы действительно хотите скачать этот файл?</p>'
    '<p class="ask-name"></p>'
    '<p class="ask-size"></p>'
    '<div class="ask-row">'
    '<button type="button" class="ask-no">Отмена</button>'
    '<button type="button" class="ask-yes">Скачать</button>'
    "</div></div></div>"
)

ASK_SCRIPT = """
(function () {
  var ask = document.querySelector('.ask');
  var name = ask.querySelector('.ask-name');
  var weight = ask.querySelector('.ask-size');
  var aim = null;

  function close() { ask.hidden = true; aim = null; }

  document.querySelectorAll('.doc, .sheet-save').forEach(function (link) {
    link.addEventListener('click', function (event) {
      event.preventDefault();
      aim = link;
      name.textContent = link.getAttribute('download');
      weight.textContent = link.getAttribute('data-size') || '';
      ask.hidden = false;
      ask.querySelector('.ask-yes').focus();
    });
  });

  ask.querySelector('.ask-yes').addEventListener('click', function () {
    if (!aim) { return; }
    // Сохраняет отдельная ссылка: щелчок по исходной снова открыл бы вопрос
    var out = document.createElement('a');
    out.href = aim.href;
    out.download = aim.getAttribute('download');
    document.body.appendChild(out);
    out.click();
    out.remove();
    close();
  });

  ask.querySelector('.ask-no').addEventListener('click', close);
  ask.addEventListener('click', function (event) {
    if (event.target === ask) { close(); }
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !ask.hidden) { close(); }
  });
})();
"""

JUMP_SCRIPT = """
function flash(aim) {
  // Класс снимает конец самой анимации: таймер от прошлого щелчка гасил бы
  // новую подсветку на середине, и от частых нажатий она дёргалась
  if (!aim.dataset.lit) {
    aim.addEventListener('animationend', function () {
      aim.classList.remove('flash');
    });
    aim.dataset.lit = '1';
  }
  // Класс снимают и ставят заново, иначе второй щелчок подсветку не повторит
  aim.classList.remove('flash');
  void aim.offsetWidth;
  aim.classList.add('flash');
}

document.querySelectorAll('.reply').forEach(function (link) {
  link.addEventListener('click', function (event) {
    var aim = document.querySelector(link.getAttribute('href'));
    if (!aim) { return; }
    // Адрес страницы не трогаем: с ним подсветка досталась бы и :target
    event.preventDefault();
    aim.scrollIntoView({ block: 'center', behavior: 'smooth' });
    flash(aim);
  });
});
"""


# Мини-кадр в цитате: берём его у самого сообщения на странице, чтобы не вшивать
# те же байты второй раз — у видео для этого снимаем один кадр на полотно
MINI_SCRIPT = """
document.querySelectorAll('.reply-mini').forEach(function (mini) {
  var aim = document.getElementById(mini.dataset.aim);
  // Тяжёлое вложение осталось на странице строкой, и кадр для рамки брать негде
  if (!aim) { mini.remove(); return; }

  function stand(source) { mini.style.backgroundImage = 'url(' + source + ')'; }

  var shot = aim.querySelector('.shot img');
  if (shot) { stand(shot.getAttribute('src')); return; }

  var film = aim.querySelector('video');
  if (!film) { mini.remove(); return; }

  function snap() {
    if (!film.videoWidth) { return; }

    // Кадр обрезаем по центру в квадрат: иначе он сплющивается в мини-рамке
    var side = Math.min(film.videoWidth, film.videoHeight);
    var sheet = document.createElement('canvas');
    sheet.width = 68;
    sheet.height = 68;
    sheet.getContext('2d').drawImage(
      film,
      (film.videoWidth - side) / 2, (film.videoHeight - side) / 2, side, side,
      0, 0, 68, 68
    );
    stand(sheet.toDataURL('image/jpeg', 0.7));
  }

  if (film.readyState >= 2) { snap(); }
  else { film.addEventListener('loadeddata', snap); }
});
"""


# Кнопки просмотра: рисуем их сами, чтобы страница не ходила за иконками в сеть
VIEW_ICONS = {
    "close": '<path d="M7 7l10 10M17 7L7 17"/>',
    "turn": (
        '<path d="M12 8.2 15.8 12 12 15.8 8.2 12z"/>'
        '<path d="M12 20A8 8 0 0 0 14.5 4.4"/>'
        # Обводка тем же цветом скругляет углы наконечника, заливка делает его сплошным
        '<path d="M12.7 3.8 15 2.7 13.9 6.1z" fill="currentColor" stroke-width="1.4"/>'
    ),
    "save": '<path d="M12 5v9m0 0-3.5-3.5M12 14l3.5-3.5"/><path d="M6 18h12"/>',
    "back": '<path d="M14.5 5 7.5 12l7 7"/>',
    "fold": '<path d="M6 9.5 12 15.5l6-6"/>',
    "next": '<path d="M9.5 5 16.5 12l-7 7"/>',
    # Знаки острова под видео: у браузеров они разные, поэтому рисуем свои
    "play": '<path d="M8 5.4 18.6 12 8 18.6z" fill="currentColor"/>',
    "pause": (
        '<rect x="8.2" y="5.4" width="2.9" height="13.2" rx="1"'
        ' fill="currentColor" stroke="none"/>'
        '<rect x="12.9" y="5.4" width="2.9" height="13.2" rx="1"'
        ' fill="currentColor" stroke="none"/>'
    ),
    "gear": (
        '<circle cx="12" cy="12" r="3.1"/>'
        '<path d="M19.4 13.5a7.7 7.7 0 0 0 0-3l1.9-1.4-1.9-3.3-2.2.9a7.6 7.6 0 0 0-2.6-1.5'
        "L14.3 2h-4l-.3 2.3a7.6 7.6 0 0 0-2.6 1.5l-2.2-.9-1.9 3.3 1.9 1.4a7.7 7.7 0 0 0 0 3"
        'l-1.9 1.4 1.9 3.3 2.2-.9a7.6 7.6 0 0 0 2.6 1.5l.3 2.3h4l.3-2.3a7.6 7.6 0 0 0 2.6-1.5'
        'l2.2.9 1.9-3.3z"/>'
    ),
    "tick": '<path d="M5.5 12.5 10 17l8.5-9"/>',
    "shut": '<path d="M7 7l10 10M17 7L7 17"/>',
    "sound": (
        '<path d="M4 9.5h3.6L11.6 6v12L7.6 14.5H4z" fill="currentColor"/>'
        '<path d="M14.8 9.4a3.6 3.6 0 0 1 0 5.2"/>'
        '<path d="M17.4 7a7.2 7.2 0 0 1 0 10"/>'
    ),
    "quiet": (
        '<path d="M4 9.5h3.6L11.6 6v12L7.6 14.5H4z" fill="currentColor"/>'
        '<path d="M15.4 9.6l5 4.8M20.4 9.6l-5 4.8"/>'
    ),
    "wide": (
        '<path d="M13.8 10.2 20 4M20 4h-4.8M20 4v4.8"/>'
        '<path d="M10.2 13.8 4 20M4 20h4.8M4 20v-4.8"/>'
    ),
    "mini": (
        '<rect x="3.4" y="5.4" width="17.2" height="13.2" rx="2.6"/>'
        '<rect x="11.8" y="11.6" width="7.2" height="5.4" rx="1.2" fill="currentColor"/>'
    ),
}


def _view_icon(name, size=20, cls=None):
    mark = f' class="{cls}"' if cls else ""
    return (
        f'<svg{mark} viewBox="0 0 24 24" width="{size}" height="{size}" fill="none"'
        ' stroke="currentColor" stroke-width="1.6" stroke-linecap="round"'
        ' stroke-linejoin="round" aria-hidden="true">'
        f"{VIEW_ICONS[name]}</svg>"
    )


def _view_button(act, title, size=20, extra=""):
    return (
        f'<button class="view-btn{extra}" type="button" data-act="{act}" title="{title}"'
        f' aria-label="{title}">{_view_icon(act, size)}</button>'
    )


def _bar_button(act, title, inner, extra=""):
    """Кнопка острова: у иных знак меняется по ходу, поэтому их два в одной."""
    return (
        f'<button class="view-btn bar-btn{extra}" type="button" data-act="{act}"'
        f' title="{title}" aria-label="{title}">{inner}</button>'
    )


# Скорости как в Telegram: своё название у каждой, чтобы не гадать по числу
VIEW_RATES = (
    ("0.5", "0,5&times;", "Медленно"),
    ("1", "1&times;", "Обычная"),
    ("1.2", "1,2&times;", "Средняя"),
    ("1.5", "1,5&times;", "Быстро"),
    ("1.7", "1,7&times;", "Очень быстро"),
    ("2", "2&times;", "Максимум"),
)


def _rate_row(value, mark, title):
    return (
        f'<button class="rate-row" type="button" data-rate="{value}">'
        f'<span class="rate-mark">{mark}</span><span class="rate-name">{title}</span>'
        f'{_view_icon("tick", 18, "rate-tick")}</button>'
    )


# Остров под видео: своих кнопок у страницы нет, а браузерные у всех свои
VIEW_BAR = (
    '<div class="view-bar">'
    '<div class="bar-top">'
    '<div class="bar-loud">'
    + _bar_button(
        "sound",
        "Звук",
        _view_icon("sound", 22, "sign-on") + _view_icon("quiet", 22, "sign-off"),
    )
    + '<input class="bar-vol" type="range" min="0" max="1" step="0.01" value="1"'
    ' aria-label="Громкость">'
    "</div>"
    + _bar_button(
        "play",
        "Играть",
        _view_icon("play", 30, "sign-on") + _view_icon("pause", 30, "sign-off"),
        extra=" bar-play",
    )
    + '<div class="bar-side">'
    + _bar_button("wide", "Во весь экран", _view_icon("wide", 22))
    + _bar_button("mini", "Поверх окна", _view_icon("mini", 22))
    + _bar_button("rate", "Скорость", _view_icon("gear", 22))
    + "</div></div>"
    '<div class="bar-line">'
    '<span class="bar-now">00:00</span>'
    '<div class="bar-track"><div class="bar-fill"></div></div>'
    '<span class="bar-left">&minus;00:00</span>'
    "</div>"
    '<div class="bar-rate" hidden>'
    + "".join(_rate_row(*rate) for rate in VIEW_RATES)
    + "</div></div>"
)


# Просмотр фото во весь экран: один на страницу, снимок в него подставляет скрипт
VIEWER = (
    '<div class="lightbox">'
    '<div class="view-top">' + _view_button("close", "Закрыть") + "</div>"
    + _view_button("back", "Предыдущее", size=30, extra=" view-step view-back")
    + '<img alt="">'
    # Видео открывается тем же окном, только с островом кнопок под кадром
    + '<div class="view-play" hidden><video class="view-film" playsinline></video>'
    # В полный экран уходит только кадр, и верхний крестик окна туда не попадает
    + _view_button("shut", "Свернуть", extra=" view-shut")
    + VIEW_BAR
    + "</div>"
    + _view_button("next", "Следующее", size=30, extra=" view-step view-next")
    + '<div class="view-foot">'
    '<div class="view-who"><span class="view-count"></span>'
    '<span class="view-from"></span></div>'
    + '<div class="view-note">'
    + _view_button("fold", "Скрыть подпись", size=18, extra=" view-fold")
    + '<div class="view-text"><div class="view-line"></div></div></div>'
    + '<div class="view-tools">'
    + _view_button("turn", "Повернуть", size=26)
    + '<a class="view-btn view-save" download title="Скачать" aria-label="Скачать">'
    + _view_icon("save", 26)
    + "</a></div></div></div>"
)


# Просмотр фото во весь экран: одно окно на страницу, снимки листаются стрелками
VIEW_SCRIPT = """
(function () {
  var box = document.querySelector('.lightbox');
  var big = box.querySelector('img');
  var count = box.querySelector('.view-count');
  var from = box.querySelector('.view-from');
  var caption = box.querySelector('.view-text');
  var line = caption.querySelector('.view-line');
  var note = box.querySelector('.view-note');
  var save = box.querySelector('.view-save');
  var film = box.querySelector('.view-film');
  var stage = box.querySelector('.view-play');
  var loud = box.querySelector('.bar-vol');
  var track = box.querySelector('.bar-track');
  var fill = box.querySelector('.bar-fill');
  var now = box.querySelector('.bar-now');
  var rest = box.querySelector('.bar-left');
  var rates = box.querySelector('.bar-rate');
  var bar = box.querySelector('.view-bar');
  var foot = box.querySelector('.view-foot');
  var tools = box.querySelector('.view-tools');
  // Фото и видео листаются вперемешку, в том же порядке, что идут в переписке
  var shots = Array.prototype.slice.call(document.querySelectorAll('.shot img, .film video'));
  var at = -1;
  var turn = 0;
  var beat = 0;
  var pace = 1;
  var movie = false;
  // Полоса под кнопки сверху и потолок подписи снизу
  var TOP = 56;
  var CAP = 132;

  function tell(open, quick) {
    box.classList.toggle('folded', !open);
    // Меряют строку, а не плашку: у плашки высота уже подрезана прошлым замером,
    // и от открытия к открытию она росла бы, пока не упрётся в потолок
    var full = Math.ceil(line.getBoundingClientRect().height);
    // Полоса прокрутки нужна только длинной подписи: короткой её давало округление
    caption.style.overflowY = full > CAP ? 'auto' : 'hidden';
    var height = Math.min(full, CAP);
    // При листании подпись уже открыта, и ехать ей заново незачем: плавно она
    // ходит только от стрелки
    caption.style.transition = quick ? 'none' : '';
    // Точная высота вместо потолка: иначе плашка сперва стоит, а потом падает рывком
    caption.style.maxHeight = open ? height + 'px' : '0';
    // Полоса под подпись вместе со стрелкой; такая же сверху держит снимок посередине.
    // Считают её по самой подписи, а не по тому, убрали её стрелкой или нет.
    // У видео подпись лежит поверх кадра, и полосы под неё не надо: иначе кадр
    // с подписью выходил заметно меньше такого же без неё
    var band = movie || note.hidden ? TOP : Math.max(TOP, height + 76);
    box.style.setProperty('--band', band + 'px');
    // Значения без перехода надо успеть применить, пока он выключен
    if (quick) { caption.getBoundingClientRect(); caption.style.transition = ''; }
  }

  function turned() {
    // Только поворот: размер снимка от него не меняется, как в Telegram. Широкий
    // кадр после поворота выходит за края экрана, и это правильно
    big.style.transform = 'rotate(' + turn + 'deg)';
    film.style.transform = 'rotate(' + turn + 'deg)';
  }

  function quiet() {
    // Видео из прошлого показа не должно играть за спиной у соседнего снимка
    film.pause();
    film.removeAttribute('src');
  }

  function clocked(seconds) {
    var whole = Math.max(0, Math.floor(seconds || 0));
    var tail = whole % 60;
    var mins = Math.floor(whole / 60);
    return (mins < 10 ? '0' + mins : mins) + ':' + (tail < 10 ? '0' + tail : tail);
  }

  function drawn() {
    var whole = film.duration || 0;
    fill.style.width = (whole ? film.currentTime / whole * 100 : 0) + '%';
    now.textContent = clocked(film.currentTime);
    // Справа, как в плеере, остаток до конца, а не вся длина
    rest.textContent = '\\u2212' + clocked(whole - film.currentTime);
  }

  function follow() {
    // timeupdate приходит раз в четверть секунды, и дорожка от него дёргается
    drawn();
    beat = requestAnimationFrame(follow);
  }

  function halt() {
    if (beat) { cancelAnimationFrame(beat); }
    beat = 0;
  }

  function louder() {
    var part = film.muted ? 0 : film.volume;
    stage.classList.toggle('quiet', part === 0);
    loud.value = part;
    loud.style.background = 'linear-gradient(to right, #fff ' + (part * 100) +
      '%, rgba(255, 255, 255, .35) ' + (part * 100) + '%)';
  }

  function paced(value) {
    // Скорость держится, пока её не сменят: со следующим видео она та же.
    // Новый источник сбрасывает её на обычную, поэтому помним выбор сами
    pace = value;
    film.playbackRate = value;
    film.defaultPlaybackRate = value;
    rates.querySelectorAll('.rate-row').forEach(function (row) {
      row.classList.toggle('picked', Number(row.dataset.rate) === value);
    });
  }

  function seek(event) {
    if (!film.duration) { return; }

    var line = track.getBoundingClientRect();
    var part = (event.clientX - line.left) / line.width;
    film.currentTime = Math.min(1, Math.max(0, part)) * film.duration;
    drawn();
  }

  function show(index) {
    if (index < 0 || index >= shots.length) { return; }
    at = index;
    turn = 0;
    turned();

    var shot = shots[at];
    var source = shot.currentSrc || shot.getAttribute('src');
    movie = shot.tagName === 'VIDEO';
    big.hidden = movie;
    stage.hidden = !movie;
    // У видео подпись висит над островом, поверх нижнего края кадра, а у фото
    // стоит своей строкой внизу окна
    if (movie) { stage.insertBefore(note, bar); } else { foot.insertBefore(note, tools); }
    quiet();
    if (movie) {
      // На полотне кадр крутится молча, а в окне его смотрят со звуком
      shot.pause();
      film.src = source;
      film.muted = false;
      paced(pace);
      louder();
      drawn();
      var wish = film.play();
      if (wish && wish.catch) { wish.catch(function () {}); }
    } else {
      big.src = source;
    }
    save.href = source;
    save.setAttribute('download', shot.dataset.name || (movie ? 'видео' : 'фото'));
    count.textContent = (movie ? 'Видео ' : 'Фото ') + (at + 1) + ' из ' + shots.length;
    from.textContent = shot.dataset.from || '';
    line.textContent = shot.dataset.text || '';
    // Без подписи прятать нечего, и стрелка только мешала бы
    note.hidden = !shot.dataset.text;
    // Стрелка показывается только туда, куда есть куда листать
    box.classList.toggle('at-start', at === 0);
    box.classList.toggle('at-end', at === shots.length - 1);
    box.classList.add('open');
    // Высоту подписи меряют уже у открытого окна: у скрытого она нулевая
    tell(!box.classList.contains('folded'), true);
    // Пока смотрят снимок, переписка под ним не должна ездить от колеса мыши
    document.body.style.overflow = 'hidden';
  }

  function hide() {
    box.classList.remove('open');
    document.body.style.overflow = '';
    // Снимок из окна убираем: иначе он висит в памяти вторым разом
    big.removeAttribute('src');
    save.removeAttribute('href');
    quiet();
    halt();
    rates.hidden = true;
    // Немой прокрут на полотне продолжается, но только у видимых кадров:
    // те, что ушли за край экрана, крутить незачем
    shots.forEach(function (shot) {
      if (shot.tagName !== 'VIDEO' || !shot.paused) { return; }

      var seen = shot.getBoundingClientRect();
      if (seen.bottom < 0 || seen.top > window.innerHeight) { return; }

      var wish = shot.play();
      if (wish && wish.catch) { wish.catch(function () {}); }
    });
  }

  shots.forEach(function (shot, index) {
    shot.addEventListener('click', function () { show(index); });
  });

  box.addEventListener('click', function (event) {
    var speed = event.target.closest('[data-rate]');
    if (speed) {
      paced(Number(speed.dataset.rate));
      rates.hidden = true;
      return;
    }

    var button = event.target.closest('[data-act]');
    // Мимо шестерёнки и самого списка щелчок его закрывает
    if (!button || button.dataset.act !== 'rate') { rates.hidden = true; }
    if (!button) {
      // Щелчок мимо снимка и кнопок закрывает просмотр
      if (event.target === box) { hide(); }
      return;
    }

    var act = button.dataset.act;
    if (act === 'close') { hide(); }
    if (act === 'back') { show(at - 1); }
    if (act === 'next') { show(at + 1); }
    if (act === 'turn') { turn = (turn + 90) % 360; turned(); }
    if (act === 'fold') { tell(box.classList.contains('folded')); }
    if (act === 'play') { toggle(); }
    if (act === 'sound') { film.muted = !film.muted; }
    if (act === 'wide') { wide(); }
    if (act === 'mini') { mini(); }
    if (act === 'rate') { rates.hidden = !rates.hidden; }
    // Крестик на кадре возвращает из полного экрана, а не закрывает просмотр
    if (act === 'shut') { wide(); }
  });

  function toggle() {
    if (film.paused) {
      var wish = film.play();
      if (wish && wish.catch) { wish.catch(function () {}); }
    } else {
      film.pause();
    }
  }

  function wide() {
    if (document.fullscreenElement) { document.exitFullscreen(); }
    else if (stage.requestFullscreen) { stage.requestFullscreen(); }
  }

  function mini() {
    if (document.pictureInPictureElement) { document.exitPictureInPicture(); }
    else if (film.requestPictureInPicture) {
      var wish = film.requestPictureInPicture();
      if (wish && wish.catch) { wish.catch(function () {}); }
    }
  }

  // Окно поверх других умеют не все браузеры, и кнопка без дела только мешает
  if (!document.pictureInPictureEnabled) {
    box.querySelector('[data-act="mini"]').hidden = true;
  }
  paced(1);

  film.addEventListener('click', toggle);
  film.addEventListener('play', function () {
    stage.classList.add('playing');
    if (!beat) { follow(); }
  });
  film.addEventListener('pause', function () {
    stage.classList.remove('playing');
    halt();
    drawn();
  });
  film.addEventListener('ended', function () { stage.classList.remove('playing'); });
  film.addEventListener('loadedmetadata', drawn);
  film.addEventListener('volumechange', louder);
  loud.addEventListener('input', function () {
    film.muted = false;
    film.volume = Number(loud.value);
  });

  track.addEventListener('pointerdown', function (event) {
    track.setPointerCapture(event.pointerId);
    seek(event);
  });
  track.addEventListener('pointermove', function (event) {
    if (track.hasPointerCapture(event.pointerId)) { seek(event); }
  });

  document.addEventListener('keydown', function (event) {
    if (!box.classList.contains('open')) { return; }
    if (event.key === 'Escape') { hide(); }
    // Соседние снимки листаются стрелками, не закрывая просмотр
    if (event.key === 'ArrowRight') { show(at + 1); }
    if (event.key === 'ArrowLeft') { show(at - 1); }
    // Пробел у видео — «играть», а не прокрутка страницы под окном
    if (event.key === ' ' && !stage.hidden) { event.preventDefault(); toggle(); }
  });
})();
"""


class _Media:
    """Учёт вшитого: сколько байт ещё можно взять и нужен ли странице скрипт."""

    def __init__(self, limit=None):
        self.left = EMBED_TOTAL_LIMIT if limit is None else limit
        self.has_notes = False
        self.has_voices = False
        self.has_shots = False
        self.has_docs = False
        self.has_films = False

    def take(self, size):
        if size > EMBED_FILE_LIMIT or size > self.left:
            return False
        self.left -= size
        return True


def _media_source(attachment, media, fallback=None):
    """Файл вложения строкой data: или None с объяснением, почему его нет.

    Возвращает (источник, замечание). Источник подставляется прямо в тег: так
    страница остаётся одним файлом и играет, даже если её переслать дальше.
    Незнакомый тип годится только для скачивания, поэтому запас на него тратим,
    лишь когда вызвавший согласен на любой файл (fallback).
    """
    if attachment.file_path is None:
        return None, FILE_NOT_SAVED

    path = resolve_attachment(attachment.file_path)
    if path is None:
        logger.warning("Путь вложения ведёт за пределы вложений: %s", attachment.file_path)
        return None, FILE_MISSING
    if not path.is_file():
        return None, FILE_MISSING

    mime = MEDIA_TYPES.get(path.suffix.lower(), fallback)
    if mime is None:
        # Играть неизвестное браузеру незачем: он покажет сломанный плеер
        return None, IN_ARCHIVE

    try:
        size = path.stat().st_size
        if not media.take(size):
            return None, TOO_HEAVY
        data = path.read_bytes()
    except OSError:
        logger.exception("Вложение %s прочитать не удалось", path)
        return None, FILE_MISSING

    return f"data:{mime};base64,{b64encode(data).decode('ascii')}", None


def _true_size(attachment):
    """Вес файла: у вложений постарше его в базе нет, и тогда спросим диск."""
    if attachment.file_size is not None:
        return attachment.file_size
    if attachment.file_path is None:
        return None

    path = resolve_attachment(attachment.file_path)
    if path is None or not path.is_file():
        return None
    try:
        return path.stat().st_size
    except OSError:
        logger.exception("Вес вложения %s узнать не удалось", path)
        return None


def _media_meta(attachment):
    """Подпись под плеером: длительность видна до того, как файл загрузится."""
    parts = [file_type_label(attachment.file_type)]

    duration = format_duration(attachment.duration_seconds)
    if duration:
        parts.append(duration)
    parts.append(format_size(_true_size(attachment)))

    return " · ".join(parts)


SPEEDS = ("0,5", "1", "1,5", "2")


def _save_name(attachment, kind):
    """Имя для скачивания: своё, если оно есть, иначе тип и расширение файла."""
    if attachment.original_filename:
        return attachment.original_filename
    return kind + PurePosixPath(attachment.file_path).suffix


# Крестик останавливает запись: пауза оставляет её на месте, а он сбрасывает
STOP_SIGN = (
    '<button class="stop" type="button" aria-label="Остановить">'
    '<svg viewBox="0 0 24 24"><path d="M7 7l10 10M17 7L7 17"/></svg>'
    "</button>"
)


def _sheet_block(attachment, kind):
    """Троеточие у записи или видео: под ним скачивание и скорость."""
    rates = "".join(
        f'<button type="button" data-rate="{speed.replace(",", ".")}"'
        f'{' class="picked"' if speed == "1" else ""}>{speed}&times;</button>'
        for speed in SPEEDS
    )
    speed_block = f'<div class="sheet-speed">{rates}</div>'
    return (
        '<button class="more" type="button" aria-label="Ещё">&#8942;</button>'
        '<div class="sheet">'
        # Адрес ставит скрипт из самого вложения: второй раз вшивать файл в
        # страницу нельзя, она от этого разом удвоится
        f'<a class="sheet-save" download="{escape(_save_name(attachment, kind), quote=True)}"'
        # Вес спрашивать некогда: его показывают в окне подтверждения
        f' data-size="{escape(format_size(_true_size(attachment)), quote=True)}">'
        "Скачать</a>"
        f"{speed_block}</div>"
    )


def _voice_block(attachment, source, notice):
    """Голосовое как в переписке: кнопка, дорожка звука и время под ней.

    Плеер браузера сюда не годится: он шире пузыря и выглядит чужеродно,
    а дорожку звука рисует скрипт — по самой записи, прямо в странице.
    """
    if source is None:
        return (
            '<div class="voice"><span class="icon">&#127908;</span>'
            f'<div class="voice-body"><span class="off">{escape(notice)}</span>'
            f'<span class="meta">{escape(_media_meta(attachment))}</span>'
            "</div></div>"
        )

    length = format_duration(attachment.duration_seconds) or "0:00"
    # До запуска — длина и вес, как в переписке; дальше скрипт ведёт счёт
    idle = f"{length}, {format_size(_true_size(attachment))}"
    return (
        '<div class="voice">'
        '<button class="voice-play" type="button" aria-label="Включить">'
        '<svg viewBox="0 0 24 24">'
        '<path class="play" d="M8 5.2l11 6.8-11 6.8z"></path>'
        '<path class="pause" d="M8 5.5h3.3v13H8zM12.7 5.5H16v13h-3.3z"></path>'
        "</svg></button>"
        '<div class="voice-body"><canvas class="voice-wave"></canvas>'
        f'<span class="voice-line" data-idle="{escape(idle)}">{escape(idle)}</span></div>'
        + STOP_SIGN
        + _sheet_block(attachment, "голосовое")
        + f'<audio src="{source}" preload="auto"></audio></div>'
    )


def _circle_block(attachment, source, notice, message):
    """Кружок ведёт себя как в Telegram: щелчок включает, кольцо перематывает.

    Своих кнопок у видео нет — ни треугольника, ни громкости, ни разворота:
    в переписке их не бывает, а тут они бы только загораживали картинку.
    Поэтому и понадобился скрипт: без него щелчок по видео ничего не сделает.
    """
    length = format_duration(attachment.duration_seconds) or "0:00"
    if source is None:
        # Картинки не будет, а с ней и табличек: всё уходит в подпись под ней
        meta = f'<span class="meta">{escape(_media_meta(attachment))}</span>'
        body = f'<div class="circle-off">{escape(notice)}</div>'
    else:
        meta = ""
        body = (
            '<div class="note" title="Нажмите, чтобы включить">'
            f'<video class="note-video" src="{source}" preload="auto" '
            'playsinline muted loop>'
            "</video>"
            '<svg class="ring" viewBox="0 0 100 100">'
            '<circle class="ring-track" cx="50" cy="50" r="48"></circle>'
            '<circle class="ring-bar" cx="50" cy="50" r="48" '
            'transform="rotate(-90 50 50)"></circle>'
            '<circle class="ring-knob" cx="50" cy="2" r="5.5"></circle>'
            '<circle class="ring-hit" cx="50" cy="50" r="48"></circle>'
            "</svg>"
            '<span class="note-mute">'
            '<svg viewBox="0 0 24 24">'
            '<path d="M4 9.2h3.7L12 5v14L7.7 14.8H4z"></path>'
            '<path class="cross" d="M15.6 9.6l5 4.8M20.6 9.6l-5 4.8"></path>'
            "</svg></span>"
            f'<span class="note-time">{length}</span>'
            # Вес на картинке ни к чему: его спросят перед скачиванием, а место
            # у второй таблички занимает время сообщения, как в переписке
            f'<span class="note-when">{escape(format_time(message.sent_at) or "")}</span>'
            + STOP_SIGN
            + _sheet_block(attachment, "видеосообщение")
            + "</div>"
        )

    return f'<div class="circle">{body}{meta}</div>'


def _player_block(attachment, media, message):
    """Звук и кружки: у них свой вид, свои плееры и свой запас в странице."""
    source, notice = _media_source(attachment, media)
    if attachment.file_type == "video_note":
        if source is not None:
            media.has_notes = True
        return _circle_block(attachment, source, notice, message)

    if source is not None:
        media.has_voices = True
    return _voice_block(attachment, source, notice)


def _about_sender(message):
    """Подпись под просмотром: кто отправил и когда, одной строкой."""
    when = " в ".join(
        part for part in (format_date(message.sent_at), format_time(message.sent_at)) if part
    )
    about = escape(sender_name(message.user) or UNKNOWN_SENDER, quote=True)
    if when:
        about += f" &bull; {escape(when, quote=True)}"
    return about


def _shot_block(attachment, source, message):
    """Фото прямо в переписке: щелчок разворачивает его во весь экран.

    Автор, время и подпись едут в самом теге: просмотр один на страницу, и
    брать их ему больше неоткуда.
    """
    label = file_type_label(attachment.file_type)
    return (
        '<figure class="shot">'
        f'<img src="{source}" alt="{escape(label, quote=True)}" loading="lazy"'
        f' data-name="{escape(_save_name(attachment, "фото"), quote=True)}"'
        f' data-from="{_about_sender(message)}"'
        f' data-text="{escape(message.text or "", quote=True)}">'
        # Троеточия у снимка нет: скачивают его стрелкой из просмотра
        "</figure>"
    )


def _padded_clock(seconds):
    """Счётчик на кадре идёт с ведущим нулём — «00:03», как в переписке."""
    hours, rest = divmod(int(seconds or 0), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _clip_block(attachment, source, message):
    """Видео ведёт себя как кружок, GIF-анимация просто крутится, как в чате.

    Своих кнопок у видео нет: до щелчка оно крутится молча, щелчок включает
    его со звуком с начала, второй ставит паузу, а перематывают дорожкой по
    нижнему краю. Всё это делает скрипт — браузер сам так не умеет.
    """
    if attachment.file_type == "animation":
        player = f'<video src="{source}" preload="auto" playsinline autoplay muted loop></video>'
        return f'<div class="clip">{player}' + _sheet_block(attachment, "анимация") + "</div>"

    length = _padded_clock(attachment.duration_seconds)
    return (
        '<div class="clip">'
        '<div class="film" title="Нажмите, чтобы открыть">'
        f'<video src="{source}" preload="auto" playsinline muted loop'
        # Автор, время и подпись едут в самом теге: их покажет окно просмотра
        f' data-name="{escape(_save_name(attachment, "видео"), quote=True)}"'
        f' data-from="{_about_sender(message)}"'
        f' data-text="{escape(message.text or "", quote=True)}"></video>'
        # Счётчик и немой динамик стоят одной табличкой в углу кадра
        '<div class="film-badge">'
        f'<span class="film-time">{length}</span>'
        '<span class="film-mute">'
        '<svg viewBox="0 0 24 24">'
        '<path d="M4 9.2h3.7L12 5v14L7.7 14.8H4z"></path>'
        '<path class="cross" d="M15.6 9.6l5 4.8M20.6 9.6l-5 4.8"></path>'
        "</svg></span></div>"
        f'<span class="film-when">{escape(format_time(message.sent_at) or "")}</span>'
        '<div class="film-line"><div class="film-bar"></div></div>'
        "</div></div>"
    )


# Лист с загнутым уголком, как в Telegram: рисуем сами, чтобы не ходить в сеть
DOC_SIGN = (
    '<span class="doc-icon"><svg viewBox="0 0 24 24" aria-hidden="true">'
    # Вырезанный уголок — вторым контуром: с evenodd он становится дыркой
    '<path fill-rule="evenodd" d="M7.5 4h5.7L20 10.8v5.7A3.5 3.5 0 0 1 16.5 20'
    'h-9A3.5 3.5 0 0 1 4 16.5v-9A3.5 3.5 0 0 1 7.5 4Zm5.4 1.6v5.5h5.5z"/>'
    "</svg></span>"
)


def _doc_block(attachment, source):
    """Документ строкой: показать его нечем, а сохранить — один щелчок."""
    name = _save_name(attachment, "файл")
    about = format_size(_true_size(attachment))
    return (
        f'<a class="doc" href="{source}" download="{escape(name, quote=True)}"'
        f' data-size="{escape(about, quote=True)}">'
        f"{DOC_SIGN}"
        f'<span class="doc-body"><span class="doc-name">{escape(name)}</span>'
        f'<span class="meta">{escape(about)}</span></span></a>'
    )


def _attachment_line(attachment, notice=None):
    """Строка про то, чего в странице нет, — с причиной, куда оно делось."""
    parts = [file_type_label(attachment.file_type)]
    if attachment.original_filename:
        parts.append(attachment.original_filename)

    duration = format_duration(attachment.duration_seconds)
    if duration:
        parts.append(duration)
    parts.append(format_size(attachment.file_size))

    line = escape(" · ".join(parts))
    if attachment.file_path is None:
        return f'<li class="lost">{line} — {escape(FILE_NOT_SAVED)}</li>'
    if notice:
        return f'<li class="lost">{line} — {escape(notice)}</li>'
    return f"<li>{line}</li>"


def _file_block(attachment, media, message):
    """Фото, видео и документы: вшитые показываем, остальные отдаём строкой."""
    pictures = attachment.file_type in PICTURE_TYPES
    clips = attachment.file_type in CLIP_TYPES

    # Документ всё равно только качают, поэтому его тип браузеру не важен
    source, notice = _media_source(attachment, media, None if pictures or clips else ANY_FILE)
    if source is None:
        return None, notice

    if pictures:
        media.has_shots = True
        return _shot_block(attachment, source, message), None
    if clips:
        # У анимации плеера нет: она просто крутится, и скрипт ей не нужен
        if attachment.file_type != "animation":
            media.has_films = True
        return _clip_block(attachment, source, message), None

    media.has_docs = True
    return _doc_block(attachment, source), None


def _attachments_block(message, media):
    """Каждое вложение — своим видом, а что не вшилось — строкой под ними."""
    blocks = []
    lines = []
    for item in message.attachments:
        if item.file_type in PLAYER_TYPES:
            blocks.append(_player_block(item, media, message))
            continue

        block, notice = _file_block(item, media, message)
        if block is None:
            lines.append(_attachment_line(item, notice))
        else:
            blocks.append(block)

    if lines:
        blocks.append(f'<ul class="files">{"".join(lines)}</ul>')
    return "".join(blocks)


def _text_block(message, mark=""):
    """Текст с приклеенным к нему временем: оно встаёт в конец последней строки."""
    stamp = f'<span class="stamp">{mark}</span>'
    if message.text:
        return f'<p class="text">{escape(message.text)}{stamp}</p>'
    # У сообщения с одним вложением текста и не должно быть, подписывать нечего
    if message.attachments:
        return f'<div class="stamp alone">{mark}</div>'
    return f'<p class="text no-text">{escape(NO_TEXT)}{stamp}</p>'


def _message_block(message, by_telegram_id, media):
    author = sender_name(message.user) or UNKNOWN_SENDER
    edited = '<span class="edited">изменено</span>' if message.edited_at else ""
    mark = f"{edited}<time>{escape(format_time(message.sent_at) or '')}</time>"
    attachments = _attachments_block(message, media)
    forward = _forward_block(message)
    # У кружка и записи пометка о пересылке встаёт сбоку от них, как в переписке:
    # сверху её место занято именем, и двумя строками подряд они смотрятся лестницей
    if forward and 'class="note"' in attachments:
        attachments = f'<div class="aside">{attachments}{forward}</div>'
        forward = ""

    # У записи, снимка и кадра она идёт сбоку от всего пузыря: внутри его
    # обрезает скругление, а от края записи пометка свисает ниже её самой
    beside = ""
    if forward and any(
        kind in attachments for kind in ('class="shot"', 'class="clip"', 'class="voice"')
    ):
        beside, forward = forward, ""

    # Пузырь со снимком меряют по снимку, и подпись переносится по его ширине
    if 'class="shot"' in attachments:
        shaped = " by-shot"
    # А у документа и записи время уходит вбок, на одну высоту с весом файла
    elif 'class="doc"' in attachments:
        shaped = " by-doc"
    # Кружок и видео обходятся и вовсе без пузыря, но только если они сыграют
    elif 'class="note"' in attachments:
        shaped = " by-note"
    # А с подписью кадр возвращается в пузырь: без него текст висит сам по себе
    elif 'class="film"' in attachments:
        shaped = " by-shot" if message.text else " by-film"
    elif 'class="voice"' in attachments:
        shaped = " by-voice"
    else:
        shaped = ""

    bubble = (
        f'<article class="msg{shaped}">'
        f'<header><span class="author" style="color: {_author_color(author)}">'
        f"{escape(author)}</span></header>"
        + forward
        + _reply_block(message, by_telegram_id)
        + attachments
        + _text_block(message, mark)
        + _edits_block(message)
        + "</article>"
    )
    if beside:
        bubble = f'<div class="aside">{bubble}{beside}</div>'

    # Полоса, во всю ширину которой подсвечивается сообщение, — на обёртке:
    # у пузыря своя ширина, да и снимок он обрезает по себе
    return f'<div class="row" id="m{message.telegram_message_id}">{bubble}</div>'


def _event_block(event):
    """Служебное событие — серой строкой по центру, как системное в Telegram."""
    line = event_label(event)

    actor = sender_name(event.actor)
    if actor:
        line = f"{actor} — {line}"

    target = sender_name(event.target)
    if target and target != actor:
        line += f": {target}"
    if event.details:
        line += f" ({event.details})"

    return (
        f'<div class="event">{escape(line)} '
        f"<time>{escape(format_time(event.happened_at) or '')}</time></div>"
    )


def _head(chat_title, telegram_chat_id, period_label, messages, events, generated_at):
    """Шапка: то же, что в имени файла, только на виду и словами."""
    rows = [
        ("Период", period_label),
        ("telegram_chat_id", str(telegram_chat_id)),
        ("Сообщений", str(len(messages))),
    ]
    if events:
        rows.append(("Служебных событий", str(len(events))))
    rows.append(("Выгружено", generated_at.strftime(DATETIME_FORMAT)))

    cells = "".join(
        f"<dt>{escape(name)}</dt><dd>{escape(value)}</dd>" for name, value in rows
    )
    return (
        f'<div class="head"><h1>{escape(chat_title)}</h1><dl>{cells}</dl>'
        f'<p class="hint">{escape(ATTACHMENTS_NOTE)}</p></div>'
    )


def render_report(
    chat_title,
    telegram_chat_id,
    period_label,
    messages,
    events=(),
    generated_at=None,
):
    """Собирает страницу целиком. Возвращает готовый HTML строкой."""
    if generated_at is None:
        generated_at = datetime.now(display_timezone())

    title = f"{chat_title or telegram_chat_id} — {period_label}"
    by_telegram_id = {item.telegram_message_id: item for item in messages}
    media = _Media()

    blocks = []
    current_day = None
    for item in chronology(messages, events):
        day = format_date(moment(item))
        if day != current_day:
            # Разделитель между днями: иначе не видно, где закончилось вчера
            blocks.append(f'<div class="day">{escape(day or "")}</div>')
            current_day = day

        if isinstance(item, Message):
            blocks.append(_message_block(item, by_telegram_id, media))
        else:
            blocks.append(_event_block(item))

    if not blocks:
        blocks.append('<p class="empty">За этот период записей нет.</p>')

    body = "".join(blocks)

    script = ""
    # Скрипт перехода нужен там, где есть куда переходить
    if '<a class="reply"' in body:
        script += f"<script>{JUMP_SCRIPT}</script>"
    # А мини-кадр в цитате достаётся не каждому ответу
    if 'class="reply-mini' in body:
        script += f"<script>{MINI_SCRIPT}</script>"
    if media.has_notes:
        script += f"<script>{NOTES_SCRIPT}</script>"
    if media.has_voices:
        script += f"<script>{VOICE_SCRIPT}</script>"
    if media.has_films:
        script += f"<script>{CLIPS_SCRIPT}</script>"
    # Меню под троеточием есть и у видео, поэтому смотрим на саму страницу
    if 'class="sheet"' in body:
        script += f"<script>{MENU_SCRIPT}</script>"
    if media.has_shots or media.has_films:
        # Окно просмотра одно на страницу: в нём открываются и фото, и видео
        script = VIEWER + script
        script += f"<script>{VIEW_SCRIPT}</script>"
    if media.has_docs or 'class="sheet-save"' in body:
        # Вопрос перед сохранением тоже один на всю страницу
        script = ASK + script
        script += f"<script>{ASK_SCRIPT}</script>"

    return (
        "<!DOCTYPE html>\n"
        '<html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLES}</style></head>"
        '<body><div class="page">'
        + _head(
            chat_title or str(telegram_chat_id),
            telegram_chat_id,
            period_label,
            messages,
            events,
            generated_at,
        )
        + body
        + "</div>"
        + script
        + "</body></html>\n"
    )
