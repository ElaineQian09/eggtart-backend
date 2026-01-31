# memory.py

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from database import get_db
from models import Memory
from auth import verify_token
import uuid


router = APIRouter()


class MemoryRequest(BaseModel):

    type: str
    content: str
    importance: float


@router.post("/v1/memory")
def save_memory(
    req: MemoryRequest,
    authorization: str = Header(...),
    db: Session = Depends(get_db)
):

    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid token")

    token = authorization.replace("Bearer ", "")

    user_id = verify_token(token)

    memory = Memory(
        id=str(uuid.uuid4()),
        user_id=user_id,
        type=req.type,
        content=req.content,
        importance=req.importance
    )

    db.add(memory)
    db.commit()

    return {
        "message": "Memory saved"
    }
