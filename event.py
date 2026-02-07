from datetime import datetime, timezone
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import verify_token
from database import get_db
from models import Device, Event


router = APIRouter()

EVENT_STATUS_PENDING = "pending"
EVENT_STATUS_TRANSCRIBING = "transcribing"
EVENT_STATUS_PROCESSED = "processed"
EVENT_STATUS_FAILED = "failed"
VALID_EVENT_STATUSES = {
    EVENT_STATUS_PENDING,
    EVENT_STATUS_TRANSCRIBING,
    EVENT_STATUS_PROCESSED,
    EVENT_STATUS_FAILED,
}


def get_user_id(authorization: str) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid token")
    token = authorization.replace("Bearer ", "")
    return verify_token(token)


def event_to_dict(event: Event):
    return {
        "eventId": event.id,
        "deviceId": event.device_id,
        "recordingUrl": event.recording_url,
        "transcript": event.transcript,
        "durationSec": int(event.duration_sec or 0),
        "eventAt": event.event_at.isoformat(),
        "status": event.status,
        "createdAt": event.created_at.isoformat(),
        "updatedAt": event.updated_at.isoformat(),
    }


def infer_status(recording_url: Optional[str], transcript: Optional[str]) -> str:
    if transcript:
        return EVENT_STATUS_PROCESSED
    if recording_url:
        return EVENT_STATUS_TRANSCRIBING
    return EVENT_STATUS_PENDING


class EventCreateRequest(BaseModel):
    device_id: str
    recording_url: Optional[str] = None
    transcript: Optional[str] = None
    duration_sec: Optional[int] = 0
    event_at: Optional[datetime] = None


class EventUpdateRequest(BaseModel):
    recording_url: Optional[str] = None
    transcript: Optional[str] = None
    duration_sec: Optional[int] = None
    event_at: Optional[datetime] = None
    status: Optional[str] = None


@router.post("/v1/events")
def create_event(
    req: EventCreateRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    device = (
        db.query(Device)
        .filter(Device.id == req.device_id, Device.user_id == user_id)
        .first()
    )
    if not device:
        raise HTTPException(404, "Device not found")

    event_at = req.event_at or datetime.now(timezone.utc)
    status = infer_status(req.recording_url, req.transcript)

    event = Event(
        id=str(uuid.uuid4()),
        user_id=user_id,
        device_id=req.device_id,
        recording_url=req.recording_url,
        transcript=req.transcript,
        duration_sec=float(req.duration_sec or 0),
        event_at=event_at,
        status=status,
    )
    db.add(event)
    db.commit()
    return {"eventId": event.id, "status": event.status}


@router.patch("/v1/events/{event_id}")
def update_event(
    event_id: str,
    req: EventUpdateRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.user_id == user_id)
        .first()
    )
    if not event:
        raise HTTPException(404, "Event not found")

    if req.recording_url is not None:
        event.recording_url = req.recording_url
    if req.transcript is not None:
        event.transcript = req.transcript
    if req.duration_sec is not None:
        event.duration_sec = float(req.duration_sec)
    if req.event_at is not None:
        event.event_at = req.event_at

    if req.status is not None:
        if req.status not in VALID_EVENT_STATUSES:
            raise HTTPException(400, "Invalid status")
        event.status = req.status
    else:
        event.status = infer_status(event.recording_url, event.transcript)

    db.commit()
    return {"eventId": event.id, "status": event.status}


@router.get("/v1/events/{event_id}")
def get_event(
    event_id: str,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.user_id == user_id)
        .first()
    )
    if not event:
        raise HTTPException(404, "Event not found")
    return event_to_dict(event)


@router.get("/v1/events/{event_id}/status")
def get_event_status(
    event_id: str,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.user_id == user_id)
        .first()
    )
    if not event:
        raise HTTPException(404, "Event not found")
    return {"status": event.status}
