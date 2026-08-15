from sqlalchemy.orm import Session, joinedload
from app.db.models import Category, Book


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