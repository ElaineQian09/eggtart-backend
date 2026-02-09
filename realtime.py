import asyncio
import json
import logging
import os
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from websockets import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from auth import verify_token


router = APIRouter()
logger = logging.getLogger("uvicorn.error")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_LIVE_WS_URL_TEMPLATE = os.getenv(
    "GEMINI_LIVE_WS_URL_TEMPLATE",
    "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={api_key}",
)


def _extract_token(websocket: WebSocket) -> str:
    token = websocket.query_params.get("token")
    if token:
        return token

    auth_header = websocket.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.replace("Bearer ", "", 1)
    return ""


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
    host = (websocket.client.host if websocket.client else "unknown")
    port = (websocket.client.port if websocket.client else "unknown")
    return f"{host}:{port}"


async def _client_to_gemini(client_ws: WebSocket, gemini_ws, conn_id: str) -> None:
    while True:
        message = await client_ws.receive()
        msg_type = message.get("type")
        if msg_type == "websocket.disconnect":
            logger.info("Realtime[%s] client disconnected", conn_id)
            break
        text = message.get("text")
        if text is not None:
            if '"setup"' in text:
                logger.info("Realtime[%s] forwarding setup payload to Gemini", conn_id)
            await gemini_ws.send(text)
            continue
        data = message.get("bytes")
        if data is not None:
            await gemini_ws.send(data)


async def _gemini_to_client(client_ws: WebSocket, gemini_ws, conn_id: str) -> None:
    first_message_logged = False
    while True:
        message = await gemini_ws.recv()
        if isinstance(message, bytes):
            if not first_message_logged:
                logger.info("Realtime[%s] first upstream message is bytes len=%s", conn_id, len(message))
                first_message_logged = True
            await client_ws.send_bytes(message)
        else:
            if not first_message_logged:
                preview = message[:300]
                logger.info("Realtime[%s] first upstream message text=%s", conn_id, preview)
                first_message_logged = True
            if '"error"' in message:
                logger.warning("Realtime[%s] Gemini payload contains error", conn_id)
            await client_ws.send_text(message)


@router.websocket("/v1/realtime/ws")
async def realtime_ws_proxy(websocket: WebSocket):
    conn_id = _connection_meta(websocket)
    token = _extract_token(websocket)
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
        "Realtime[%s] accepted user_id=%s, connecting Gemini url=%s",
        conn_id,
        user_id,
        _sanitize_ws_url(ws_url),
    )

    try:
        async with ws_connect(ws_url, open_timeout=15, close_timeout=10) as gemini_ws:
            logger.info("Realtime[%s] connected to Gemini Live", conn_id)
            tasks = {
                asyncio.create_task(
                    _client_to_gemini(websocket, gemini_ws, conn_id),
                    name="client_to_gemini",
                ),
                asyncio.create_task(
                    _gemini_to_client(websocket, gemini_ws, conn_id),
                    name="gemini_to_client",
                ),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            done_names = [task.get_name() for task in done]
            logger.info("Realtime[%s] relay first completed tasks=%s", conn_id, done_names)
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
                        "Realtime[%s] task=%s upstream closed code=%s reason=%s",
                        conn_id,
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
        logger.info("Realtime[%s] client websocket disconnected", conn_id)
    except ConnectionClosed as exc:
        code = getattr(exc, "code", None)
        reason = getattr(exc, "reason", "")
        logger.info("Realtime[%s] Gemini websocket disconnected code=%s reason=%s", conn_id, code, reason)
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
        logger.exception("Realtime[%s] proxy error: %s", conn_id, exc)
        try:
            if websocket.client_state.name == "CONNECTED":
                await websocket.send_text(
                    '{"type":"error","source":"proxy","message":"Realtime proxy error"}'
                )
                await websocket.close(code=1011, reason="Realtime proxy error")
        except Exception:
            pass
