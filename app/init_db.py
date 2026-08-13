from app.db.db import init_tables
from app.db.crud import create_category, create_book

init_tables()

fiction = create_category("Фантастика")
history = create_category("История")

create_book("Дюна", "Роман про пустынную планету", 25.99, fiction.id)
create_book("Марсианин", "Выживание на Марсе", 19.50, fiction.id)
create_book("1984", "Антиутопия", 15.00, fiction.id)

create_book("Sapiens", "Краткая история человечества", 22.30, history.id)
create_book("Оружие, микробы и сталь", "История цивилизаций", 18.75, history.id)

print("База данных заполнена!")