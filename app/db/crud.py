from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from app.db.models import (
    Admin,
    Attachment,
    Chat,
    Message,
    Reaction,
    TelegramUser,
)


def get_or_create_chat(db: Session, telegram_chat_id, title):
    chat = db.query(Chat).filter(Chat.telegram_chat_id == telegram_chat_id).first()
    if chat is not None:
        return chat
    chat = Chat(telegram_chat_id=telegram_chat_id, title=title)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def get_chats_overview(db: Session):
    """Чаты со счётчиком сообщений и датой последнего, активные сверху."""
    return (
        db.query(
            Chat.telegram_chat_id,
            Chat.title,
            func.count(Message.id),
            func.max(Message.sent_at),
        )
        .outerjoin(Message, Message.chat_id == Chat.id)
        .group_by(Chat.id, Chat.telegram_chat_id, Chat.title)
        .order_by(func.max(Message.sent_at).desc().nullslast())
        .all()
    )


def get_or_create_user(db: Session, telegram_user_id, username, first_name, last_name):
    user = (
        db.query(TelegramUser)
        .filter(TelegramUser.telegram_user_id == telegram_user_id)
        .first()
    )
    if user is not None:
        return user
    user = TelegramUser(
        telegram_user_id=telegram_user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_message(
    db: Session, telegram_message_id, chat_id, user_id, text, sent_at, edited_at=None
):
    message = Message(
        telegram_message_id=telegram_message_id,
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        sent_at=sent_at,
        edited_at=edited_at,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def get_message_by_telegram_id(db: Session, chat_id, telegram_message_id):
    return (
        db.query(Message)
        .filter(
            Message.chat_id == chat_id,
            Message.telegram_message_id == telegram_message_id,
        )
        .first()
    )


def get_messages_by_period(db: Session, chat_id, date_from, date_to):
    return (
        db.query(Message)
        .options(joinedload(Message.attachments), joinedload(Message.reactions))
        .filter(
            Message.chat_id == chat_id,
            Message.sent_at >= date_from,
            Message.sent_at <= date_to,
        )
        .order_by(Message.sent_at, Message.telegram_message_id)
        .all()
    )


def search_messages(db: Session, chat_id, keyword):
    return (
        db.query(Message)
        .options(joinedload(Message.attachments), joinedload(Message.reactions))
        .filter(
            Message.chat_id == chat_id,
            Message.text.ilike(f"%{keyword}%"),
        )
        .order_by(Message.sent_at, Message.telegram_message_id)
        .all()
    )


def update_message_text(db: Session, message_id, new_text, edited_at):
    message = db.query(Message).filter(Message.id == message_id).first()
    if message is None:
        return None
    message.text = new_text
    message.edited_at = edited_at
    db.commit()
    db.refresh(message)
    return message


def create_attachment(db: Session, message_id, file_type, file_path, original_filename):
    attachment = Attachment(
        message_id=message_id,
        file_type=file_type,
        file_path=file_path,
        original_filename=original_filename,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return attachment


def create_reaction(db: Session, message_id, user_id, emoji, reacted_at):
    reaction = Reaction(
        message_id=message_id,
        user_id=user_id,
        emoji=emoji,
        reacted_at=reacted_at,
    )
    db.add(reaction)
    db.commit()
    db.refresh(reaction)
    return reaction


def delete_reaction(db: Session, message_id, user_id, emoji):
    reaction = (
        db.query(Reaction)
        .filter(
            Reaction.message_id == message_id,
            Reaction.user_id == user_id,
            Reaction.emoji == emoji,
        )
        .first()
    )
    if reaction is None:
        return False
    db.delete(reaction)
    db.commit()
    return True


def is_admin(db: Session, telegram_user_id):
    admin = db.query(Admin).filter(Admin.telegram_user_id == telegram_user_id).first()
    return admin is not None


def add_admin(db: Session, telegram_user_id):
    admin = db.query(Admin).filter(Admin.telegram_user_id == telegram_user_id).first()
    if admin is not None:
        return admin
    admin = Admin(telegram_user_id=telegram_user_id)
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin