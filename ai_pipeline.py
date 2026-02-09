import json
import logging
import os
import time
import uuid
from datetime import date as date_type, datetime, timedelta
from threading import Lock
from typing import Any, Dict, List

import httpx
from sqlalchemy.orm import Session

from models import (
    EggbookComment,
    EggbookCommentGeneration,
    EggbookIdea,
    EggbookNotification,
    EggbookTodo,
    Event,
)


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-pro-preview")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
logger = logging.getLogger(__name__)
_USER_GUARD = Lock()
_USER_PROCESSING: set[str] = set()
_USER_LAST_RUN_AT: Dict[str, float] = {}
COMMENT_STATUS_IDLE = "idle"
COMMENT_STATUS_GENERATING = "generating"
COMMENT_STATUS_READY = "ready"
COMMENT_STATUS_FAILED = "failed"


class GeminiRateLimitError(Exception):
    pass


class GeminiTransientError(Exception):
    pass


def ai_enabled() -> bool:
    return bool(GEMINI_API_KEY)


def _extract_json_text(response_json: Dict[str, Any]) -> str:
    candidates = response_json.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini returned no candidates")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    if not parts:
        raise ValueError("Gemini returned empty content")
    text = parts[0].get("text", "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return text


def _validate_gemini3_model(model: str) -> str:
    normalized = (model or "").strip()
    if not normalized:
        raise ValueError("GEMINI_MODEL is empty")
    if not normalized.startswith("gemini-3"):
        raise ValueError(f"Gemini 3 only mode enabled. Invalid model: {normalized}")
    return normalized


def _call_gemini_json(prompt: str) -> Dict[str, Any]:
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    headers = {"x-goog-api-key": GEMINI_API_KEY}
    model = _validate_gemini3_model(GEMINI_MODEL)
    url = f"{GEMINI_BASE_URL}/{model}:generateContent"
    transient_statuses = {408, 429, 500, 502, 503, 504}

    request_timeout = float(os.getenv("GEMINI_REQUEST_TIMEOUT_SEC", "60"))
    with httpx.Client(timeout=request_timeout) as client:
        max_attempts = int(os.getenv("GEMINI_RETRY_MAX_ATTEMPTS", "4"))
        base_delay = float(os.getenv("GEMINI_RETRY_BASE_DELAY_SEC", "1.0"))

        for attempt in range(1, max_attempts + 1):
            try:
                resp = client.post(url, json=payload, headers=headers)
            except httpx.ReadTimeout:
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    "Gemini read timeout, model=%s, attempt=%s/%s, sleeping %.2fs",
                    model,
                    attempt,
                    max_attempts,
                    delay,
                )
                if attempt == max_attempts:
                    raise GeminiTransientError(
                        f"Gemini read timeout after {max_attempts} attempts, model={model}"
                    )
                time.sleep(delay)
                continue

            if resp.status_code not in transient_statuses:
                resp.raise_for_status()
                logger.info("Gemini request succeeded with model=%s", model)
                text = _extract_json_text(resp.json())
                return json.loads(text)

            retry_after = resp.headers.get("retry-after")
            if retry_after is not None:
                try:
                    delay = max(float(retry_after), 0.5)
                except ValueError:
                    delay = base_delay * (2 ** (attempt - 1))
            else:
                delay = base_delay * (2 ** (attempt - 1))

            logger.warning(
                "Gemini transient status=%s, model=%s, attempt=%s/%s, sleeping %.2fs",
                resp.status_code,
                model,
                attempt,
                max_attempts,
                delay,
            )

            if attempt == max_attempts:
                raise GeminiTransientError(
                    f"Gemini transient failure status={resp.status_code} after {max_attempts} attempts, model={model}"
                )
            time.sleep(delay)

    raise GeminiTransientError(f"Gemini transient failure, model={model}")


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _screen_recording_url(event: Event) -> str:
    return (event.screen_recording_url or event.recording_url or "").strip()


def _persist_items(db: Session, user_id: str, items: List[Dict[str, Any]]) -> int:
    created = 0
    now = datetime.utcnow()
    for item in items:
        idea_title = _safe_text(item.get("scrolling_idea_title"))
        idea_detail = _safe_text(item.get("scrolling_idea_detail"))
        todo_item = _safe_text(item.get("todo_item"))
        alert = _safe_text(item.get("alert"))

        if idea_title or idea_detail:
            db.add(
                EggbookIdea(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    title=idea_title or None,
                    content=idea_detail or idea_title,
                )
            )
            created += 1
        if todo_item:
            db.add(
                EggbookTodo(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    title=todo_item,
                    is_accepted=False,
                    is_pinned=False,
                )
            )
            created += 1
        if alert:
            # Reuse notification table to persist alert text.
            db.add(
                EggbookNotification(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    todo_id=None,
                    title=alert,
                    notify_at=now,
                )
            )
            created += 1
    return created


def _build_items_prompt(events: List[Event], single_mode: bool) -> str:
    serialized = [
        {
            "event_id": e.id,
            "event_at": e.event_at.isoformat() if e.event_at else None,
            "audio_url": e.audio_url,
            "screen_recording_url": _screen_recording_url(e),
            "recording_url": e.recording_url,
            "transcript": e.transcript,
            "duration_sec": e.duration_sec,
        }
        for e in events
    ]
    if single_mode:
        return (
            "You are an assistant that extracts actionable productivity signals from ONE user event.\n"
            "Task:\n"
            "1) Read the event content.\n"
            "2) Decide what should become idea/todo/alert outputs.\n"
            "3) Return strict JSON only, no markdown.\n"
            "Output JSON schema:\n"
            "{\n"
            '  "items": [\n'
            "    {\n"
            '      "scrolling_idea_title": "string",\n'
            '      "scrolling_idea_detail": "string",\n'
            '      "todo_item": "string",\n'
            '      "alert": "string"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Field meanings and rules:\n"
            "- scrolling_idea_title: short headline for a potentially valuable idea from this event.\n"
            "- scrolling_idea_detail: concise explanation of that idea; include context and intent.\n"
            "- todo_item: one concrete, executable next action; keep imperative and specific.\n"
            "- alert: important risk/reminder/deadline to surface prominently.\n"
            "- If a field has no meaningful content, use empty string.\n"
            "- You may output multiple items if the event contains multiple independent thoughts.\n"
            "- Preserve original language tone when possible.\n"
            f"Input event JSON:\n{json.dumps(serialized, ensure_ascii=True)}"
        )

    return (
        "You are an assistant that extracts actionable productivity signals from MULTIPLE user events.\n"
        "Task:\n"
        "1) Read all events as one context window.\n"
        "2) Merge duplicates and cluster related points.\n"
        "3) Return strict JSON only, no markdown.\n"
        "Output JSON schema:\n"
        "{\n"
        '  "items": [\n'
        "    {\n"
        '      "scrolling_idea_title": "string",\n'
        '      "scrolling_idea_detail": "string",\n'
        '      "todo_item": "string",\n'
        '      "alert": "string"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Field meanings and rules:\n"
        "- scrolling_idea_title: short headline for a synthesized idea across events.\n"
        "- scrolling_idea_detail: compact detail that combines relevant evidence from the event set.\n"
        "- todo_item: concrete next action derived from the strongest actionable signal.\n"
        "- alert: urgent caution, conflict, or time-sensitive reminder detected in the batch.\n"
        "- If a field has no meaningful content, use empty string.\n"
        "- Prefer fewer, higher-quality items instead of repeating similar items.\n"
        "- Do not invent facts that are not grounded in the input events.\n"
        f"Input events JSON:\n{json.dumps(serialized, ensure_ascii=True)}"
    )


def _build_comments_prompt(
    ideas: List[EggbookIdea],
    todos: List[EggbookTodo],
    alerts: List[EggbookNotification],
) -> str:
    payload = {
        "ideas": [
            {"title": i.title, "detail": i.content, "created_at": i.created_at.isoformat()}
            for i in ideas
        ],
        "todos": [
            {"title": t.title, "isAccepted": bool(t.is_accepted), "updated_at": t.updated_at.isoformat()}
            for t in todos
        ],
        "alerts": [
            {"alert": a.title, "notify_at": a.notify_at.isoformat()}
            for a in alerts
        ],
    }
    return (
        "You summarize a user's day for two channels based on generated ideas/todos/alerts.\n"
        "Task:\n"
        "1) Write one personal reflection comment.\n"
        "2) Write community-style comments with egg personas.\n"
        "3) Return strict JSON only, no markdown.\n"
        "Schema:\n"
        "{\n"
        '  "my_egg_comment": "string",\n'
        '  "egg_community_comment": [\n'
        "    {\n"
        '      "egg_name": "string",\n'
        '      "egg_comment": "string"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Field meanings and rules:\n"
        "- my_egg_comment: one direct summary for the user, supportive and specific, based on today's signals.\n"
        "- egg_community_comment: list of community voices.\n"
        "- egg_name: name of the persona speaking (e.g., Focus Egg, Health Egg).\n"
        "- egg_comment: what that persona says; must be relevant, concise, and actionable.\n"
        "- Keep each comment short (1-2 sentences).\n"
        "- Do not include harmful, medical, legal, or financial claims.\n"
        "- If there is little signal, still provide gentle, neutral comments without fabricating details.\n"
        f"Input JSON:\n{json.dumps(payload, ensure_ascii=True)}"
    )


def _upsert_comment(
    db: Session,
    user_id: str,
    content: str,
    comment_date: date_type,
    is_community: bool,
    egg_name: str = "",
    egg_comment: str = "",
) -> None:
    text = content.strip()
    if not text:
        return
    name_text = egg_name.strip()
    comment_text = egg_comment.strip()
    exists = (
        db.query(EggbookComment)
        .filter(
            EggbookComment.user_id == user_id,
            EggbookComment.date == comment_date,
            EggbookComment.is_community == is_community,
            EggbookComment.content == text,
            EggbookComment.egg_name == (name_text if is_community else None),
            EggbookComment.egg_comment == (comment_text if is_community else None),
        )
        .first()
    )
    if exists:
        return
    db.add(
        EggbookComment(
            id=str(uuid.uuid4()),
            user_id=user_id,
            content=text,
            egg_name=name_text if is_community else None,
            egg_comment=comment_text if is_community else None,
            date=comment_date,
            is_community=is_community,
        )
    )


def _day_bounds(target_date: date_type) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(target_date, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)
    return start_dt, end_dt


def _cleanup_old_comment_data(db: Session, user_id: str, keep_days: int = 7) -> None:
    cutoff_date = date_type.today() - timedelta(days=keep_days - 1)
    (
        db.query(EggbookComment)
        .filter(EggbookComment.user_id == user_id, EggbookComment.date < cutoff_date)
        .delete(synchronize_session=False)
    )
    (
        db.query(EggbookCommentGeneration)
        .filter(EggbookCommentGeneration.user_id == user_id, EggbookCommentGeneration.date < cutoff_date)
        .delete(synchronize_session=False)
    )
    db.commit()


def _get_or_create_comment_state(db: Session, user_id: str, target_date: date_type) -> EggbookCommentGeneration:
    state = (
        db.query(EggbookCommentGeneration)
        .filter(EggbookCommentGeneration.user_id == user_id, EggbookCommentGeneration.date == target_date)
        .first()
    )
    if state:
        return state
    state = EggbookCommentGeneration(
        id=str(uuid.uuid4()),
        user_id=user_id,
        date=target_date,
        status=COMMENT_STATUS_IDLE,
        has_input=False,
        active_duration_sec=0,
    )
    db.add(state)
    db.commit()
    db.refresh(state)
    return state


def _get_daily_input_stats(db: Session, user_id: str, target_date: date_type) -> tuple[bool, float]:
    start_dt, end_dt = _day_bounds(target_date)
    events = (
        db.query(Event)
        .filter(Event.user_id == user_id, Event.event_at >= start_dt, Event.event_at < end_dt)
        .all()
    )
    has_input = any(
        bool((e.audio_url or "").strip() or (e.screen_recording_url or e.recording_url or "").strip())
        for e in events
    )
    active_duration_sec = float(sum(float(e.duration_sec or 0) for e in events))
    return has_input, active_duration_sec


def get_comment_generation_state(db: Session, user_id: str, target_date: date_type) -> Dict[str, Any]:
    _cleanup_old_comment_data(db, user_id)
    state = _get_or_create_comment_state(db, user_id, target_date)
    has_input, active_duration_sec = _get_daily_input_stats(db, user_id, target_date)
    state.has_input = has_input
    state.active_duration_sec = active_duration_sec
    if state.status in [COMMENT_STATUS_IDLE, COMMENT_STATUS_READY] and not has_input:
        state.status = COMMENT_STATUS_IDLE
    db.commit()
    db.refresh(state)
    return {
        "date": target_date.isoformat(),
        "status": state.status,
        "hasInput": bool(state.has_input),
        "activeDurationSec": int(state.active_duration_sec or 0),
        "canManualTrigger": bool(state.has_input),
    }


def _send_comment_ready_notification(db: Session, user_id: str, target_date: date_type) -> None:
    title = f"Comments ready for {target_date.isoformat()}"
    exists = (
        db.query(EggbookNotification)
        .filter(
            EggbookNotification.user_id == user_id,
            EggbookNotification.title == title,
        )
        .first()
    )
    if exists:
        return
    now = datetime.utcnow()
    db.add(
        EggbookNotification(
            id=str(uuid.uuid4()),
            user_id=user_id,
            todo_id=None,
            title=title,
            notify_at=now,
        )
    )
    db.commit()


def trigger_daily_comments_generation(
    db: Session,
    user_id: str,
    target_date: date_type,
    manual: bool = False,
) -> Dict[str, Any]:
    _cleanup_old_comment_data(db, user_id)
    state = _get_or_create_comment_state(db, user_id, target_date)
    has_input, active_duration_sec = _get_daily_input_stats(db, user_id, target_date)

    state.has_input = has_input
    state.active_duration_sec = active_duration_sec
    state.trigger_mode = "manual" if manual else "auto"

    if not has_input:
        state.status = COMMENT_STATUS_IDLE
        state.error_message = "No voice/screen input for the day"
        db.commit()
        return get_comment_generation_state(db, user_id, target_date)

    if (not manual) and active_duration_sec < 3600:
        state.status = COMMENT_STATUS_IDLE
        state.error_message = "Active duration below auto threshold (3600s)"
        db.commit()
        return get_comment_generation_state(db, user_id, target_date)

    state.status = COMMENT_STATUS_GENERATING
    state.error_message = None
    db.commit()

    start_dt, end_dt = _day_bounds(target_date)
    ideas = (
        db.query(EggbookIdea)
        .filter(EggbookIdea.user_id == user_id, EggbookIdea.created_at >= start_dt, EggbookIdea.created_at < end_dt)
        .all()
    )
    todos = (
        db.query(EggbookTodo)
        .filter(EggbookTodo.user_id == user_id, EggbookTodo.created_at >= start_dt, EggbookTodo.created_at < end_dt)
        .all()
    )
    alerts = (
        db.query(EggbookNotification)
        .filter(
            EggbookNotification.user_id == user_id,
            EggbookNotification.created_at >= start_dt,
            EggbookNotification.created_at < end_dt,
        )
        .all()
    )
    if not ideas and not todos and not alerts:
        state.status = COMMENT_STATUS_IDLE
        state.error_message = "No idea/todo/alert signals for the day"
        db.commit()
        return get_comment_generation_state(db, user_id, target_date)

    try:
        comments_payload = _call_gemini_json(_build_comments_prompt(ideas, todos, alerts))
        my_comment = _safe_text(comments_payload.get("my_egg_comment"))
        _upsert_comment(db, user_id, my_comment, target_date, False)

        community_items = comments_payload.get("egg_community_comment") or []
        for item in community_items:
            egg_name = _safe_text(item.get("egg_name"))
            egg_comment = _safe_text(item.get("egg_comment"))
            text = f"{egg_name}: {egg_comment}" if egg_name else egg_comment
            _upsert_comment(
                db,
                user_id,
                text,
                target_date,
                True,
                egg_name=egg_name,
                egg_comment=egg_comment,
            )
        db.commit()
        state.status = COMMENT_STATUS_READY
        state.error_message = None
        db.commit()
        _send_comment_ready_notification(db, user_id, target_date)
    except Exception as exc:
        state.status = COMMENT_STATUS_FAILED
        state.error_message = str(exc)[:500]
        db.commit()
        raise

    return get_comment_generation_state(db, user_id, target_date)


def _acquire_user_slot(user_id: str) -> bool:
    cooldown_sec = float(os.getenv("AI_USER_COOLDOWN_SEC", "8"))
    now = time.time()
    with _USER_GUARD:
        if user_id in _USER_PROCESSING:
            return False
        last_run = _USER_LAST_RUN_AT.get(user_id, 0.0)
        if now - last_run < cooldown_sec:
            return False
        _USER_PROCESSING.add(user_id)
        _USER_LAST_RUN_AT[user_id] = now
        return True


def _release_user_slot(user_id: str) -> None:
    with _USER_GUARD:
        _USER_PROCESSING.discard(user_id)


def process_user_ai_queue(db: Session, user_id: str) -> None:
    if not ai_enabled():
        return

    if not _acquire_user_slot(user_id):
        logger.info("Skip AI run: user slot busy/cooldown, user_id=%s", user_id)
        return

    try:
        trigger_event = (
            db.query(Event)
            .filter(
                Event.user_id == user_id,
                Event.status.in_(["pending", "transcribing", "failed"]),
            )
            .order_by(Event.event_at.asc())
            .first()
        )
        if not trigger_event:
            return

        events_to_mark_processed: List[Event] = []

        # Rule 1: screen recording exists -> infer this event independently.
        if _screen_recording_url(trigger_event) and trigger_event.status != "processed":
            payload = _call_gemini_json(_build_items_prompt([trigger_event], single_mode=True))
            items = payload.get("items") or []
            _persist_items(db, user_id, items)
            events_to_mark_processed.append(trigger_event)

        # Rule 2: no screen recording and transcript exists -> batch infer.
        batch_events = (
            db.query(Event)
            .filter(
                Event.user_id == user_id,
                Event.screen_recording_url.is_(None),
                Event.recording_url.is_(None),
                Event.transcript.is_not(None),
                Event.status.in_(["pending", "transcribing", "failed"]),
            )
            .order_by(Event.event_at.asc())
            .limit(20)
            .all()
        )
        if batch_events:
            payload = _call_gemini_json(_build_items_prompt(batch_events, single_mode=False))
            items = payload.get("items") or []
            _persist_items(db, user_id, items)
            events_to_mark_processed.extend(batch_events)

        for event in events_to_mark_processed:
            event.status = "processed"
        db.commit()

        trigger_daily_comments_generation(db, user_id, date_type.today(), manual=False)
    finally:
        _release_user_slot(user_id)


def process_events_ai(db: Session, user_id: str, trigger_event_id: str) -> None:
    # Backward-compatible wrapper.
    _ = trigger_event_id
    process_user_ai_queue(db, user_id)
