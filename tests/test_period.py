"""Разбор периода: формат, существование дат, будущее и границы суток."""

from datetime import datetime, timedelta

import pytest

from app.admin_bot.handlers.common import PeriodError, display_timezone, parse_period


def local_today():
    return datetime.now(display_timezone()).date()


def test_границы_переводятся_в_utc():
    date_from, date_to, *_ = parse_period("01.09.2026 - 02.09.2026")

    # Москва впереди UTC на три часа, поэтому местная полночь — это 21:00 накануне
    assert date_from == datetime(2026, 8, 31, 21, 0)
    assert date_to.date() == datetime(2026, 9, 2).date()


def test_последний_день_входит_целиком():
    _, date_to, *_ = parse_period("01.09.2026 - 01.09.2026")

    # Конец суток по Москве — 20:59:59 того же дня по UTC
    assert date_to.hour == 20
    assert date_to.minute == 59
    assert date_to.second == 59


def test_подписи_для_имени_файла():
    *_, label_from, label_to = parse_period("01.09.2026 - 14.09.2026")

    assert label_from == "01-09-2026"
    assert label_to == "14-09-2026"


def test_пробелы_вокруг_дефиса_необязательны():
    assert parse_period("01.09.2026-02.09.2026") == parse_period(
        "01.09.2026 - 02.09.2026"
    )


@pytest.mark.parametrize(
    "text",
    ["2026-09-01 - 2026-09-02", "01.09.26 - 02.09.26", "вчера", "", "01.09.2026"],
)
def test_неверный_формат(text):
    with pytest.raises(PeriodError, match="Неверный формат"):
        parse_period(text)


def test_несуществующая_дата():
    with pytest.raises(PeriodError, match="Некорректная дата"):
        parse_period("32.01.2026 - 01.02.2026")


def test_начало_позже_конца():
    with pytest.raises(PeriodError, match="не может быть позже"):
        parse_period("02.09.2026 - 01.09.2026")


def test_один_будущий_день():
    tomorrow = (local_today() + timedelta(days=1)).strftime("%d.%m.%Y")

    with pytest.raises(PeriodError, match="ещё не наступила"):
        parse_period(f"{tomorrow} - {tomorrow}")


def test_период_целиком_в_будущем():
    start = (local_today() + timedelta(days=1)).strftime("%d.%m.%Y")
    end = (local_today() + timedelta(days=5)).strftime("%d.%m.%Y")

    with pytest.raises(PeriodError, match="целиком в будущем"):
        parse_period(f"{start} - {end}")


def test_сегодняшний_день_разрешён():
    today = local_today().strftime("%d.%m.%Y")

    date_from, date_to, *_ = parse_period(f"{today} - {today}")

    assert date_from < date_to


def test_период_дотягивающийся_до_будущего_разрешён():
    """Начало в прошлом — запрос осмысленный, даже если конец ещё не наступил."""
    start = (local_today() - timedelta(days=3)).strftime("%d.%m.%Y")
    end = (local_today() + timedelta(days=3)).strftime("%d.%m.%Y")

    date_from, date_to, *_ = parse_period(f"{start} - {end}")

    assert date_from < date_to
