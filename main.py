# main.py

from fastapi import FastAPI
from sqlalchemy import inspect, text

from database import engine
from models import Base

from auth import router as auth_router
from device import router as device_router
from memory import router as memory_router
from event import router as event_router
from eggbook import router as eggbook_router

app = FastAPI(
    title="Egg Backend",
    version="1.0"
)


@app.on_event("startup")
def create_tables():
    # Ensure newly added models are created in existing databases.
    Base.metadata.create_all(bind=engine)
    _migrate_eggbook_comments_columns()


def _migrate_eggbook_comments_columns():
    inspector = inspect(engine)
    if "eggbook_comments" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("eggbook_comments")}
    statements = []
    if "egg_name" not in existing_columns:
        statements.append("ALTER TABLE eggbook_comments ADD COLUMN egg_name VARCHAR")
    if "egg_comment" not in existing_columns:
        statements.append("ALTER TABLE eggbook_comments ADD COLUMN egg_comment VARCHAR")

    if not statements:
        return

    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


app.include_router(auth_router)
app.include_router(device_router)
app.include_router(memory_router)
app.include_router(event_router)
app.include_router(eggbook_router)


@app.get("/")
def health_check():

    return {
        "status": "ok",
        "service": "Egg Backend"
    }
