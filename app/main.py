from fastapi import FastAPI

from app.api import categories, books

app = FastAPI(title="Books API")

app.include_router(categories.router)
app.include_router(books.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}