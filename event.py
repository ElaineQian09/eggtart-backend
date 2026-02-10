from datetime import datetime, timedelta, timezone
from typing import Optional
import logging
import os
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import verify_token
from ai_pipeline import (
    GeminiRateLimitError,
    GeminiTransientError,
    ai_enabled,
    get_user_ai_runtime_state,
    process_user_ai_queue,
)
from database import get_db
from models import Device, Event, EggbookIdea
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
EVENT_DEBUG_ENABLED = os.getenv("EVENT_DEBUG_ENABLED", "0") == "1"
AUDIO_BATCH_TRIGGER_COUNT = int(os.getenv("AUDIO_BATCH_TRIGGER_COUNT", "5"))
AUDIO_BATCH_MAX_WAIT_HOURS = float(os.getenv("AUDIO_BATCH_MAX_WAIT_HOURS", "12"))


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


def _event_ai_debug_flags(event: Event) -> dict:
    has_audio_url = bool((event.audio_url or "").strip())
    has_screen_recording = bool((event.screen_recording_url or event.recording_url or "").strip())
    has_transcript = bool((event.transcript or "").strip())

    # Rule 1: screen recording exists -> single infer.
    rule1_eligible = has_screen_recording
    # Rule 2: no recording urls and transcript exists -> batch infer.
    rule2_eligible = (not has_screen_recording) and (not has_audio_url) and has_transcript
    eligible_any = rule1_eligible or rule2_eligible

    return {
        "hasAudioUrl": has_audio_url,
        "hasScreenRecordingUrl": has_screen_recording,
        "hasTranscript": has_transcript,
        "rule1SingleEligible": rule1_eligible,
        "rule2BatchEligible": rule2_eligible,
        "eligibleForAiExtraction": eligible_any,
    }


def _audio_batch_candidates_query(db: Session, user_id: str):
    return (
        db.query(Event)
        .filter(
            Event.user_id == user_id,
            Event.audio_url.is_not(None),
            Event.transcript.is_(None),
            Event.screen_recording_url.is_(None),
            Event.recording_url.is_(None),
            Event.status.in_([EVENT_STATUS_PENDING, EVENT_STATUS_TRANSCRIBING, EVENT_STATUS_FAILED]),
        )
        .order_by(Event.event_at.asc())
    )


def _count_pending_audio_batch_candidates(db: Session, user_id: str) -> int:
    return _audio_batch_candidates_query(db, user_id).count()


def _pending_input_candidates_query(db: Session, user_id: str):
    return (
        db.query(Event)
        .filter(
            Event.user_id == user_id,
            Event.status.in_([EVENT_STATUS_PENDING, EVENT_STATUS_TRANSCRIBING, EVENT_STATUS_FAILED]),
            (
                Event.audio_url.is_not(None)
                | Event.screen_recording_url.is_not(None)
                | Event.recording_url.is_not(None)
                | Event.transcript.is_not(None)
            ),
        )
        .order_by(Event.event_at.asc())
    )


def _count_pending_input_candidates(db: Session, user_id: str) -> int:
    return _pending_input_candidates_query(db, user_id).count()


def _run_audio_batch_stt(db: Session, user_id: str) -> int:
    events = _audio_batch_candidates_query(db, user_id).all()
    if not events:
        return 0

    processed = 0
    for candidate in events:
        candidate.status = EVENT_STATUS_TRANSCRIBING
        db.commit()
        try:
            transcript = transcribe_audio_from_url(candidate.audio_url or "")
        except Exception:
            logger.exception("Batch STT failed for event %s", candidate.id)
            candidate.status = EVENT_STATUS_FAILED
            db.commit()
            continue
        if transcript:
            candidate.transcript = transcript
            processed += 1
        db.commit()
    return processed


def _oldest_pending_audio_event_at(db: Session, user_id: str) -> Optional[datetime]:
    oldest = _audio_batch_candidates_query(db, user_id).first()
    return oldest.event_at if oldest else None


def _oldest_pending_input_event_at(db: Session, user_id: str) -> Optional[datetime]:
    oldest = _pending_input_candidates_query(db, user_id).first()
    return oldest.event_at if oldest else None


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
    # Optional explicit trigger switch from client.
    finalize: Optional[bool] = None


def _stt_fill_transcript(event: Event, db: Session) -> None:
    if event.transcript:
        return
    source_urls = _stt_source_urls(event)
    if not source_urls:
        return
    if not stt_enabled():
        return

    event.status = EVENT_STATUS_TRANSCRIBING
    db.commit()

    transcript = None
    for source_url in source_urls:
        try:
            transcript = transcribe_audio_from_url(source_url)
        except Exception:
            logger.exception("STT failed for event %s using source %s", event.id, source_url)
            continue
        if transcript:
            break

    if transcript:
        event.transcript = transcript
    db.commit()


def _stt_source_urls(event: Event) -> list[str]:
    candidates = [
        (event.audio_url or "").strip(),
        (event.screen_recording_url or "").strip(),
        (event.recording_url or "").strip(),
    ]
    deduped = []
    for url in candidates:
        if url and url not in deduped:
            deduped.append(url)
    return deduped


def _has_media_url(event: Event) -> bool:
    return bool(
        (event.audio_url or "").strip()
        or (event.screen_recording_url or event.recording_url or "").strip()
    )


def _has_any_input(event: Event) -> bool:
    return bool(
        (event.audio_url or "").strip()
        or (event.screen_recording_url or event.recording_url or "").strip()
        or (event.transcript or "").strip()
    )


def _run_pending_input_stt(db: Session, user_id: str) -> int:
    events = _pending_input_candidates_query(db, user_id).all()
    if not events:
        return 0

    processed = 0
    for candidate in events:
        if (candidate.transcript or "").strip():
            continue
        source_urls = _stt_source_urls(candidate)
        if not source_urls:
            continue

        candidate.status = EVENT_STATUS_TRANSCRIBING
        db.commit()

        transcript = None
        for source_url in source_urls:
            try:
                transcript = transcribe_audio_from_url(source_url)
            except Exception:
                logger.exception(
                    "Batch STT failed for event %s using source %s",
                    candidate.id,
                    source_url,
                )
                continue
            if transcript:
                break

        if transcript:
            candidate.transcript = transcript
            processed += 1
        else:
            candidate.status = EVENT_STATUS_FAILED
        db.commit()
    return processed


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
    # Create or refresh a placeholder idea for screen recordings.
    screen_recording_url = event.screen_recording_url or event.recording_url
    if screen_recording_url:
        idea = (
            db.query(EggbookIdea)
            .filter(EggbookIdea.user_id == user_id, EggbookIdea.source_event_id == event.id)
            .first()
        )
        if not idea:
            idea = EggbookIdea(
                id=str(uuid.uuid4()),
                user_id=user_id,
                source_event_id=event.id,
                title=None,
                content=None,
                screen_recording_url=screen_recording_url,
                recording_url=event.recording_url,
                audio_url=event.audio_url,
            )
            db.add(idea)
        else:
            idea.screen_recording_url = screen_recording_url
            idea.recording_url = event.recording_url
            idea.audio_url = event.audio_url
        db.commit()

    # Trigger gate: by default, do not run AI on transcript-only patches.
    # This avoids duplicate AI runs when frontend PATCHes twice for one voice event
    # (first transcript/duration, then media URL after upload).
    allow_transcript_only_trigger = os.getenv("AI_TRIGGER_TRANSCRIPT_ONLY", "0") == "1"
    should_trigger_processing = bool(req.finalize) or _has_media_url(event) or allow_transcript_only_trigger
    if not should_trigger_processing:
        payload = event_to_dict(event)
        payload["eventId"] = event.id
        return payload

    pending_input_count = _count_pending_input_candidates(db, user_id)
    oldest_pending_input_at = _oldest_pending_input_event_at(db, user_id)
    batch_wait_exceeded = False
    if oldest_pending_input_at is not None:
        threshold_dt = datetime.utcnow() - timedelta(hours=AUDIO_BATCH_MAX_WAIT_HOURS)
        batch_wait_exceeded = oldest_pending_input_at <= threshold_dt
    has_screen_recording = bool((event.screen_recording_url or event.recording_url or "").strip())
    should_delay_input_processing = (
        _has_any_input(event)
        and not has_screen_recording
        and pending_input_count < AUDIO_BATCH_TRIGGER_COUNT
        and not batch_wait_exceeded
    )
    if should_delay_input_processing:
        event.status = EVENT_STATUS_TRANSCRIBING
        db.commit()
        payload = event_to_dict(event)
        payload["eventId"] = event.id
        return payload

    try:
        if has_screen_recording:
            # Screen recording events should be processed immediately.
            _stt_fill_transcript(event, db)
        elif (pending_input_count >= AUDIO_BATCH_TRIGGER_COUNT or batch_wait_exceeded) and stt_enabled():
            _run_pending_input_stt(db, user_id)
        else:
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


@router.get("/v1/debug/events/{event_id}/ai-state")
def debug_event_ai_state(
    event_id: str,
    authorization: str = Header(...),
    db: Session = Depends(get_db),
):
    if not EVENT_DEBUG_ENABLED:
        raise HTTPException(404, "Not Found")

    user_id = get_user_id(authorization)
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.user_id == user_id)
        .first()
    )
    if not event:
        raise HTTPException(404, "Event not found")

    flags = _event_ai_debug_flags(event)
    runtime = get_user_ai_runtime_state(user_id)
    pending_input_count = _count_pending_input_candidates(db, user_id)
    oldest_pending_input_at = _oldest_pending_input_event_at(db, user_id)
    batch_wait_exceeded = False
    if oldest_pending_input_at is not None:
        threshold_dt = datetime.utcnow() - timedelta(hours=AUDIO_BATCH_MAX_WAIT_HOURS)
        batch_wait_exceeded = oldest_pending_input_at <= threshold_dt
    probable_reason = None
    if not runtime["aiEnabled"]:
        probable_reason = "AI disabled (missing GEMINI_API_KEY)"
    elif runtime["userProcessing"]:
        probable_reason = "User AI queue is currently processing"
    elif runtime["cooldownRemainingSec"] > 0:
        probable_reason = "User AI queue cooldown active"
    elif pending_input_count > 0 and pending_input_count < AUDIO_BATCH_TRIGGER_COUNT and not batch_wait_exceeded:
        probable_reason = "Waiting for input batch trigger threshold"
    elif not flags["eligibleForAiExtraction"]:
        probable_reason = "Event not eligible for extraction rules"
    elif event.status == EVENT_STATUS_TRANSCRIBING:
        probable_reason = "AI/STT likely pending or transient failure retry path"
    elif event.status == EVENT_STATUS_FAILED:
        probable_reason = "Last AI/STT attempt failed"
    elif event.status == EVENT_STATUS_PROCESSED:
        probable_reason = "Processed successfully"

    return {
        "eventId": event.id,
        "userId": user_id,
        "status": event.status,
        "eventAt": event.event_at.isoformat() if event.event_at else None,
        "updatedAt": event.updated_at.isoformat() if event.updated_at else None,
        "signals": flags,
        "audioBatch": {
            "pendingInputCount": pending_input_count,
            # Backward-compat alias for old debug readers.
            "pendingAudioCount": pending_input_count,
            "triggerCount": AUDIO_BATCH_TRIGGER_COUNT,
            "maxWaitHours": AUDIO_BATCH_MAX_WAIT_HOURS,
            "oldestPendingEventAt": oldest_pending_input_at.isoformat() if oldest_pending_input_at else None,
            "waitExceeded": batch_wait_exceeded,
        },
        "runtime": runtime,
        "probableReason": probable_reason,
    }


@router.get("/v1/debug/events/{event_id}/linked-idea")
def debug_event_linked_idea(
    event_id: str,
    authorization: str = Header(...),
    db: Session = Depends(get_db),
):
    if not EVENT_DEBUG_ENABLED:
        raise HTTPException(404, "Not Found")

    user_id = get_user_id(authorization)
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.user_id == user_id)
        .first()
    )
    if not event:
        raise HTTPException(404, "Event not found")

    idea = (
        db.query(EggbookIdea)
        .filter(EggbookIdea.user_id == user_id, EggbookIdea.source_event_id == event.id)
        .first()
    )
    if not idea:
        return {
            "eventId": event.id,
            "idea": None,
        }

    title = (idea.title or "").strip()
    content = (idea.content or "").strip()
    is_placeholder = not title and not content

    return {
        "eventId": event.id,
        "idea": {
            "id": idea.id,
            "isPlaceholder": is_placeholder,
            "title": idea.title,
            "content": idea.content,
            "screenRecordingUrl": idea.screen_recording_url,
            "recordingUrl": idea.recording_url,
            "audioUrl": idea.audio_url,
            "createdAt": idea.created_at.isoformat(),
            "updatedAt": idea.updated_at.isoformat(),
        },
    }
