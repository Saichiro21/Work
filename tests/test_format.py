"""Подписи, которые читает человек: размер файла и пояс показа времени."""

import os

from app.admin_bot.handlers.common import display_timezone, format_size, machine_timezone


def test_размер_показывают_с_десятыми_но_без_пустого_хвоста():
    """«467 КБ» скрывало настоящий вес, а «52,0 КБ» читалось бы хуже прежнего."""
    assert format_size(478632) == "467,4 КБ"
    assert format_size(52 * 1024) == "52 КБ"
    assert format_size(700) == "700 Б"
    assert format_size(45 * 1024 * 1024 + 300 * 1024) == "45,3 МБ"


def test_без_настройки_время_показывают_в_поясе_машины(monkeypatch):
    """С чужим поясом выгрузка расходится с тем, что человек видит в Telegram."""
    monkeypatch.delenv("DISPLAY_TIMEZONE", raising=False)
    display_timezone.cache_clear()

    assert display_timezone() == machine_timezone()


def test_настройка_пояс_машины_перебивает(monkeypatch):
    """Сервер часто живёт в UTC, а читают выгрузку в своём поясе."""
    monkeypatch.setenv("DISPLAY_TIMEZONE", "Asia/Shanghai")
    display_timezone.cache_clear()

    assert str(display_timezone()) == "Asia/Shanghai"


def test_неизвестный_пояс_не_роняет_выгрузку(monkeypatch):
    """Опечатка в .env не должна превращать время в UTC молча и навсегда."""
    monkeypatch.setenv("DISPLAY_TIMEZONE", "Europe/Атлантида")
    display_timezone.cache_clear()

    assert display_timezone() == machine_timezone()
