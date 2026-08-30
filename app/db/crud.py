from sqlalchemy.orm import Session, joinedload
from app.db.models import (
    Admin,
    Attachment,
    Book,
    Category,
    Chat,
    Message,
    Reaction,
    TelegramUser,
)


def create_category(db: Session, title: str):
    category = Category(title=title)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def get_all_categories(db: Session):
    return db.query(Category).all()


def get_category(db: Session, category_id: int):
    return db.query(Category).filter(Category.id == category_id).first()


def update_category(db: Session, category_id: int, title: str):
    category = get_category(db, category_id)
    if category is None:
        return None
    category.title = title
    db.commit()
    db.refresh(category)
    return category


def delete_category(db: Session, category_id: int):
    category = get_category(db, category_id)
    if category is None:
        return False
    db.delete(category)
    db.commit()
    return True


def create_book(db: Session, title, description, price, category_id, url=""):
    book = Book(
        title=title,
        description=description,
        price=price,
        url=url,
        category_id=category_id,
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


def get_all_books(db: Session, category_id: int = None):
    query = db.query(Book).options(joinedload(Book.category))
    if category_id is not None:
        query = query.filter(Book.category_id == category_id)
    return query.all()


def get_book(db: Session, book_id: int):
    return (
        db.query(Book)
        .options(joinedload(Book.category))
        .filter(Book.id == book_id)
        .first()
    )


def update_book(db: Session, book_id: int, title, description, price, category_id, url=""):
    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        return None
    book.title = title
    book.description = description
    book.price = price
    book.url = url
    book.category_id = category_id
    db.commit()
    db.refresh(book)
    return book


def delete_book(db: Session, book_id: int):
    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        return False
    db.delete(book)
    db.commit()
    return True


def get_or_create_chat(db: Session, telegram_chat_id, title):
    chat = db.query(Chat).filter(Chat.telegram_chat_id == telegram_chat_id).first()
    if chat is not None:
        return chat
    chat = Chat(telegram_chat_id=telegram_chat_id, title=title)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


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


def create_message(db: Session, telegram_message_id, chat_id, user_id, text, sent_at):
    message = Message(
        telegram_message_id=telegram_message_id,
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        sent_at=sent_at,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def get_messages_by_period(db: Session, chat_id, date_from, date_to):
    return (
        db.query(Message)
        .options(joinedload(Message.attachments), joinedload(Message.reactions))
        .filter(
            Message.chat_id == chat_id,
            Message.sent_at >= date_from,
            Message.sent_at <= date_to,
        )
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