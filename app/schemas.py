from pydantic import BaseModel
from typing import Optional


class CategoryBase(BaseModel):
    title: str


class CategoryCreate(CategoryBase):
    pass


class CategoryResponse(CategoryBase):
    id: int

    class Config:
        from_attributes = True


class BookBase(BaseModel):
    title: str
    description: Optional[str] = None
    price: float
    url: Optional[str] = ""
    category_id: int


class BookCreate(BookBase):
    pass


class BookResponse(BookBase):
    id: int

    class Config:
        from_attributes = True