from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from app.db.db import Base


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)

    books = relationship("Book", back_populates="category")


class Book(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(String)
    price = Column(Numeric(10, 2))
    url = Column(String)
    category_id = Column(Integer, ForeignKey("categories.id"))

    category = relationship("Category", back_populates="books")


class Chat(Base):
    __tablename__ = "chats"

    id = Column(Integer, primary_key=True)
    telegram_chat_id = Column(BigInteger, unique=True, nullable=False)
    title = Column(String)
    added_at = Column(DateTime, default=datetime.utcnow)

    messages = relationship("Message", back_populates="chat")


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

    chat = relationship("Chat", back_populates="messages")
    user = relationship("TelegramUser", back_populates="messages")
    attachments = relationship("Attachment", back_populates="message")
    reactions = relationship("Reaction", back_populates="message")


class Attachment(Base):
    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False)
    file_type = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    original_filename = Column(String)

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
    added_at = Column(DateTime, default=datetime.utcnow)