from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from app.db.db import get_db
from app.db import crud
from app.schemas import BookCreate, BookResponse

router = APIRouter(prefix="/books", tags=["books"])


@router.get("/", response_model=list[BookResponse])
def list_books(category_id: Optional[int] = None, db: Session = Depends(get_db)):
    return crud.get_all_books(db, category_id=category_id)


@router.get("/{book_id}", response_model=BookResponse)
def get_book(book_id: int, db: Session = Depends(get_db)):
    book = crud.get_book(db, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    return book


@router.post("/", response_model=BookResponse, status_code=201)
def create_book(book: BookCreate, db: Session = Depends(get_db)):
    category = crud.get_category(db, book.category_id)
    if category is None:
        raise HTTPException(status_code=400, detail="Указанная категория не существует")
    return crud.create_book(
        db, book.title, book.description, book.price, book.category_id, book.url
    )


@router.put("/{book_id}", response_model=BookResponse)
def update_book(book_id: int, book: BookCreate, db: Session = Depends(get_db)):
    category = crud.get_category(db, book.category_id)
    if category is None:
        raise HTTPException(status_code=400, detail="Указанная категория не существует")
    updated = crud.update_book(
        db, book_id, book.title, book.description, book.price, book.category_id, book.url
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Книга не найдена")
    return updated


@router.delete("/{book_id}", status_code=204)
def delete_book(book_id: int, db: Session = Depends(get_db)):
    deleted = crud.delete_book(db, book_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Книга не найдена")
        