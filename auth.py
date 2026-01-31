# auth.py

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from models import User
import uuid
import jwt
import datetime


router = APIRouter()

SECRET_KEY = "egg-secret-key"
ALGORITHM = "HS256"


def create_token(user_id: str):

    payload = {
        "user_id": user_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(days=30)
    }

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str):

    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

    return payload["user_id"]


@router.post("/v1/auth/anonymous")
def anonymous_login(db: Session = Depends(get_db)):

    user_id = str(uuid.uuid4())

    user = User(id=user_id)

    db.add(user)
    db.commit()

    token = create_token(user_id)

    return {
        "userId": user_id,
        "token": token
    }
