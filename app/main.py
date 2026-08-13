from app.db.crud import get_all_categories, get_all_books

categories = get_all_categories()
books = get_all_books()

print("=== Категории ===")
for c in categories:
    print(f"{c.id}: {c.title}")

print("\n=== Книги ===")
for b in books:
    print(f"{b.id}: {b.title} | {b.price}$ | категория: {b.category.title}")