# auth.py

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session
from database import SessionLocal, get_db
from models import User, Device
import uuid
import jwt
import datetime
import os
from pydantic import BaseModel
from typing import Optional


router = APIRouter()

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-this-in-production-32bytes-minimum")
ALGORITHM = "HS256"
DEBUG_DEVICE_LOOKUP_ENABLED = os.getenv("DEBUG_DEVICE_LOOKUP_ENABLED", "0") == "1"


def create_token(user_id: str):

    payload = {
        "user_id": user_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=30)
    }

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(401, "Invalid token")
    # Self-heal migrated databases: token may reference a user missing in `users`.
    # Ensuring here prevents downstream FK failures in all authenticated endpoints.
    db = SessionLocal()
    try:
        _ensure_user_exists(db, user_id)
    finally:
        db.close()
    return user_id


class AnonymousLoginRequest(BaseModel):

    device_id: str
    device_model: Optional[str] = None
    os: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None


def _ensure_user_exists(db: Session, user_id: str) -> None:
    existing_user = db.query(User).filter(User.id == user_id).first()
    if existing_user:
        return
    db.add(User(id=user_id))
    db.commit()


@router.post("/v1/auth/anonymous")
def anonymous_login(req: AnonymousLoginRequest, db: Session = Depends(get_db)):

    if not req.device_id:
        raise HTTPException(400, "device_id is required")

    device = db.query(Device).filter(Device.id == req.device_id).first()

    if device:
        user_id = device.user_id
    else:
        user_id = str(uuid.uuid4())
        user = User(id=user_id)
        device = Device(
            id=req.device_id,
            user_id=user_id,
            device_model=req.device_model,
            os=req.os,
            language=req.language,
            timezone=req.timezone
        )
        db.add(user)
        db.add(device)
        db.commit()

    # Self-heal migrated environments where devices row exists but users row is missing.
    _ensure_user_exists(db, user_id)

    token = create_token(user_id)

    return {
        "userId": user_id,
        "token": token,
        "deviceId": req.device_id
    }


@router.get("/v1/auth/whoami")
def whoami(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid token")
    token = authorization.replace("Bearer ", "", 1)
    user_id = verify_token(token)
    return {"userId": user_id}


@router.get("/v1/debug/device-bindings")
def debug_device_bindings(
    authorization: str = Header(...),
    device_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    if not DEBUG_DEVICE_LOOKUP_ENABLED:
        raise HTTPException(404, "Not Found")
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid token")
    token = authorization.replace("Bearer ", "", 1)
    requester_user_id = verify_token(token)

    query = db.query(Device)
    if device_id:
        query = query.filter(Device.id == device_id)
    else:
        query = query.filter(Device.user_id == requester_user_id)
    devices = query.order_by(Device.created_at.desc()).limit(limit).all()

    users = (
        db.query(User)
        .order_by(User.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "requesterUserId": requester_user_id,
        "deviceBindings": [
            {
                "deviceId": d.id,
                "userId": d.user_id,
                "deviceModel": d.device_model,
                "os": d.os,
                "language": d.language,
                "timezone": d.timezone,
                "createdAt": d.created_at.isoformat() if d.created_at else None,
            }
            for d in devices
        ],
        "recentUsers": [
            {
                "userId": u.id,
                "createdAt": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ],
    }
