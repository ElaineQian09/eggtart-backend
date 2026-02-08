import asyncio
import logging
import os
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from websockets import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from auth import verify_token


router = APIRouter()
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_LIVE_WS_URL_TEMPLATE = os.getenv(
    "GEMINI_LIVE_WS_URL_TEMPLATE",
    "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={api_key}",
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


async def _client_to_gemini(client_ws: WebSocket, gemini_ws) -> None:
    while True:
        message = await client_ws.receive()
        msg_type = message.get("type")
        if msg_type == "websocket.disconnect":
            break
        text = message.get("text")
        if text is not None:
            await gemini_ws.send(text)
            continue
        data = message.get("bytes")
        if data is not None:
            await gemini_ws.send(data)


async def _gemini_to_client(client_ws: WebSocket, gemini_ws) -> None:
    while True:
        message = await gemini_ws.recv()
        if isinstance(message, bytes):
            await client_ws.send_bytes(message)
        else:
            await client_ws.send_text(message)


@router.websocket("/v1/realtime/ws")
async def realtime_ws_proxy(websocket: WebSocket):
    token = _extract_token(websocket)
    if not token:
        await websocket.close(code=4401, reason="Missing auth token")
        return
    try:
        verify_token(token)
    except Exception:
        await websocket.close(code=4401, reason="Invalid auth token")
        return

    if not GEMINI_API_KEY:
        await websocket.close(code=4500, reason="Gemini API key is not configured")
        return

    await websocket.accept()
    ws_url = _build_live_ws_url()

    try:
        async with ws_connect(ws_url, open_timeout=15, close_timeout=10) as gemini_ws:
            logger.info("Realtime WS connected to Gemini Live")
            tasks = {
                asyncio.create_task(_client_to_gemini(websocket, gemini_ws)),
                asyncio.create_task(_gemini_to_client(websocket, gemini_ws)),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, (WebSocketDisconnect, ConnectionClosed)):
                    raise exc
    except WebSocketDisconnect:
        logger.info("Client realtime websocket disconnected")
    except ConnectionClosed:
        logger.info("Gemini realtime websocket disconnected")
    except Exception as exc:
        logger.exception("Realtime WS proxy error: %s", exc)
        try:
            await websocket.close(code=1011, reason="Realtime proxy error")
        except Exception:
            pass
