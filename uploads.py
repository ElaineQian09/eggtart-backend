import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from auth import verify_token


router = APIRouter()

UPLOAD_EXPIRES_MINUTES = int(os.getenv("UPLOAD_EXPIRES_MINUTES", "15"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/tmp/egg_uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory signed upload sessions for MVP/testing.
_UPLOAD_SESSIONS: Dict[str, Dict[str, str]] = {}


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


@router.post("/v1/uploads/recording")
def create_recording_upload(
    req: UploadRecordingRequest,
    request: Request,
    authorization: str = Header(...),
):
    user_id = get_user_id(authorization)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=UPLOAD_EXPIRES_MINUTES)

    upload_id = str(uuid.uuid4())
    upload_token = str(uuid.uuid4())
    ext = _safe_ext(req.content_type, req.filename)
    file_name = f"{upload_id}.{ext}"
    file_path = str((UPLOAD_DIR / file_name).resolve())

    _UPLOAD_SESSIONS[upload_id] = {
        "token": upload_token,
        "user_id": user_id,
        "content_type": req.content_type,
        "expires_at": expires_at.isoformat(),
        "file_path": file_path,
    }

    base = str(request.base_url).rstrip("/")
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
):
    session = _UPLOAD_SESSIONS.get(upload_id)
    if not session:
        raise HTTPException(404, "Upload session not found")
    if token != session["token"]:
        raise HTTPException(403, "Invalid upload token")

    expires_at = datetime.fromisoformat(session["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        _UPLOAD_SESSIONS.pop(upload_id, None)
        raise HTTPException(410, "Upload URL expired")

    body = await request.body()
    if not body:
        raise HTTPException(400, "Empty upload body")

    with open(session["file_path"], "wb") as f:
        f.write(body)

    return {"message": "Upload completed", "fileUrl": f"/v1/uploads/files/{upload_id}"}


@router.get("/v1/uploads/files/{upload_id}")
def get_uploaded_file(upload_id: str):
    session = _UPLOAD_SESSIONS.get(upload_id)
    if not session:
        raise HTTPException(404, "File not found")
    file_path = session["file_path"]
    if not os.path.exists(file_path):
        raise HTTPException(404, "File not found")
    return FileResponse(path=file_path, media_type=session["content_type"])
