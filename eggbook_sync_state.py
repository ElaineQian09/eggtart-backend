import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from eggbook_sync_push import publish_eggbook_sync_event
from models import EggbookSyncState


SYNC_IDLE = "idle"
SYNC_PROCESSING = "processing"
SYNC_UPDATED = "updated"
SYNC_FAILED = "failed"
VALID_SYNC_STATES = {SYNC_IDLE, SYNC_PROCESSING, SYNC_UPDATED, SYNC_FAILED}


def _now_utc_naive() -> datetime:
    return datetime.utcnow()


def _to_json_tabs(updated_tabs: Optional[list[str]]) -> str:
    return json.dumps(updated_tabs or [], ensure_ascii=True)


def _from_json_tabs(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except Exception:
        pass
    return []


def _get_or_create_state(db: Session, user_id: str) -> EggbookSyncState:
    row = db.query(EggbookSyncState).filter(EggbookSyncState.user_id == user_id).first()
    if row:
        return row
    row = EggbookSyncState(
        user_id=user_id,
        state=SYNC_IDLE,
        sequence=0,
        last_source_event_id=None,
        state_changed_at=_now_utc_naive(),
        updated_tabs_json=_to_json_tabs([]),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _can_transition(
    current_state: str,
    target_state: str,
    current_source_event_id: Optional[str],
    next_source_event_id: Optional[str],
) -> bool:
    if target_state not in VALID_SYNC_STATES:
        return False
    if current_state == target_state:
        return False

    if target_state == SYNC_PROCESSING:
        if current_state in {SYNC_IDLE, SYNC_FAILED}:
            return True
        if current_state == SYNC_UPDATED:
            # After updated, only allow processing when a new source event arrives.
            if not next_source_event_id:
                return False
            return next_source_event_id != (current_source_event_id or "")
        return False

    if target_state in {SYNC_UPDATED, SYNC_FAILED}:
        return current_state == SYNC_PROCESSING

    if target_state == SYNC_IDLE:
        return current_state == SYNC_PROCESSING

    return False


def _row_to_snapshot(row: EggbookSyncState) -> dict[str, Any]:
    return {
        "state": row.state,
        "sequence": int(row.sequence or 0),
        "stateChangedAt": row.state_changed_at.isoformat() if row.state_changed_at else None,
        "sourceEventId": row.last_source_event_id,
        "updatedTabs": _from_json_tabs(row.updated_tabs_json),
        "reason": row.last_reason,
    }


def get_sync_state_snapshot(db: Session, user_id: str) -> dict[str, Any]:
    row = _get_or_create_state(db, user_id)
    return _row_to_snapshot(row)


def transition_sync_state_and_publish(
    db: Session,
    user_id: str,
    target_state: str,
    reason: str,
    source_event_id: Optional[str] = None,
    updated_tabs: Optional[list[str]] = None,
) -> dict[str, Any]:
    row = _get_or_create_state(db, user_id)
    if not _can_transition(
        current_state=row.state,
        target_state=target_state,
        current_source_event_id=row.last_source_event_id,
        next_source_event_id=source_event_id,
    ):
        return _row_to_snapshot(row)

    row.state = target_state
    row.sequence = int(row.sequence or 0) + 1
    row.state_changed_at = _now_utc_naive()
    row.last_reason = reason
    if source_event_id:
        row.last_source_event_id = source_event_id
    if target_state == SYNC_UPDATED:
        row.updated_tabs_json = _to_json_tabs(updated_tabs or [])
    elif target_state in {SYNC_PROCESSING, SYNC_FAILED, SYNC_IDLE}:
        row.updated_tabs_json = _to_json_tabs([])

    db.commit()
    db.refresh(row)
    snapshot = _row_to_snapshot(row)

    publish_eggbook_sync_event(
        user_id=user_id,
        processing=(snapshot["state"] == SYNC_PROCESSING),
        updates=(snapshot["state"] == SYNC_UPDATED),
        reason=reason,
        source_event_id=snapshot["sourceEventId"],
        updated_tabs=snapshot["updatedTabs"],
        sequence=snapshot["sequence"],
        state_changed_at=snapshot["stateChangedAt"],
        state=snapshot["state"],
    )
    return snapshot
