from datetime import datetime, timezone
from typing import Optional
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import verify_token
from ai_pipeline import GeminiRateLimitError, GeminiTransientError, ai_enabled, process_user_ai_queue
from database import get_db
from models import Device, Event
from stt_client import stt_enabled, transcribe_audio_from_url


router = APIRouter()
logger = logging.getLogger(__name__)

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
    screen_recording_url = event.screen_recording_url or event.recording_url
    return {
        "eventId": event.id,
        "deviceId": event.device_id,
        # Backward-compatible field for existing clients.
        "recordingUrl": screen_recording_url,
        "audioUrl": event.audio_url,
        "screenRecordingUrl": screen_recording_url,
        "transcript": event.transcript,
        "durationSec": int(event.duration_sec or 0),
        "eventAt": event.event_at.isoformat(),
        "status": event.status,
        "createdAt": event.created_at.isoformat(),
        "updatedAt": event.updated_at.isoformat(),
    }


def infer_status(audio_url: Optional[str], screen_recording_url: Optional[str], transcript: Optional[str]) -> str:
    # "processed" should only be set after AI pipeline succeeds.
    if transcript or audio_url or screen_recording_url:
        return EVENT_STATUS_TRANSCRIBING
    return EVENT_STATUS_PENDING


class EventCreateRequest(BaseModel):
    device_id: str
    audio_url: Optional[str] = None
    screen_recording_url: Optional[str] = None
    # Deprecated, keep for backward compatibility.
    recording_url: Optional[str] = None
    transcript: Optional[str] = None
    duration_sec: Optional[int] = 0
    event_at: Optional[datetime] = None


class EventUpdateRequest(BaseModel):
    audio_url: Optional[str] = None
    screen_recording_url: Optional[str] = None
    # Deprecated, keep for backward compatibility.
    recording_url: Optional[str] = None
    transcript: Optional[str] = None
    duration_sec: Optional[int] = None
    event_at: Optional[datetime] = None
    status: Optional[str] = None


def _stt_fill_transcript(event: Event, db: Session) -> None:
    if event.transcript:
        return
    audio_url = event.audio_url
    if not audio_url:
        return
    if not stt_enabled():
        return

    event.status = EVENT_STATUS_TRANSCRIBING
    db.commit()

    transcript = transcribe_audio_from_url(audio_url)
    if transcript:
        event.transcript = transcript
    db.commit()


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
    screen_recording_url = req.screen_recording_url or req.recording_url
    # POST only stores event; AI/STT is triggered on PATCH finalization.
    status = EVENT_STATUS_PENDING

    event = Event(
        id=str(uuid.uuid4()),
        user_id=user_id,
        device_id=req.device_id,
        recording_url=screen_recording_url,
        audio_url=req.audio_url,
        screen_recording_url=screen_recording_url,
        transcript=req.transcript,
        duration_sec=float(req.duration_sec or 0),
        event_at=event_at,
        status=status,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    payload = event_to_dict(event)
    payload["eventId"] = event.id
    return payload


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

    if req.audio_url is not None:
        event.audio_url = req.audio_url
    if req.screen_recording_url is not None:
        event.screen_recording_url = req.screen_recording_url
        event.recording_url = req.screen_recording_url
    if req.recording_url is not None:
        event.recording_url = req.recording_url
        event.screen_recording_url = req.recording_url
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
        screen_recording_url = event.screen_recording_url or event.recording_url
        event.status = infer_status(event.audio_url, screen_recording_url, event.transcript)

    db.commit()

    try:
        _stt_fill_transcript(event, db)
    except Exception:
        logger.exception("STT failed for event %s", event.id)
        event.status = EVENT_STATUS_FAILED
        db.commit()
        payload = event_to_dict(event)
        payload["eventId"] = event.id
        return payload

    if not ai_enabled():
        event.status = EVENT_STATUS_PENDING
        db.commit()
        payload = event_to_dict(event)
        payload["eventId"] = event.id
        return payload

    try:
        process_user_ai_queue(db, user_id)
    except (GeminiRateLimitError, GeminiTransientError):
        logger.warning("AI transient error for event %s, keeping status for retry", event.id)
        event.status = EVENT_STATUS_TRANSCRIBING
        db.commit()
    except Exception:
        logger.exception("AI processing failed for event %s", event.id)
        event.status = EVENT_STATUS_FAILED
        db.commit()
    else:
        db.refresh(event)

    payload = event_to_dict(event)
    payload["eventId"] = event.id
    return payload


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
