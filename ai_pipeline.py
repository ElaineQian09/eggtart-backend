import json
import logging
import os
import re
import time
import uuid
from datetime import date as date_type, datetime, timedelta, timezone
from typing import Any, Dict, List, Set, Tuple
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import SessionLocal
from models import (
    AiUserLock,
    EggbookComment,
    EggbookCommentGeneration,
    Device,
    EggbookIdea,
    EggbookNotification,
    EggbookTodo,
    Event,
)


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-pro-preview")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
logger = logging.getLogger(__name__)
_LAST_AI_ERROR: Dict[str, Any] = {}
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


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _get_user_timezone_hint(db: Session, user_id: str) -> str:
    row = (
        db.query(Device.timezone)
        .filter(Device.user_id == user_id, Device.timezone.is_not(None), Device.timezone != "")
        .order_by(Device.created_at.desc())
        .first()
    )
    if not row:
        return "UTC"
    return str(row[0]).strip() or "UTC"


def _build_time_context(now_utc: datetime, user_timezone: str) -> str:
    now_iso = _iso_utc(now_utc) or ""
    return (
        "Time reference (critical):\n"
        f"- current_time_utc: {now_iso}\n"
        f"- user_timezone_hint: {user_timezone}\n"
        "- Treat event_at as UTC when no timezone offset is present.\n"
        "- Resolve relative time expressions (today, tomorrow, tonight, next Monday, in 2 hours) "
        "against current_time_utc + user_timezone_hint.\n"
        "- For any time-sensitive todo/alert, write an explicit absolute time in the text using "
        "YYYY-MM-DD HH:MM plus timezone (or UTC when unsure).\n"
        "- Never leave a deadline/reminder as only a relative phrase when it can be resolved.\n"
    )


def _user_tzinfo(user_timezone: str):
    tz_name = (user_timezone or "").strip() or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _to_utc_naive(dt: datetime, user_timezone: str) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_user_tzinfo(user_timezone))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_notify_at_text(value: Any, user_timezone: str) -> datetime | None:
    text = _safe_text(value)
    if not text:
        return None

    # Prefer strict ISO timestamps from model output.
    iso_candidate = text.replace("Z", "+00:00")
    try:
        return _to_utc_naive(datetime.fromisoformat(iso_candidate), user_timezone)
    except Exception:
        pass

    # Fallback: parse explicit datetime in text like "2026-02-22 20:00 UTC".
    m = re.search(
        r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)\s*(UTC|Z|[+-]\d{2}:?\d{2})?",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        return None

    date_part = m.group(1)
    time_part = m.group(2)
    tz_part = (m.group(3) or "").upper()
    dt_part = f"{date_part} {time_part}"
    fmt = "%Y-%m-%d %H:%M:%S" if len(time_part) == 8 else "%Y-%m-%d %H:%M"
    try:
        dt = datetime.strptime(dt_part, fmt)
    except Exception:
        return None

    if tz_part in ["UTC", "Z"]:
        return dt
    if tz_part:
        normalized = tz_part if ":" in tz_part else f"{tz_part[:3]}:{tz_part[3:]}"
        try:
            offset_dt = datetime.fromisoformat(f"{date_part}T{time_part}{normalized}")
            return _to_utc_naive(offset_dt, user_timezone)
        except Exception:
            return _to_utc_naive(dt, user_timezone)
    return _to_utc_naive(dt, user_timezone)


def _extract_alert_notify_at(item: Dict[str, Any], alert_text: str, user_timezone: str) -> datetime | None:
    for key in ["alert_notify_at_utc", "alert_notify_at", "alert_at_utc", "alert_at"]:
        parsed = _parse_notify_at_text(item.get(key), user_timezone)
        if parsed is not None:
            return parsed
    return _parse_notify_at_text(alert_text, user_timezone)


def _persist_items(
    db: Session,
    user_id: str,
    items: List[Dict[str, Any]],
    user_timezone: str,
    source_event: Event | None = None,
) -> Tuple[int, Set[str]]:
    created = 0
    now = datetime.utcnow()
    idea_written = False
    updated_tabs: Set[str] = set()
    for item in items:
        idea_title = _safe_text(item.get("scrolling_idea_title"))
        idea_detail = _safe_text(item.get("scrolling_idea_detail"))
        todo_item = _safe_text(item.get("todo_item"))
        alert = _safe_text(item.get("alert"))

        if (idea_title or idea_detail) and (source_event is None or not idea_written):
            if source_event is not None:
                idea = (
                    db.query(EggbookIdea)
                    .filter(
                        EggbookIdea.user_id == user_id,
                        EggbookIdea.source_event_id == source_event.id,
                    )
                    .first()
                )
                if not idea:
                    idea = EggbookIdea(
                        id=str(uuid.uuid4()),
                        user_id=user_id,
                        source_event_id=source_event.id,
                        title=None,
                        content=None,
                        screen_recording_url=_screen_recording_url(source_event) or None,
                        recording_url=source_event.recording_url,
                        audio_url=source_event.audio_url,
                    )
                    db.add(idea)
                    created += 1
                idea.title = idea_title or None
                idea.content = idea_detail or idea_title
                idea.screen_recording_url = _screen_recording_url(source_event) or None
                idea.recording_url = source_event.recording_url
                idea.audio_url = source_event.audio_url
                idea_written = True
                updated_tabs.add("ideas")
            else:
                db.add(
                    EggbookIdea(
                        id=str(uuid.uuid4()),
                        user_id=user_id,
                        title=idea_title or None,
                        content=idea_detail or idea_title,
                    )
                )
                created += 1
                updated_tabs.add("ideas")
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
            updated_tabs.add("todos")
        if alert:
            notify_at = _extract_alert_notify_at(item, alert, user_timezone) or now
            # Reuse notification table to persist alert text.
            db.add(
                EggbookNotification(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    todo_id=None,
                    title=alert,
                    notify_at=notify_at,
                )
            )
            created += 1
            updated_tabs.add("notifications")
    return created, updated_tabs


def _build_items_prompt(
    events: List[Event],
    single_mode: bool,
    now_utc: datetime,
    user_timezone: str,
) -> str:
    serialized = [
        {
            "event_id": e.id,
            "event_at": _iso_utc(e.event_at),
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
            '      "alert": "string",\n'
            '      "alert_notify_at_utc": "string"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Field meanings and rules:\n"
            "- scrolling_idea_title: short headline for a potentially valuable idea from this event.\n"
            "- scrolling_idea_detail: detailed explanation of that idea (at least 3 sentences).\n"
            "- scrolling_idea_detail must include: why it matters, supporting evidence from input, and a practical next step.\n"
            "- todo_item: one concrete, executable next action; keep imperative and specific.\n"
            "- alert: important risk/reminder/deadline to surface prominently.\n"
            "- alert_notify_at_utc: if the user mentions a specific/relative reminder time (e.g. "
            '"in 2 days at 8pm", "tomorrow morning"), resolve it using current_time_utc + user_timezone_hint '
            'and output absolute UTC ISO8601 (example: "2026-02-22T04:00:00Z"). Otherwise empty string.\n'
            "- If there is a clear reminder time in the input, alert must not be empty.\n"
            "- If a field has no meaningful content, use empty string.\n"
            "- You may output multiple items if the event contains multiple independent thoughts.\n"
            "- Preserve original language tone when possible.\n"
            f"{_build_time_context(now_utc, user_timezone)}"
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
        '      "alert": "string",\n'
        '      "alert_notify_at_utc": "string"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Field meanings and rules:\n"
        "- scrolling_idea_title: short headline for a synthesized idea across events.\n"
        "- scrolling_idea_detail: detailed synthesis (at least 3 sentences) that combines relevant evidence from the event set.\n"
        "- scrolling_idea_detail must include: key context, why this matters now, and a practical direction or next step.\n"
        "- todo_item: concrete next action derived from the strongest actionable signal.\n"
        "- alert: urgent caution, conflict, or time-sensitive reminder detected in the batch.\n"
        "- alert_notify_at_utc: if the user mentions a specific/relative reminder time, resolve it "
        'using current_time_utc + user_timezone_hint and output absolute UTC ISO8601 (example: "2026-02-22T04:00:00Z"). '
        "Otherwise empty string.\n"
        "- If there is a clear reminder time in the input, alert must not be empty.\n"
        "- If a field has no meaningful content, use empty string.\n"
        "- Prefer fewer, higher-quality items instead of repeating similar items.\n"
        "- Do not invent facts that are not grounded in the input events.\n"
        f"{_build_time_context(now_utc, user_timezone)}"
        f"Input events JSON:\n{json.dumps(serialized, ensure_ascii=True)}"
    )


def _build_comments_prompt(
    ideas: List[EggbookIdea],
    todos: List[EggbookTodo],
    alerts: List[EggbookNotification],
    now_utc: datetime,
    user_timezone: str,
) -> str:
    payload = {
        "ideas": [
            {"title": i.title, "detail": i.content, "created_at": _iso_utc(i.created_at)}
            for i in ideas
        ],
        "todos": [
            {"title": t.title, "isAccepted": bool(t.is_accepted), "updated_at": _iso_utc(t.updated_at)}
            for t in todos
        ],
        "alerts": [
            {"alert": a.title, "notify_at": _iso_utc(a.notify_at)}
            for a in alerts
        ],
    }
    return (
        "You are Eggtart, a warm, witty, and observant companion.\n"
        "\n"
        "Your task is to generate:\n"
        '1) One short "my_egg_comment"\n'
        '2) Multiple short "egg_community_comment"\n'
        "\n"
        "Use today's ideas, todos, and alerts as your ONLY source.\n"
        "Do NOT invent facts.\n"
        "\n"
        "Tone:\n"
        "- Catchy\n"
        "- Casual\n"
        "- TikTok-style\n"
        "- Slightly playful, never rude\n"
        "- Sounds like real people chatting\n"
        "\n"
        "Style rules:\n"
        "- Each comment: 1-2 short sentences\n"
        '- Natural slang is allowed (e.g., "bro", "lowkey", "ngl", "bruh", "fr")\n'
        "- Can tease lightly, but must stay supportive\n"
        "- If signals are weak, give gentle neutral encouragement\n"
        "\n"
        "Output rules:\n"
        "- Return ONLY valid JSON\n"
        "- No markdown\n"
        "- No explanations\n"
        "- No extra text\n"
        "\n"
        "JSON schema:\n"
        "{\n"
        '  "my_egg_comment": "string",\n'
        '  "egg_community_comment": ["string"]\n'
        "}\n"
        "\n"
        "Now generate comments based on today's context.\n"
        f"{_build_time_context(now_utc, user_timezone)}"
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


def _clear_daily_comments(db: Session, user_id: str, target_date: date_type) -> None:
    (
        db.query(EggbookComment)
        .filter(EggbookComment.user_id == user_id, EggbookComment.date == target_date)
        .delete(synchronize_session=False)
    )


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
    now_utc = datetime.now(timezone.utc)
    user_timezone = _get_user_timezone_hint(db, user_id)
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
        comments_payload = _call_gemini_json(
            _build_comments_prompt(
                ideas,
                todos,
                alerts,
                now_utc=now_utc,
                user_timezone=user_timezone,
            )
        )
        # Replace strategy: one latest generated comment set per user per day.
        _clear_daily_comments(db, user_id, target_date)
        my_comment = _safe_text(comments_payload.get("my_egg_comment"))
        _upsert_comment(db, user_id, my_comment, target_date, False)

        community_items = comments_payload.get("egg_community_comment") or []
        for item in community_items:
            if isinstance(item, str):
                egg_comment = _safe_text(item)
            elif isinstance(item, dict):
                # Backward compatibility with old model outputs.
                egg_comment = _safe_text(item.get("egg_comment") or item.get("content"))
            else:
                egg_comment = _safe_text(item)
            text = egg_comment
            _upsert_comment(
                db,
                user_id,
                text,
                target_date,
                True,
                egg_name="",
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


def _datetime_to_epoch_sec(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).timestamp()


def _acquire_user_slot(db: Session, user_id: str) -> tuple[bool, str | None]:
    cooldown_sec = float(os.getenv("AI_USER_COOLDOWN_SEC", "8"))
    lock_lease_sec = float(os.getenv("AI_USER_LOCK_LEASE_SEC", "600"))

    for _ in range(3):
        try:
            now = datetime.utcnow()
            query = db.query(AiUserLock).filter(AiUserLock.user_id == user_id)
            if db.bind is not None and db.bind.dialect.name != "sqlite":
                query = query.with_for_update()
            lock_row = query.first()
            if not lock_row:
                lock_row = AiUserLock(user_id=user_id)
                db.add(lock_row)
                db.flush()

            if lock_row.locked_until is not None and lock_row.locked_until > now:
                return False, None

            if lock_row.last_run_at is not None:
                elapsed_sec = (now - lock_row.last_run_at).total_seconds()
                if elapsed_sec < cooldown_sec:
                    return False, None

            owner_token = str(uuid.uuid4())
            lock_row.owner_token = owner_token
            lock_row.locked_until = now + timedelta(seconds=lock_lease_sec)
            lock_row.last_run_at = now
            db.commit()
            return True, owner_token
        except IntegrityError:
            db.rollback()

    return False, None


def _release_user_slot(db: Session, user_id: str, owner_token: str | None) -> None:
    if not owner_token:
        return
    try:
        row = db.query(AiUserLock).filter(AiUserLock.user_id == user_id).first()
        if not row:
            return
        if row.owner_token != owner_token:
            return
        row.owner_token = None
        row.locked_until = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to release AI user lock, user_id=%s", user_id)


def _record_ai_error(user_id: str, event_id: str | None, exc: Exception) -> None:
    _LAST_AI_ERROR.clear()
    _LAST_AI_ERROR.update(
        {
            "atEpochSec": time.time(),
            "userId": user_id,
            "eventId": event_id,
            "type": type(exc).__name__,
            "message": str(exc)[:500],
        }
    )


def _get_user_ai_runtime_state_in_db(db: Session, user_id: str) -> Dict[str, Any]:
    cooldown_sec = float(os.getenv("AI_USER_COOLDOWN_SEC", "8"))
    now = datetime.utcnow()
    row = db.query(AiUserLock).filter(AiUserLock.user_id == user_id).first()
    processing = bool(row and row.locked_until and row.locked_until > now)
    last_run_at_epoch = _datetime_to_epoch_sec(row.last_run_at if row else None)
    elapsed = 0.0
    if last_run_at_epoch is not None:
        elapsed = max(time.time() - last_run_at_epoch, 0.0)
    cooldown_remaining_sec = max(cooldown_sec - elapsed, 0.0)
    return {
        "aiEnabled": ai_enabled(),
        "userProcessing": processing,
        "cooldownSec": cooldown_sec,
        "lastRunAtEpochSec": last_run_at_epoch,
        "cooldownRemainingSec": round(cooldown_remaining_sec, 3),
        "canRunNow": ai_enabled() and (not processing) and cooldown_remaining_sec <= 0,
    }


def get_user_ai_runtime_state(user_id: str) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        return _get_user_ai_runtime_state_in_db(db, user_id)
    finally:
        db.close()


def get_ai_runtime_snapshot(user_id: str | None = None) -> Dict[str, Any]:
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        active_query = db.query(AiUserLock).filter(
            AiUserLock.locked_until.is_not(None),
            AiUserLock.locked_until > now,
        )
        processing_users = [row.user_id for row in active_query.limit(20).all()]
        snapshot = {
            "aiEnabled": ai_enabled(),
            "activeProcessingUserCount": active_query.count(),
            "activeProcessingUsers": processing_users,
            "trackedUserCount": db.query(AiUserLock).count(),
            "lastAiError": dict(_LAST_AI_ERROR) if _LAST_AI_ERROR else None,
        }
        if user_id:
            snapshot["userRuntime"] = _get_user_ai_runtime_state_in_db(db, user_id)
        return snapshot
    finally:
        db.close()


def process_user_ai_queue(db: Session, user_id: str) -> Dict[str, Any]:
    if not ai_enabled():
        return {
            "processedEventCount": 0,
            "updatedTabs": [],
            "hasUpdates": False,
        }

    acquired, owner_token = _acquire_user_slot(db, user_id)
    if not acquired:
        logger.info("Skip AI run: user slot busy/cooldown, user_id=%s", user_id)
        return {
            "processedEventCount": 0,
            "updatedTabs": [],
            "hasUpdates": False,
        }

    try:
        now_utc = datetime.now(timezone.utc)
        user_timezone = _get_user_timezone_hint(db, user_id)
        max_events_per_run = int(os.getenv("AI_QUEUE_MAX_EVENTS_PER_RUN", "0"))
        remaining_events = max_events_per_run if max_events_per_run > 0 else None
        updated_tabs: Set[str] = set()

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
            return {
                "processedEventCount": 0,
                "updatedTabs": [],
                "hasUpdates": False,
            }

        events_to_mark_processed: List[Event] = []

        # Rule 1: screen recording exists -> infer this event independently.
        if _screen_recording_url(trigger_event) and trigger_event.status != "processed":
            payload = _call_gemini_json(
                _build_items_prompt(
                    [trigger_event],
                    single_mode=True,
                    now_utc=now_utc,
                    user_timezone=user_timezone,
                )
            )
            items = payload.get("items") or []
            _, single_tabs = _persist_items(
                db,
                user_id,
                items,
                user_timezone=user_timezone,
                source_event=trigger_event,
            )
            updated_tabs.update(single_tabs)
            events_to_mark_processed.append(trigger_event)
            if remaining_events is not None:
                remaining_events = max(remaining_events - 1, 0)

        # Rule 2: no screen recording and transcript exists -> batch infer.
        batch_limit = 20
        if remaining_events is not None:
            batch_limit = max(min(20, remaining_events), 0)
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
            .limit(batch_limit)
            .all()
        )
        if batch_events:
            payload = _call_gemini_json(
                _build_items_prompt(
                    batch_events,
                    single_mode=False,
                    now_utc=now_utc,
                    user_timezone=user_timezone,
                )
            )
            items = payload.get("items") or []
            _, batch_tabs = _persist_items(
                db,
                user_id,
                items,
                user_timezone=user_timezone,
            )
            updated_tabs.update(batch_tabs)
            events_to_mark_processed.extend(batch_events)

        for event in events_to_mark_processed:
            event.status = "processed"
        db.commit()

        comments_state = trigger_daily_comments_generation(db, user_id, date_type.today(), manual=False)
        if comments_state.get("status") == COMMENT_STATUS_READY:
            updated_tabs.add("comments")
        return {
            "processedEventCount": len(events_to_mark_processed),
            "updatedTabs": sorted(updated_tabs),
            "hasUpdates": bool(updated_tabs),
        }
    except Exception as exc:
        trigger_event_id = locals().get("trigger_event").id if locals().get("trigger_event") is not None else None
        _record_ai_error(user_id, trigger_event_id, exc)
        raise
    finally:
        _release_user_slot(db, user_id, owner_token)


def process_events_ai(db: Session, user_id: str, trigger_event_id: str) -> None:
    # Backward-compatible wrapper.
    _ = trigger_event_id
    process_user_ai_queue(db, user_id)
