import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from websockets import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from auth import verify_token
from database import SessionLocal
from models import EggbookIdea, EggbookNotification, EggbookTodo


router = APIRouter()
logger = logging.getLogger("uvicorn.error")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_LIVE_WS_URL_TEMPLATE = os.getenv(
    "GEMINI_LIVE_WS_URL_TEMPLATE",
    "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={api_key}",
)
LIVE_MODEL_DEFAULT = os.getenv(
    "LIVE_MODEL",
    "models/gemini-2.5-flash-native-audio-preview-12-2025",
)
LIVE_PROMPT_TEXT = os.getenv(
    "LIVE_PROMPT_TEXT",
    "You are Coco, a warm and concise companion. Never claim your name is Gemini. "
    "If asked your name, answer Coco. Keep replies short and practical.",
)
LIVE_PROMPT_VERSION = os.getenv("LIVE_PROMPT_VERSION", "v1")
LIVE_PROMPT_INJECTION_MODE = os.getenv("LIVE_PROMPT_INJECTION_MODE", "setup")  # setup|none
LIVE_INCLUDE_CONTEXT = os.getenv("LIVE_INCLUDE_CONTEXT", "1") == "1"


def _extract_token_from_ws(websocket: WebSocket) -> str:
    token = websocket.query_params.get("token")
    if token:
        return token
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.replace("Bearer ", "", 1)
    return ""


def _extract_token_from_header(authorization: str) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Invalid token")
    return authorization.replace("Bearer ", "", 1)


def _build_live_ws_url() -> str:
    if "{api_key}" in GEMINI_LIVE_WS_URL_TEMPLATE:
        return GEMINI_LIVE_WS_URL_TEMPLATE.format(api_key=GEMINI_API_KEY)
    parsed = urlparse(GEMINI_LIVE_WS_URL_TEMPLATE)
    query = dict(parse_qsl(parsed.query))
    query["key"] = GEMINI_API_KEY
    return urlunparse(parsed._replace(query=urlencode(query)))


def _sanitize_ws_url(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    if "key" in query:
        query["key"] = "***"
    return urlunparse(parsed._replace(query=urlencode(query)))


def _connection_meta(websocket: WebSocket) -> str:
    host = websocket.client.host if websocket.client else "unknown"
    port = websocket.client.port if websocket.client else "unknown"
    return f"{host}:{port}"


def _fetch_top3_context(user_id: str) -> dict:
    db = SessionLocal()
    try:
        ideas = (
            db.query(EggbookIdea)
            .filter(EggbookIdea.user_id == user_id)
            .order_by(EggbookIdea.created_at.desc())
            .limit(3)
            .all()
        )
        todos = (
            db.query(EggbookTodo)
            .filter(EggbookTodo.user_id == user_id)
            .order_by(EggbookTodo.created_at.desc())
            .limit(3)
            .all()
        )
        alerts = (
            db.query(EggbookNotification)
            .filter(EggbookNotification.user_id == user_id)
            .order_by(EggbookNotification.created_at.desc())
            .limit(3)
            .all()
        )
        return {
            "scrolling_idea_title": [i.title or "" for i in ideas],
            "scrolling_idea_detail": [i.content or "" for i in ideas],
            "todo_item": [t.title or "" for t in todos],
            "alert": [a.title or "" for a in alerts],
        }
    finally:
        db.close()


def _build_live_prompt(user_id: str) -> str:
    prompt = LIVE_PROMPT_TEXT.strip()
    if not LIVE_INCLUDE_CONTEXT:
        return prompt

    ctx = _fetch_top3_context(user_id)
    lines = [
        "",
        "Latest top3 context (for grounding only):",
        f"scrolling_idea_title: {ctx['scrolling_idea_title']}",
        f"scrolling_idea_detail: {ctx['scrolling_idea_detail']}",
        f"todo_item: {ctx['todo_item']}",
        f"alert: {ctx['alert']}",
    ]
    return prompt + "\n".join(lines)


def _build_canonical_setup(raw_text: str, user_id: str, runtime_state: dict) -> str:
    try:
        incoming = json.loads(raw_text)
    except Exception:
        incoming = {}

    setup = (incoming.get("setup") or {}) if isinstance(incoming, dict) else {}
    generation_config = setup.get("generationConfig") or {}
    response_modalities = generation_config.get("responseModalities") or ["AUDIO"]

    canonical = {
        "setup": {
            "model": setup.get("model") or LIVE_MODEL_DEFAULT,
            "generationConfig": {
                "responseModalities": response_modalities,
            },
        }
    }
    if isinstance(setup.get("inputAudioConfig"), dict):
        canonical["setup"]["inputAudioConfig"] = setup["inputAudioConfig"]

    prompt_mode = LIVE_PROMPT_INJECTION_MODE
    prompt_version = LIVE_PROMPT_VERSION
    if prompt_mode == "setup":
        prompt_text = _build_live_prompt(user_id)
        canonical["setup"]["systemInstruction"] = {
            "parts": [{"text": prompt_text}]
        }

    setup_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()[:12]

    runtime_state["prompt_injection_mode"] = prompt_mode
    runtime_state["prompt_version"] = prompt_version
    runtime_state["setup_sent_hash"] = setup_hash
    runtime_state["model"] = canonical["setup"]["model"]
    return json.dumps(canonical, ensure_ascii=True)


async def _client_to_gemini(client_ws: WebSocket, gemini_ws, conn_id: str, runtime_state: dict, user_id: str) -> None:
    while True:
        message = await client_ws.receive()
        msg_type = message.get("type")
        if msg_type == "websocket.disconnect":
            logger.info("Realtime[%s] client disconnected", conn_id)
            break

        text = message.get("text")
        if text is not None:
            try:
                payload = json.loads(text)
            except Exception:
                payload = None

            if payload and isinstance(payload, dict) and "setup" in payload:
                if runtime_state.get("setup_forwarded"):
                    logger.warning("Realtime[%s] duplicate setup ignored", conn_id)
                    continue
                canonical_setup_text = _build_canonical_setup(text, user_id, runtime_state)
                logger.info(
                    "Realtime[%s] sending canonical setup model=%s prompt_version=%s mode=%s setup_hash=%s",
                    conn_id,
                    runtime_state.get("model"),
                    runtime_state.get("prompt_version"),
                    runtime_state.get("prompt_injection_mode"),
                    runtime_state.get("setup_sent_hash"),
                )
                await gemini_ws.send(canonical_setup_text)
                runtime_state["setup_forwarded"] = True
                continue

            if payload and isinstance(payload, dict) and "realtimeInput" in payload:
                media_chunks = (payload.get("realtimeInput") or {}).get("mediaChunks") or []
                if media_chunks:
                    runtime_state["sent_chunk_count"] += len(media_chunks)
                    if not runtime_state.get("first_chunk_logged"):
                        first_chunk = media_chunks[0] if isinstance(media_chunks[0], dict) else {}
                        shell = {
                            "realtimeInput": {
                                "mediaChunks": [{
                                    "mimeType": first_chunk.get("mimeType"),
                                    "data": "<omitted>",
                                }]
                            }
                        }
                        logger.info(
                            "Realtime[%s] forwarding first chunk shell=%s",
                            conn_id,
                            json.dumps(shell, ensure_ascii=True),
                        )
                        runtime_state["first_chunk_logged"] = True
                        runtime_state["first_chunk_at"] = time.time()
            await gemini_ws.send(text)
            continue

        data = message.get("bytes")
        if data is not None:
            runtime_state["sent_chunk_count"] += 1
            if not runtime_state.get("first_chunk_logged"):
                logger.info("Realtime[%s] forwarding first binary chunk len=%s", conn_id, len(data))
                runtime_state["first_chunk_logged"] = True
                runtime_state["first_chunk_at"] = time.time()
            await gemini_ws.send(data)


async def _gemini_to_client(client_ws: WebSocket, gemini_ws, conn_id: str, runtime_state: dict) -> None:
    first_message_logged = False
    while True:
        try:
            message = await gemini_ws.recv()
            if isinstance(message, bytes):
                runtime_state["recv_chunk_count"] += 1
                if not first_message_logged:
                    logger.info("Realtime[%s] first upstream message is bytes len=%s", conn_id, len(message))
                    first_message_logged = True
                if runtime_state.get("first_chunk_logged") and not runtime_state.get("post_chunk_message_logged"):
                    logger.info(
                        "Realtime[%s] first upstream response after chunk is bytes len=%s",
                        conn_id,
                        len(message),
                    )
                    runtime_state["post_chunk_message_logged"] = True
                await client_ws.send_bytes(message)
            else:
                if not first_message_logged:
                    logger.info("Realtime[%s] first upstream message text=%s", conn_id, message[:500])
                    first_message_logged = True
                if runtime_state.get("first_chunk_logged") and not runtime_state.get("post_chunk_message_logged"):
                    logger.info(
                        "Realtime[%s] first upstream response after chunk text=%s",
                        conn_id,
                        message[:500],
                    )
                    runtime_state["post_chunk_message_logged"] = True
                if '"error"' in message:
                    logger.warning("Realtime[%s] Gemini payload contains error", conn_id)
                await client_ws.send_text(message)
        except ConnectionClosed as exc:
            code = getattr(exc, "code", None)
            reason = getattr(exc, "reason", "")
            logger.info(
                "Realtime[%s] gemini_to_client closed by upstream code=%s reason=%s",
                conn_id,
                code,
                reason,
            )
            raise


@router.get("/v1/live/config")
def live_config(authorization: str = Header(...)):
    token = _extract_token_from_header(authorization)
    verify_token(token)
    return {
        "model": LIVE_MODEL_DEFAULT,
        "promptVersion": LIVE_PROMPT_VERSION,
        "promptInjectionMode": LIVE_PROMPT_INJECTION_MODE,
        "supportsPcm16k": True,
    }


@router.websocket("/v1/realtime/ws")
async def realtime_ws_proxy(websocket: WebSocket):
    session_id = str(uuid.uuid4())
    conn_id = _connection_meta(websocket)
    token = _extract_token_from_ws(websocket)
    if not token:
        await websocket.close(code=4401, reason="Missing auth token")
        return
    try:
        user_id = verify_token(token)
    except Exception:
        await websocket.close(code=4401, reason="Invalid auth token")
        return

    if not GEMINI_API_KEY:
        await websocket.close(code=4500, reason="Gemini API key is not configured")
        return

    await websocket.accept()
    ws_url = _build_live_ws_url()
    logger.info(
        "Realtime[%s] session=%s accepted user_id=%s, connecting Gemini url=%s",
        conn_id,
        session_id,
        user_id,
        _sanitize_ws_url(ws_url),
    )

    runtime_state = {
        "setup_forwarded": False,
        "first_chunk_logged": False,
        "first_chunk_at": None,
        "post_chunk_message_logged": False,
        "sent_chunk_count": 0,
        "recv_chunk_count": 0,
        "prompt_injection_mode": "none",
        "prompt_version": LIVE_PROMPT_VERSION,
        "setup_sent_hash": None,
        "model": LIVE_MODEL_DEFAULT,
    }

    try:
        async with ws_connect(ws_url, open_timeout=15, close_timeout=10) as gemini_ws:
            logger.info("Realtime[%s] session=%s connected to Gemini Live", conn_id, session_id)
            tasks = {
                asyncio.create_task(
                    _client_to_gemini(websocket, gemini_ws, conn_id, runtime_state, user_id),
                    name="client_to_gemini",
                ),
                asyncio.create_task(
                    _gemini_to_client(websocket, gemini_ws, conn_id, runtime_state),
                    name="gemini_to_client",
                ),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            done_names = [task.get_name() for task in done]
            logger.info("Realtime[%s] session=%s relay first completed tasks=%s", conn_id, session_id, done_names)
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is None:
                    continue
                if isinstance(exc, ConnectionClosed):
                    code = getattr(exc, "code", None)
                    reason = getattr(exc, "reason", "")
                    logger.info(
                        "Realtime[%s] session=%s task=%s upstream closed code=%s reason=%s",
                        conn_id,
                        session_id,
                        task.get_name(),
                        code,
                        reason,
                    )
                    continue
                if not isinstance(exc, WebSocketDisconnect):
                    raise exc
            if websocket.client_state.name == "CONNECTED":
                await websocket.close(code=1000, reason="Session ended")
    except WebSocketDisconnect:
        logger.info("Realtime[%s] session=%s client websocket disconnected", conn_id, session_id)
    except ConnectionClosed as exc:
        code = getattr(exc, "code", None)
        reason = getattr(exc, "reason", "")
        logger.info(
            "Realtime[%s] session=%s Gemini websocket disconnected code=%s reason=%s",
            conn_id,
            session_id,
            code,
            reason,
        )
        try:
            if websocket.client_state.name == "CONNECTED":
                await websocket.send_text(
                    (
                        '{"type":"error","source":"gemini","message":"Gemini websocket disconnected",'
                        f'"upstreamCloseCode":{int(code) if code is not None else "null"},'
                        f'"upstreamCloseReason":{json.dumps(reason or "")}'
                        "}"
                    )
                )
                await websocket.close(code=1011, reason="Gemini websocket disconnected")
        except Exception:
            pass
    except Exception as exc:
        logger.exception("Realtime[%s] session=%s proxy error: %s", conn_id, session_id, exc)
        try:
            if websocket.client_state.name == "CONNECTED":
                await websocket.send_text(
                    '{"type":"error","source":"proxy","message":"Realtime proxy error"}'
                )
                await websocket.close(code=1011, reason="Realtime proxy error")
        except Exception:
            pass
    finally:
        logger.info(
            "Realtime[%s] session=%s end model=%s prompt_version=%s mode=%s sent_chunk_count=%s recv_chunk_count=%s setup_hash=%s",
            conn_id,
            session_id,
            runtime_state.get("model"),
            runtime_state.get("prompt_version"),
            runtime_state.get("prompt_injection_mode"),
            runtime_state.get("sent_chunk_count"),
            runtime_state.get("recv_chunk_count"),
            runtime_state.get("setup_sent_hash"),
        )
