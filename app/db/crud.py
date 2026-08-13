from sqlalchemy.orm import joinedload
from app.db.db import get_session
from app.db.models import Category, Book


def create_category(title):
    session = get_session()
    category = Category(title=title)
    session.add(category)
    session.commit()
    session.refresh(category)
    session.close()
    return category


def create_book(title, description, price, category_id, url=""):
    session = get_session()
    book = Book(
        title=title,
        description=description,
        price=price,
        url=url,
        category_id=category_id,
    )
    session.add(book)
    session.commit()
    session.refresh(book)
    session.close()
    return book


def get_all_categories():
    session = get_session()
    categories = session.query(Category).all()
    session.close()
    return categories


def get_all_books():
    session = get_session()
    books = session.query(Book).options(joinedload(Book.category)).all()
    session.close()
    return books