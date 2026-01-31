# models.py

from sqlalchemy import Column, String, DateTime, ForeignKey, Float
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
