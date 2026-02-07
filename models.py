# models.py

from sqlalchemy import Column, String, DateTime, ForeignKey, Float, Boolean, Date
from database import Base
import datetime


class User(Base):

    __tablename__ = "users"

    id = Column(String, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Device(Base):

    __tablename__ = "devices"

    id = Column(String, primary_key=True, index=True)

    user_id = Column(String, ForeignKey("users.id"))

    device_model = Column(String)
    os = Column(String)
    language = Column(String)
    timezone = Column(String)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Memory(Base):

    __tablename__ = "memories"

    id = Column(String, primary_key=True, index=True)

    user_id = Column(String, ForeignKey("users.id"))

    type = Column(String)
    content = Column(String)

    importance = Column(Float)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Event(Base):

    __tablename__ = "events"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id"))
    device_id = Column(String, ForeignKey("devices.id"))

    recording_url = Column(String, nullable=True)
    transcript = Column(String, nullable=True)
    duration_sec = Column(Float, default=0)
    event_at = Column(DateTime, default=datetime.datetime.utcnow)
    status = Column(String, default="pending")

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow
    )


class EggbookIdea(Base):

    __tablename__ = "eggbook_ideas"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id"))

    title = Column(String)
    content = Column(String)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow
    )


class EggbookTodo(Base):

    __tablename__ = "eggbook_todos"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id"))

    title = Column(String)
    is_accepted = Column(Boolean, default=False)
    is_pinned = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow
    )


class EggbookNotification(Base):

    __tablename__ = "eggbook_notifications"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id"))
    todo_id = Column(String, ForeignKey("eggbook_todos.id"), nullable=True)

    title = Column(String)
    notify_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow
    )


class EggbookComment(Base):

    __tablename__ = "eggbook_comments"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.id"))

    content = Column(String)
    egg_name = Column(String, nullable=True)
    egg_comment = Column(String, nullable=True)
    date = Column(Date, default=datetime.date.today)
    is_community = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
