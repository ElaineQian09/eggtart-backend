import base64
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx


logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_STT_MODEL = os.getenv("STT_GEMINI_MODEL", os.getenv("GEMINI_STT_MODEL", os.getenv("GEMINI_MODEL", "gemini-3-pro-preview")))
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_AUDIO_BYTES = int(os.getenv("STT_MAX_AUDIO_BYTES", str(10 * 1024 * 1024)))
STT_REQUEST_TIMEOUT_SEC = float(os.getenv("STT_REQUEST_TIMEOUT_SEC", "60"))
STT_RETRY_MAX_ATTEMPTS = int(os.getenv("STT_RETRY_MAX_ATTEMPTS", "4"))
STT_RETRY_BASE_DELAY_SEC = float(os.getenv("STT_RETRY_BASE_DELAY_SEC", "1.0"))
TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}


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


def _post_gemini_with_retry(client: httpx.Client, url: str, payload: Dict[str, Any], headers: Dict[str, str]):
    for attempt in range(1, STT_RETRY_MAX_ATTEMPTS + 1):
        try:
            resp = client.post(url, json=payload, headers=headers)
        except httpx.ReadTimeout:
            delay = STT_RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))
            logger.warning(
                "STT Gemini read timeout, model=%s, attempt=%s/%s, sleeping %.2fs",
                GEMINI_STT_MODEL,
                attempt,
                STT_RETRY_MAX_ATTEMPTS,
                delay,
            )
            if attempt == STT_RETRY_MAX_ATTEMPTS:
                raise
            time.sleep(delay)
            continue

        if resp.status_code not in TRANSIENT_STATUSES:
            resp.raise_for_status()
            return resp

        retry_after = resp.headers.get("retry-after")
        if retry_after is not None:
            try:
                delay = max(float(retry_after), 0.5)
            except ValueError:
                delay = STT_RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))
        else:
            delay = STT_RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))

        logger.warning(
            "STT Gemini transient status=%s, model=%s, attempt=%s/%s, sleeping %.2fs",
            resp.status_code,
            GEMINI_STT_MODEL,
            attempt,
            STT_RETRY_MAX_ATTEMPTS,
            delay,
        )
        if attempt == STT_RETRY_MAX_ATTEMPTS:
            resp.raise_for_status()
        time.sleep(delay)

    raise RuntimeError("STT Gemini retry loop exited unexpectedly")


def transcribe_audio_from_url(recording_url: str) -> str:
    if not stt_enabled():
        raise ValueError("STT is not enabled: GEMINI_API_KEY is missing")

    with httpx.Client(timeout=STT_REQUEST_TIMEOUT_SEC, follow_redirects=True) as client:
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
        llm_resp = _post_gemini_with_retry(client, url, payload, headers)
        logger.info("STT request succeeded with model=%s", GEMINI_STT_MODEL)
        transcript = _extract_text(llm_resp.json())
        return transcript.strip()
