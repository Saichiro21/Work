from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from app.db.db import Base


def utc_now():
    """Время в UTC без таймзоны — как хранят все колонки DateTime в проекте.

    datetime.utcnow() для этого не годится: он объявлен устаревшим.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Chat(Base):
    __tablename__ = "chats"

    id = Column(Integer, primary_key=True)
    telegram_chat_id = Column(BigInteger, unique=True, nullable=False)
    title = Column(String)
    added_at = Column(DateTime, default=utc_now)

    messages = relationship("Message", back_populates="chat")
    events = relationship("ChatEvent", back_populates="chat")


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id = Column(Integer, primary_key=True)
    telegram_user_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String)
    first_name = Column(String)
    last_name = Column(String)

    messages = relationship("Message", back_populates="user")
    reactions = relationship("Reaction", back_populates="user")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    telegram_message_id = Column(BigInteger, nullable=False)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("telegram_users.id"))
    text = Column(Text)
    sent_at = Column(DateTime, nullable=False)
    edited_at = Column(DateTime)
    # Telegram-id сообщения, на которое отвечают. Родителя может не быть в базе,
    # если он отправлен до того, как бота добавили в чат, поэтому это не внешний ключ
    reply_to_message_id = Column(BigInteger)
    # Источник пересылки: user, hidden_user, chat или channel. В user_id при
    # пересылке лежит тот, кто переслал, поэтому исходный автор нужен отдельно
    forward_origin_type = Column(String)
    forward_from_name = Column(String)
    # У скрытых авторов id нет — Telegram его не отдаёт
    forward_from_id = Column(BigInteger)
    forward_origin_date = Column(DateTime)
    # Пост канала, попавший в связанную группу обсуждений: формально пересылка,
    # но пересылал его не человек
    is_automatic_forward = Column(Boolean, nullable=False, default=False)

    chat = relationship("Chat", back_populates="messages")
    user = relationship("TelegramUser", back_populates="messages")
    attachments = relationship("Attachment", back_populates="message")
    reactions = relationship("Reaction", back_populates="message")
    versions = relationship(
        "MessageVersion", back_populates="message", order_by="MessageVersion.replaced_at"
    )


class MessageVersion(Base):
    """Прежние редакции сообщения. Текущая версия лежит в messages.text."""

    __tablename__ = "message_versions"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False)
    text = Column(Text)
    replaced_at = Column(DateTime, nullable=False)

    message = relationship("Message", back_populates="versions")


class ForwardOriginType:
    """Значения Message.forward_origin_type — как их называет Telegram."""

    USER = "user"
    HIDDEN_USER = "hidden_user"
    CHAT = "chat"
    CHANNEL = "channel"


class ChatEventType:
    """Значения ChatEvent.event_type.

    Лежат рядом с моделью, потому что это общий словарь двух модулей: коллектор
    события пишет, админский бот читает, а напрямую они друг о друге не знают.
    """

    MEMBER_JOINED = "member_joined"
    MEMBER_LEFT = "member_left"
    TITLE_CHANGED = "title_changed"
    PHOTO_CHANGED = "photo_changed"
    PHOTO_DELETED = "photo_deleted"
    MESSAGE_PINNED = "message_pinned"
    CHAT_CREATED = "chat_created"
    MIGRATED_TO = "migrated_to"
    MIGRATED_FROM = "migrated_from"
    AUTO_DELETE_CHANGED = "auto_delete_changed"


class ChatEvent(Base):
    """Служебные события чата: вход и выход участников, смена названия, закрепления.

    Telegram присылает их обычными сообщениями без текста, но это не переписка,
    поэтому и лежат они отдельно — иначе портили бы выгрузку и счётчики сообщений.
    """

    __tablename__ = "chat_events"

    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    telegram_message_id = Column(BigInteger, nullable=False)
    event_type = Column(String, nullable=False)
    # Кто совершил действие и над кем. Если совпадают — участник вошёл или вышел сам,
    # если различаются — его добавили или исключили
    actor_user_id = Column(Integer, ForeignKey("telegram_users.id"))
    target_user_id = Column(Integer, ForeignKey("telegram_users.id"))
    # Telegram-id закреплённого сообщения
    target_message_id = Column(BigInteger)
    # Подробности события: новое название, отрывок закреплённого текста, новый id чата
    details = Column(Text)
    happened_at = Column(DateTime, nullable=False)

    chat = relationship("Chat", back_populates="events")
    actor = relationship("TelegramUser", foreign_keys=[actor_user_id])
    target = relationship("TelegramUser", foreign_keys=[target_user_id])


class Attachment(Base):
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False)
    file_type = Column(String, nullable=False)
    # Пусто, если файл скачать не удалось: сам факт вложения всё равно важен для аудита
    file_path = Column(String)
    original_filename = Column(String)
    file_size = Column(BigInteger)
    # Только у того, что играется: голосовых, кружков, видео и аудио
    duration_seconds = Column(Integer)

    message = relationship("Message", back_populates="attachments")


class Reaction(Base):
    __tablename__ = "reactions"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("telegram_users.id"), nullable=False)
    emoji = Column(String, nullable=False)
    reacted_at = Column(DateTime, nullable=False)

    message = relationship("Message", back_populates="reactions")
    user = relationship("TelegramUser", back_populates="reactions")


class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True)
    telegram_user_id = Column(BigInteger, unique=True, nullable=False)
    added_at = Column(DateTime, default=utc_now)