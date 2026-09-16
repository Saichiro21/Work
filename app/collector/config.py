import os

from dotenv import load_dotenv


def get_token():
    load_dotenv()
    token = os.getenv("COLLECTOR_BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задан COLLECTOR_BOT_TOKEN в .env")
    return token


def get_collected_chat_ids():
    """COLLECTOR_CHAT_IDS — белый список чатов. Пустой: собираем из всех групп."""
    load_dotenv()
    raw_ids = os.getenv("COLLECTOR_CHAT_IDS", "")
    chat_ids = set()
    for item in raw_ids.split(","):
        item = item.strip()
        if item:
            chat_ids.add(int(item))
    return chat_ids
