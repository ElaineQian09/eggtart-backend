# eggbook.py

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date, timedelta
import uuid

from database import get_db
from auth import verify_token
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

    content: str
    date: Optional[date] = None
    isCommunity: Optional[bool] = False


def idea_to_dict(idea: EggbookIdea):
    return {
        "id": idea.id,
        "title": idea.title,
        "content": idea.content,
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
    return {
        "id": comment.id,
        "content": comment.content,
        "date": comment.date.isoformat(),
        "isCommunity": bool(comment.is_community),
        "createdAt": comment.created_at.isoformat()
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
    days: int = Query(7, ge=1, le=30),
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    try:
        start_date = date.fromisoformat(date_str)
    except ValueError as exc:
        raise HTTPException(400, "Invalid date format") from exc
    end_date = start_date + timedelta(days=days)
    comments = (
        db.query(EggbookComment)
        .filter(
            EggbookComment.date >= start_date,
            EggbookComment.date < end_date
        )
        .order_by(EggbookComment.created_at.desc())
        .all()
    )
    my_egg = [comment_to_dict(c) for c in comments if c.user_id == user_id]
    community = [comment_to_dict(c) for c in comments if c.user_id != user_id]
    return {"myEgg": my_egg, "community": community}


@router.post("/v1/eggbook/comments")
def create_comment(
    req: CommentCreateRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):
    user_id = get_user_id(authorization)
    comment_date = req.date or date.today()
    comment = EggbookComment(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content=req.content,
        date=comment_date,
        is_community=bool(req.isCommunity)
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return {"item": comment_to_dict(comment)}
