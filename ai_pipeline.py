import json
import os
import uuid
from datetime import date as date_type, datetime
from typing import Any, Dict, List

import httpx
from sqlalchemy.orm import Session

from models import EggbookComment, EggbookIdea, EggbookNotification, EggbookTodo, Event


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


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


def _call_gemini_json(prompt: str) -> Dict[str, Any]:
    url = f"{GEMINI_BASE_URL}/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    with httpx.Client(timeout=40.0) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        text = _extract_json_text(resp.json())
    return json.loads(text)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


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


def process_events_ai(db: Session, user_id: str, trigger_event_id: str) -> None:
    if not ai_enabled():
        return

    trigger_event = (
        db.query(Event)
        .filter(Event.id == trigger_event_id, Event.user_id == user_id)
        .first()
    )
    if not trigger_event:
        return

    events_to_mark_processed: List[Event] = []

    # Rule 1: recording_url is not null -> infer this event independently.
    if trigger_event.recording_url and trigger_event.status != "processed":
        payload = _call_gemini_json(_build_items_prompt([trigger_event], single_mode=True))
        items = payload.get("items") or []
        _persist_items(db, user_id, items)
        events_to_mark_processed.append(trigger_event)

    # Rule 2: recording_url is null and transcript exists and not yet processed -> batch infer.
    batch_events = (
        db.query(Event)
        .filter(
            Event.user_id == user_id,
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

    # Daily comments from generated idea/todo/alert fields.
    today = date_type.today()
    ideas = (
        db.query(EggbookIdea)
        .filter(
            EggbookIdea.user_id == user_id,
            EggbookIdea.created_at >= datetime.combine(today, datetime.min.time()),
        )
        .all()
    )
    todos = (
        db.query(EggbookTodo)
        .filter(
            EggbookTodo.user_id == user_id,
            EggbookTodo.created_at >= datetime.combine(today, datetime.min.time()),
        )
        .all()
    )
    alerts = (
        db.query(EggbookNotification)
        .filter(
            EggbookNotification.user_id == user_id,
            EggbookNotification.created_at >= datetime.combine(today, datetime.min.time()),
        )
        .all()
    )

    if not ideas and not todos and not alerts:
        return

    comments_payload = _call_gemini_json(_build_comments_prompt(ideas, todos, alerts))
    my_comment = _safe_text(comments_payload.get("my_egg_comment"))
    _upsert_comment(db, user_id, my_comment, today, False)

    community_items = comments_payload.get("egg_community_comment") or []
    for item in community_items:
        egg_name = _safe_text(item.get("egg_name"))
        egg_comment = _safe_text(item.get("egg_comment"))
        text = f"{egg_name}: {egg_comment}" if egg_name else egg_comment
        _upsert_comment(
            db,
            user_id,
            text,
            today,
            True,
            egg_name=egg_name,
            egg_comment=egg_comment,
        )

    db.commit()
