import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Set

from fastapi import WebSocket


logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class EggbookSyncBroker:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_by_user: Dict[str, Set[WebSocket]] = {}
        self._sse_queues_by_user: Dict[str, Set[asyncio.Queue[str]]] = {}

    def ensure_loop(self) -> None:
        loop = asyncio.get_running_loop()
        with self._guard:
            if self._loop is None or self._loop.is_closed():
                self._loop = loop

    async def register_ws(self, user_id: str, websocket: WebSocket) -> None:
        self.ensure_loop()
        with self._guard:
            peers = self._ws_by_user.setdefault(user_id, set())
            peers.add(websocket)

    async def unregister_ws(self, user_id: str, websocket: WebSocket) -> None:
        with self._guard:
            peers = self._ws_by_user.get(user_id) or set()
            peers.discard(websocket)
            if not peers and user_id in self._ws_by_user:
                del self._ws_by_user[user_id]

    async def register_sse(self, user_id: str) -> asyncio.Queue[str]:
        self.ensure_loop()
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        with self._guard:
            peers = self._sse_queues_by_user.setdefault(user_id, set())
            peers.add(queue)
        return queue

    async def unregister_sse(self, user_id: str, queue: asyncio.Queue[str]) -> None:
        with self._guard:
            peers = self._sse_queues_by_user.get(user_id) or set()
            peers.discard(queue)
            if not peers and user_id in self._sse_queues_by_user:
                del self._sse_queues_by_user[user_id]

    def publish(
        self,
        user_id: str,
        processing: bool,
        updates: bool,
        reason: str,
        source_event_id: Optional[str] = None,
        updated_tabs: Optional[list[str]] = None,
        sequence: Optional[int] = None,
        state_changed_at: Optional[str] = None,
        state: Optional[str] = None,
    ) -> None:
        with self._guard:
            loop = self._loop
        if loop is None or loop.is_closed():
            return

        envelope = {
            "type": "eggbook.sync",
            "version": 1,
            "eventId": str(uuid.uuid4()),
            "timestamp": _utc_now_iso(),
            "data": {
                "processing": bool(processing),
                "updates": bool(updates),
                "reason": reason,
                "sourceEventId": source_event_id,
                "updatedTabs": updated_tabs or [],
                "sequence": sequence,
                "stateChangedAt": state_changed_at,
                "state": state,
            },
        }
        event_text = json.dumps(envelope, ensure_ascii=True)
        asyncio.run_coroutine_threadsafe(self._broadcast(user_id, event_text), loop)

    async def _broadcast(self, user_id: str, event_text: str) -> None:
        with self._guard:
            sockets = list(self._ws_by_user.get(user_id) or [])
            queues = list(self._sse_queues_by_user.get(user_id) or [])

        if not sockets and not queues:
            return

        stale_sockets: list[WebSocket] = []
        for websocket in sockets:
            try:
                await websocket.send_text(event_text)
            except Exception:
                stale_sockets.append(websocket)

        for queue in queues:
            if queue.full():
                try:
                    _ = queue.get_nowait()
                except Exception:
                    pass
            try:
                queue.put_nowait(event_text)
            except Exception:
                logger.exception("Failed enqueueing eggbook sync SSE message")

        if stale_sockets:
            with self._guard:
                peers = self._ws_by_user.get(user_id) or set()
                for websocket in stale_sockets:
                    peers.discard(websocket)
                if not peers and user_id in self._ws_by_user:
                    del self._ws_by_user[user_id]


_BROKER = EggbookSyncBroker()


def get_eggbook_sync_broker() -> EggbookSyncBroker:
    return _BROKER


def publish_eggbook_sync_event(
    user_id: str,
    processing: bool,
    updates: bool,
    reason: str,
    source_event_id: Optional[str] = None,
    updated_tabs: Optional[list[str]] = None,
    sequence: Optional[int] = None,
    state_changed_at: Optional[str] = None,
    state: Optional[str] = None,
) -> None:
    _BROKER.publish(
        user_id=user_id,
        processing=processing,
        updates=updates,
        reason=reason,
        source_event_id=source_event_id,
        updated_tabs=updated_tabs,
        sequence=sequence,
        state_changed_at=state_changed_at,
        state=state,
    )
