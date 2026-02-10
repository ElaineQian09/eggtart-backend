# main.py

import os
import time

from fastapi import FastAPI, Header, HTTPException, Query
from sqlalchemy import inspect, text

from auth import verify_token
from ai_pipeline import get_ai_runtime_snapshot
from database import engine
from models import Base

from auth import router as auth_router
from device import router as device_router
from memory import router as memory_router
from event import router as event_router
from eggbook import router as eggbook_router
from uploads import router as uploads_router
from realtime import router as realtime_router

app = FastAPI(
    title="Egg Backend",
    version="1.0"
)
APP_STARTED_AT = time.time()
DEBUG_HEALTH_ENABLED = os.getenv("DEBUG_HEALTH_ENABLED", "0") == "1"
DEBUG_RESET_ENABLED = os.getenv("DEBUG_RESET_ENABLED", "0") == "1"
DEBUG_RESET_KEY = os.getenv("DEBUG_RESET_KEY", "")


@app.on_event("startup")
def create_tables():
    # Ensure newly added models are created in existing databases.
    Base.metadata.create_all(bind=engine)
    _migrate_eggbook_comments_columns()
    _migrate_eggbook_ideas_columns()
    _migrate_events_columns()


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


def _migrate_events_columns():
    inspector = inspect(engine)
    if "events" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("events")}
    statements = []
    if "audio_url" not in existing_columns:
        statements.append("ALTER TABLE events ADD COLUMN audio_url VARCHAR")
    if "screen_recording_url" not in existing_columns:
        statements.append("ALTER TABLE events ADD COLUMN screen_recording_url VARCHAR")

    if not statements:
        return

    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def _migrate_eggbook_ideas_columns():
    inspector = inspect(engine)
    if "eggbook_ideas" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("eggbook_ideas")}
    statements = []
    if "source_event_id" not in existing_columns:
        statements.append("ALTER TABLE eggbook_ideas ADD COLUMN source_event_id VARCHAR")
    if "screen_recording_url" not in existing_columns:
        statements.append("ALTER TABLE eggbook_ideas ADD COLUMN screen_recording_url VARCHAR")
    if "recording_url" not in existing_columns:
        statements.append("ALTER TABLE eggbook_ideas ADD COLUMN recording_url VARCHAR")
    if "audio_url" not in existing_columns:
        statements.append("ALTER TABLE eggbook_ideas ADD COLUMN audio_url VARCHAR")

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
app.include_router(uploads_router)
app.include_router(realtime_router)


@app.get("/")
def health_check():

    return {
        "status": "ok",
        "service": "Egg Backend"
    }


@app.get("/v1/debug/health")
def debug_health(authorization: str = Header(...)):
    if not DEBUG_HEALTH_ENABLED:
        raise HTTPException(404, "Not Found")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid token")

    token = authorization.replace("Bearer ", "", 1)
    user_id = verify_token(token)

    db_ok = True
    db_error = None
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        db_ok = False
        db_error = str(exc)[:500]

    return {
        "status": "ok" if db_ok else "degraded",
        "uptimeSec": round(time.time() - APP_STARTED_AT, 3),
        "db": {
            "ok": db_ok,
            "dialect": engine.dialect.name,
            "driver": engine.dialect.driver,
            "error": db_error,
        },
        "aiQueue": get_ai_runtime_snapshot(user_id=user_id),
    }


@app.post("/v1/debug/reset-data")
def debug_reset_data(
    authorization: str = Header(...),
    scope: str = Query(default="events"),  # events | all
    x_debug_reset_key: str | None = Header(default=None),
):
    if not DEBUG_RESET_ENABLED:
        raise HTTPException(404, "Not Found")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid token")
    token = authorization.replace("Bearer ", "", 1)
    _ = verify_token(token)

    if DEBUG_RESET_KEY and x_debug_reset_key != DEBUG_RESET_KEY:
        raise HTTPException(403, "Invalid reset key")

    if scope not in {"events", "all"}:
        raise HTTPException(400, "Invalid scope")

    table_order = [
        "eggbook_comment_generations",
        "eggbook_comments",
        "eggbook_notifications",
        "eggbook_todos",
        "eggbook_ideas",
        "events",
    ]
    if scope == "all":
        table_order.extend(["memories", "devices", "users"])

    existing_tables = set(inspect(engine).get_table_names())
    target_tables = [t for t in table_order if t in existing_tables]

    if not target_tables:
        return {"ok": True, "scope": scope, "clearedTables": []}

    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            table_sql = ", ".join(target_tables)
            conn.execute(text(f"TRUNCATE TABLE {table_sql} CASCADE"))
        else:
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            for table in target_tables:
                conn.execute(text(f"DELETE FROM {table}"))
            conn.execute(text("PRAGMA foreign_keys=ON"))

    return {"ok": True, "scope": scope, "clearedTables": target_tables}
