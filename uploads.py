import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import verify_token
from database import get_db
from models import UploadSession


router = APIRouter()

UPLOAD_EXPIRES_MINUTES = int(os.getenv("UPLOAD_EXPIRES_MINUTES", "15"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/tmp/egg_uploads"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_user_id(authorization: str) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid token")
    token = authorization.replace("Bearer ", "")
    return verify_token(token)


class UploadRecordingRequest(BaseModel):
    content_type: str
    filename: Optional[str] = None
    size_bytes: Optional[int] = None


def _safe_ext(content_type: str, filename: Optional[str]) -> str:
    if filename and "." in filename:
        return filename.rsplit(".", 1)[1].lower()
    if content_type == "audio/m4a":
        return "m4a"
    if content_type == "audio/mp4":
        return "mp4"
    if content_type == "audio/webm":
        return "webm"
    if content_type == "video/mp4":
        return "mp4"
    return "bin"


def _public_base(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    return str(request.base_url).rstrip("/")


@router.post("/v1/uploads/recording")
def create_recording_upload(
    req: UploadRecordingRequest,
    request: Request,
    authorization: str = Header(...),
    db: Session = Depends(get_db),
):
    user_id = get_user_id(authorization)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=UPLOAD_EXPIRES_MINUTES)

    upload_id = str(uuid.uuid4())
    upload_token = str(uuid.uuid4())
    ext = _safe_ext(req.content_type, req.filename)
    file_name = f"{upload_id}.{ext}"
    file_path = str((UPLOAD_DIR / file_name).resolve())

    session = UploadSession(
        id=upload_id,
        user_id=user_id,
        upload_token=upload_token,
        content_type=req.content_type,
        file_path=file_path,
        expires_at=expires_at.replace(tzinfo=None),
    )
    db.add(session)
    db.commit()

    base = _public_base(request)
    upload_url = f"{base}/v1/uploads/recording/{upload_id}?token={upload_token}"
    file_url = f"{base}/v1/uploads/files/{upload_id}"

    return {
        "uploadUrl": upload_url,
        "fileUrl": file_url,
        "expiresAt": expires_at.isoformat(),
    }


@router.put("/v1/uploads/recording/{upload_id}")
async def upload_recording_file(
    upload_id: str,
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):
    session = db.query(UploadSession).filter(UploadSession.id == upload_id).first()
    if not session:
        raise HTTPException(404, "Upload session not found")
    if token != session.upload_token:
        raise HTTPException(403, "Invalid upload token")

    expires_at = session.expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        db.delete(session)
        db.commit()
        raise HTTPException(410, "Upload URL expired")

    body = await request.body()
    if not body:
        raise HTTPException(400, "Empty upload body")

    with open(session.file_path, "wb") as f:
        f.write(body)

    return {
        "message": "Upload completed",
        "fileUrl": f"{_public_base(request)}/v1/uploads/files/{upload_id}",
    }


@router.get("/v1/uploads/files/{upload_id}")
def get_uploaded_file(upload_id: str, db: Session = Depends(get_db)):
    session = db.query(UploadSession).filter(UploadSession.id == upload_id).first()
    if not session:
        raise HTTPException(404, "File not found")
    file_path = session.file_path
    if not os.path.exists(file_path):
        raise HTTPException(404, "File not found")
    return FileResponse(path=file_path, media_type=session.content_type)
