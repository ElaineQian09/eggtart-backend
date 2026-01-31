# device.py

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import get_db
from models import Device
from auth import verify_token


router = APIRouter()


class DeviceRequest(BaseModel):

    device_id: str
    device_model: str
    os: str
    language: str
    timezone: str


@router.post("/v1/devices")
def register_device(
    req: DeviceRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):

    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid token")

    token = authorization.replace("Bearer ", "")

    user_id = verify_token(token)

    existing = db.query(Device).filter(Device.id == req.device_id).first()

    if existing:
        if existing.user_id != user_id:
            raise HTTPException(409, "Device is already linked to another user")
        existing.device_model = req.device_model
        existing.os = req.os
        existing.language = req.language
        existing.timezone = req.timezone
        db.commit()
        device_id = existing.id
    else:
        device = Device(
            id=req.device_id,
            user_id=user_id,
            device_model=req.device_model,
            os=req.os,
            language=req.language,
            timezone=req.timezone
        )
        db.add(device)
        db.commit()
        device_id = device.id

    return {
        "message": "Device registered",
        "deviceId": device_id
    }
