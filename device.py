# device.py

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import get_db
from models import Device
from auth import verify_token
import uuid


router = APIRouter()


class DeviceRequest(BaseModel):

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

    device = Device(
        id=str(uuid.uuid4()),
        user_id=user_id,
        device_model=req.device_model,
        os=req.os,
        language=req.language,
        timezone=req.timezone
    )

    db.add(device)
    db.commit()

    return {
        "message": "Device registered",
        "deviceId": device.id
    }
