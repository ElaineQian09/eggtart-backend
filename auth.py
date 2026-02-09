# auth.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
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
    return user_id


class AnonymousLoginRequest(BaseModel):

    device_id: str
    device_model: Optional[str] = None
    os: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None


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

    token = create_token(user_id)

    return {
        "userId": user_id,
        "token": token,
        "deviceId": req.device_id
    }
