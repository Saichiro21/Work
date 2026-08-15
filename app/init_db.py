from app.db.db import init_tables, SessionLocal
from app.db.crud import create_category, create_book

init_tables()
db = SessionLocal()

fiction = create_category(db, "Фантастика")
history = create_category(db, "История")

create_book(db, "Дюна", "Роман про пустынную планету", 25.99, fiction.id)
create_book(db, "Марсианин", "Выживание на Марсе", 19.50, fiction.id)
create_book(db, "1984", "Антиутопия", 15.00, fiction.id)

create_book(db, "Sapiens", "Краткая история человечества", 22.30, history.id)
create_book(db, "Оружие, микробы и сталь", "История цивилизаций", 18.75, history.id)

db.close()
print("База данных заполнена!")