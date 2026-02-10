# eggbook.py

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date as date_type, timedelta
import uuid

from database import get_db
from auth import verify_token
from ai_pipeline import get_comment_generation_state, trigger_daily_comments_generation
from models import (
    EggbookIdea,
    EggbookTodo,
    EggbookNotification,
    EggbookComment
)


router = APIRouter()


def get_user_id(authorization: str) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid token")
    token = authorization.replace("Bearer ", "")
    return verify_token(token)


class IdeaCreateRequest(BaseModel):

    title: Optional[str] = None
    content: str


class TodoCreateRequest(BaseModel):

    title: str


class TodoUpdateRequest(BaseModel):

    title: Optional[str] = None
    isAccepted: Optional[bool] = None


class ScheduleRequest(BaseModel):

    notify_at: datetime


class NotificationCreateRequest(BaseModel):

    title: str
    notify_at: datetime
    todo_id: Optional[str] = None


class NotificationUpdateRequest(BaseModel):

    notify_at: datetime


class CommentCreateRequest(BaseModel):

    content: Optional[str] = None
    egg_name: Optional[str] = None
    egg_comment: Optional[str] = None
    date: Optional[date_type] = None
    isCommunity: Optional[bool] = False


class CommentGenerateRequest(BaseModel):

    date: Optional[date_type] = None


def idea_to_dict(idea: EggbookIdea):
    return {
        "id": idea.id,
        "sourceEventId": idea.source_event_id,
        "title": idea.title,
        "content": idea.content,
        "screenRecordingUrl": idea.screen_recording_url,
        "recordingUrl": idea.recording_url,
        "audioUrl": idea.audio_url,
        "createdAt": idea.created_at.isoformat(),
        "updatedAt": idea.updated_at.isoformat()
    }


def todo_to_dict(todo: EggbookTodo):
    return {
        "id": todo.id,
        "title": todo.title,
        "isAccepted": bool(todo.is_accepted),
        "isPinned": bool(todo.is_pinned),
        "createdAt": todo.created_at.isoformat(),
        "updatedAt": todo.updated_at.isoformat()
    }


def notification_to_dict(notification: EggbookNotification):
    return {
        "id": notification.id,
        "title": notification.title,
        "todoId": notification.todo_id,
        "notifyAt": notification.notify_at.isoformat(),
        "createdAt": notification.created_at.isoformat(),
        "updatedAt": notification.updated_at.isoformat()
    }


def comment_to_dict(comment: EggbookComment):
    content = comment.content
    if bool(comment.is_community) and comment.egg_comment:
        content = comment.egg_comment
    return {
        "id": comment.id,
        "content": content,
        "eggName": comment.egg_name,
        "eggComment": comment.egg_comment,
        "date": comment.date.isoformat(),
        "isCommunity": bool(comment.is_community),
        "createdAt": comment.created_at.isoformat()
    }


@router.get("/v1/eggbook/sync-status")
def get_sync_status(
    authorization: str = Header(...),
    db: Session = Depends(get_db),
):
    user_id = get_user_id(authorization)
    pending_ideas = (
        db.query(EggbookIdea)
        .filter(
            EggbookIdea.user_id == user_id,
            or_(
                EggbookIdea.title.is_(None),
                EggbookIdea.title == "",
                EggbookIdea.content.is_(None),
                EggbookIdea.content == "",
            ),
        )
        .count()
    )
    total_ideas = (
        db.query(EggbookIdea)
        .filter(EggbookIdea.user_id == user_id)
        .count()
    )
    processing = pending_ideas > 0
    has_updates = (not processing) and total_ideas > 0
    return {
        "status": "ok",
        "lastSyncAt": None,
        "processing": processing,
        "hasUpdates": has_updates,
    }


@router.get("/v1/eggbook/ideas")
def list_ideas(
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    ideas = (
        db.query(EggbookIdea)
        .filter(EggbookIdea.user_id == user_id)
        .order_by(EggbookIdea.created_at.desc())
        .all()
    )
    return {"items": [idea_to_dict(idea) for idea in ideas]}


@router.post("/v1/eggbook/ideas")
def create_idea(
    req: IdeaCreateRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    idea = EggbookIdea(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title=req.title,
        content=req.content
    )
    db.add(idea)
    db.commit()
    db.refresh(idea)
    return {"item": idea_to_dict(idea)}


@router.get("/v1/eggbook/ideas/{idea_id}")
def get_idea(
    idea_id: str,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    idea = (
        db.query(EggbookIdea)
        .filter(EggbookIdea.id == idea_id, EggbookIdea.user_id == user_id)
        .first()
    )
    if not idea:
        raise HTTPException(404, "Idea not found")
    return {"item": idea_to_dict(idea)}


@router.delete("/v1/eggbook/ideas/{idea_id}")
def delete_idea(
    idea_id: str,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    idea = (
        db.query(EggbookIdea)
        .filter(EggbookIdea.id == idea_id, EggbookIdea.user_id == user_id)
        .first()
    )
    if not idea:
        raise HTTPException(404, "Idea not found")
    db.delete(idea)
    db.commit()
    return {"message": "Idea deleted"}


@router.get("/v1/eggbook/todos")
def list_todos(
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    todos = (
        db.query(EggbookTodo)
        .filter(EggbookTodo.user_id == user_id)
        .order_by(EggbookTodo.created_at.desc())
        .all()
    )
    return {"items": [todo_to_dict(todo) for todo in todos]}


@router.post("/v1/eggbook/todos")
def create_todo(
    req: TodoCreateRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    todo = EggbookTodo(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title=req.title
    )
    db.add(todo)
    db.commit()
    db.refresh(todo)
    return {"item": todo_to_dict(todo)}


@router.patch("/v1/eggbook/todos/{todo_id}")
def update_todo(
    todo_id: str,
    req: TodoUpdateRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    todo = (
        db.query(EggbookTodo)
        .filter(EggbookTodo.id == todo_id, EggbookTodo.user_id == user_id)
        .first()
    )
    if not todo:
        raise HTTPException(404, "Todo not found")
    if req.title is not None:
        todo.title = req.title
    if req.isAccepted is not None:
        todo.is_accepted = req.isAccepted
    db.commit()
    db.refresh(todo)
    return {"item": todo_to_dict(todo)}


@router.delete("/v1/eggbook/todos/{todo_id}")
def delete_todo(
    todo_id: str,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    todo = (
        db.query(EggbookTodo)
        .filter(EggbookTodo.id == todo_id, EggbookTodo.user_id == user_id)
        .first()
    )
    if not todo:
        raise HTTPException(404, "Todo not found")
    db.delete(todo)
    db.commit()
    return {"message": "Todo deleted"}


@router.post("/v1/eggbook/todos/{todo_id}/accept")
def accept_todo(
    todo_id: str,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    todo = (
        db.query(EggbookTodo)
        .filter(EggbookTodo.id == todo_id, EggbookTodo.user_id == user_id)
        .first()
    )
    if not todo:
        raise HTTPException(404, "Todo not found")
    todo.is_accepted = True
    todo.is_pinned = True
    db.commit()
    db.refresh(todo)
    return {"item": todo_to_dict(todo)}


@router.post("/v1/eggbook/todos/{todo_id}/schedule")
def schedule_todo(
    todo_id: str,
    req: ScheduleRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    todo = (
        db.query(EggbookTodo)
        .filter(EggbookTodo.id == todo_id, EggbookTodo.user_id == user_id)
        .first()
    )
    if not todo:
        raise HTTPException(404, "Todo not found")
    notification = EggbookNotification(
        id=str(uuid.uuid4()),
        user_id=user_id,
        todo_id=todo.id,
        title=todo.title,
        notify_at=req.notify_at
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return {"item": notification_to_dict(notification)}


@router.get("/v1/eggbook/notifications")
def list_notifications(
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    items = (
        db.query(EggbookNotification)
        .filter(EggbookNotification.user_id == user_id)
        .order_by(EggbookNotification.notify_at.asc())
        .all()
    )
    return {"items": [notification_to_dict(item) for item in items]}


@router.post("/v1/eggbook/notifications")
def create_notification(
    req: NotificationCreateRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    notification = EggbookNotification(
        id=str(uuid.uuid4()),
        user_id=user_id,
        todo_id=req.todo_id,
        title=req.title,
        notify_at=req.notify_at
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return {"item": notification_to_dict(notification)}


@router.patch("/v1/eggbook/notifications/{notification_id}")
def update_notification(
    notification_id: str,
    req: NotificationUpdateRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    notification = (
        db.query(EggbookNotification)
        .filter(
            EggbookNotification.id == notification_id,
            EggbookNotification.user_id == user_id
        )
        .first()
    )
    if not notification:
        raise HTTPException(404, "Notification not found")
    notification.notify_at = req.notify_at
    db.commit()
    db.refresh(notification)
    return {"item": notification_to_dict(notification)}


@router.delete("/v1/eggbook/notifications/{notification_id}")
def delete_notification(
    notification_id: str,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    notification = (
        db.query(EggbookNotification)
        .filter(
            EggbookNotification.id == notification_id,
            EggbookNotification.user_id == user_id
        )
        .first()
    )
    if not notification:
        raise HTTPException(404, "Notification not found")
    db.delete(notification)
    db.commit()
    return {"message": "Notification deleted"}


@router.get("/v1/eggbook/comments")
def list_comments(
    date_str: str = Query(..., alias="date"),
    days: int = Query(7, ge=1, le=7),
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    cutoff_date = date_type.today() - timedelta(days=6)
    (
        db.query(EggbookComment)
        .filter(EggbookComment.user_id == user_id, EggbookComment.date < cutoff_date)
        .delete(synchronize_session=False)
    )
    db.commit()
    try:
        start_date = date_type.fromisoformat(date_str)
    except ValueError as exc:
        raise HTTPException(400, "Invalid date format") from exc
    if start_date < cutoff_date:
        start_date = cutoff_date
    end_date = start_date + timedelta(days=days)
    comments = (
        db.query(EggbookComment)
        .filter(
            EggbookComment.user_id == user_id,
            EggbookComment.date >= start_date,
            EggbookComment.date < end_date
        )
        .order_by(EggbookComment.created_at.desc())
        .all()
    )
    my_egg = [comment_to_dict(c) for c in comments if not bool(c.is_community)]
    community = [comment_to_dict(c) for c in comments if bool(c.is_community)]
    return {"myEgg": my_egg, "community": community}


@router.get("/v1/eggbook/comments/status")
def get_comment_status(
    date_str: str = Query(..., alias="date"),
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    try:
        target_date = date_type.fromisoformat(date_str)
    except ValueError as exc:
        raise HTTPException(400, "Invalid date format") from exc
    return get_comment_generation_state(db, user_id, target_date)


@router.post("/v1/eggbook/comments/generate")
def generate_comments(
    req: CommentGenerateRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    target_date = req.date or date_type.today()
    return trigger_daily_comments_generation(db, user_id, target_date, manual=True)


@router.post("/v1/eggbook/comments")
def create_comment(
    req: CommentCreateRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    comment_date = req.date or date_type.today()
    content = req.content
    if bool(req.isCommunity):
        content = req.egg_comment or req.content
    if not content:
        raise HTTPException(400, "content is required")
    comment = EggbookComment(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content=content,
        egg_name=req.egg_name if bool(req.isCommunity) else None,
        egg_comment=req.egg_comment if bool(req.isCommunity) else None,
        date=comment_date,
        is_community=bool(req.isCommunity)
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return {"item": comment_to_dict(comment)}
