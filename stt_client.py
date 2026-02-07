import base64
import logging
import os
from typing import Any, Dict, Optional

import httpx


logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_STT_MODEL = os.getenv("GEMINI_STT_MODEL", os.getenv("GEMINI_MODEL", "gemini-3-pro-preview"))
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_AUDIO_BYTES = int(os.getenv("STT_MAX_AUDIO_BYTES", str(10 * 1024 * 1024)))


def stt_enabled() -> bool:
    return bool(GEMINI_API_KEY)


def _extract_text(response_json: Dict[str, Any]) -> str:
    candidates = response_json.get("candidates") or []
    if not candidates:
        return ""
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    if not parts:
        return ""
    return (parts[0].get("text") or "").strip()


def _guess_audio_mime(content_type: Optional[str]) -> str:
    if content_type and content_type.startswith("audio/"):
        return content_type
    return "audio/webm"


def transcribe_audio_from_url(recording_url: str) -> str:
    if not stt_enabled():
        raise ValueError("STT is not enabled: GEMINI_API_KEY is missing")

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        media_resp = client.get(recording_url)
        media_resp.raise_for_status()
        audio_bytes = media_resp.content
        if not audio_bytes:
            raise ValueError("Audio file is empty")
        if len(audio_bytes) > MAX_AUDIO_BYTES:
            raise ValueError(f"Audio too large for STT ({len(audio_bytes)} bytes)")

        mime_type = _guess_audio_mime(media_resp.headers.get("content-type"))
        audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

        prompt = (
            "Transcribe this audio into plain text.\n"
            "Rules:\n"
            "- Output only the transcript text.\n"
            "- Keep original language.\n"
            "- Do not add explanations.\n"
            "- If speech is unclear, output your best-effort transcript."
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": mime_type, "data": audio_b64}},
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
            },
        }
        headers = {"x-goog-api-key": GEMINI_API_KEY}
        url = f"{GEMINI_BASE_URL}/{GEMINI_STT_MODEL}:generateContent"
        llm_resp = client.post(url, json=payload, headers=headers)
        llm_resp.raise_for_status()
        logger.info("STT request succeeded with model=%s", GEMINI_STT_MODEL)
        transcript = _extract_text(llm_resp.json())
        return transcript.strip()
