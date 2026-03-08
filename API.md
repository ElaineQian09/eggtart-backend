# Egg Backend API (Draft)

Base URL:
https://eggtart-backend-production-2361.up.railway.app

All protected endpoints require:
Authorization: Bearer <token>

Date/time format:
- date: YYYY-MM-DD
- datetime: ISO 8601 (e.g. 2026-01-31T09:00:00Z)

---

## Auth

POST /v1/auth/anonymous
Request body:
{
  "device_id": "string",
  "device_model": "string (optional)",
  "os": "string (optional)",
  "language": "string (optional)",
  "timezone": "string (optional)"
}
Response:
{
  "userId": "string",
  "token": "string",
  "deviceId": "string"
}

---

## Devices

POST /v1/devices
Request body:
{
  "device_id": "string",
  "device_model": "string",
  "os": "string",
  "language": "string",
  "timezone": "string"
}
Response:
{
  "message": "Device registered",
  "deviceId": "string"
}

---

## Memory

POST /v1/memory
Request body:
{
  "type": "string",
  "content": "string",
  "importance": 0.0
}
Response:
{
  "message": "Memory saved"
}

---

## Events

POST /v1/events
Request body:
{
  "device_id": "string",
  "audio_url": "string or null",
  "screen_recording_url": "string or null",
  "transcript": "string or null",
  "duration_sec": 0 (optional),
  "event_at": "datetime (optional, default now)"
}
Response:
{
  "eventId": "string",
  "deviceId": "string",
  "audioUrl": "string or null",
  "screenRecordingUrl": "string or null",
  "mediaKind": "voice | screen",
  "transcript": "string or null",
  "durationSec": 0,
  "eventAt": "datetime",
  "status": "pending | transcribing | processed | failed",
  "createdAt": "datetime",
  "updatedAt": "datetime"
}

PATCH /v1/events/{id}
Request body:
{
  "audio_url": "string (optional)",
  "screen_recording_url": "string (optional)",
  "transcript": "string (optional)",
  "duration_sec": 0 (optional),
  "event_at": "datetime (optional)",
  "status": "pending | transcribing | processed | failed (optional)",
  "device_local_now": "ISO8601 datetime with timezone offset (optional, recommended; e.g. 2026-03-07T21:12:30-06:00)",
  "deviceLocalNow": "same as device_local_now (optional camelCase alias)"
}
Response:
{
  "eventId": "string",
  "deviceId": "string",
  "audioUrl": "string or null",
  "screenRecordingUrl": "string or null",
  "mediaKind": "voice | screen",
  "transcript": "string or null",
  "durationSec": 0,
  "eventAt": "datetime",
  "status": "pending | transcribing | processed | failed",
  "createdAt": "datetime",
  "updatedAt": "datetime"
}

GET /v1/events/{id}
Response:
{
  "eventId": "string",
  "deviceId": "string",
  "audioUrl": "string or null",
  "screenRecordingUrl": "string or null",
  "mediaKind": "voice | screen",
  "transcript": "string or null",
  "durationSec": 0,
  "eventAt": "datetime",
  "status": "pending | transcribing | processed | failed",
  "createdAt": "datetime",
  "updatedAt": "datetime"
}

GET /v1/events/{id}/status
Response:
{
  "status": "pending | transcribing | processed | failed"
}

---

## Uploads

POST /v1/uploads/recording
Request body:
{
  "content_type": "audio/m4a or video/mp4",
  "filename": "string (optional)",
  "size_bytes": 0 (optional)
}
Response:
{
  "uploadUrl": "string",
  "fileUrl": "string",
  "expiresAt": "datetime"
}

---

## Egg Book / Sync

GET /v1/eggbook/sync-status
Response:
{
  "status": "ok",
  "lastSyncAt": "datetime or null",
  "processing": true,
  "hasUpdates": false,
  "syncState": "idle | processing | updated | failed",
  "sequence": 12,
  "stateChangedAt": "datetime or null",
  "sourceEventId": "string or null"
}

WS /v1/eggbook/ws?token=<jwt>
- Auth: query `token` or `Authorization: Bearer <token>` header
- Server pushes event text JSON envelope:
{
  "type": "eggbook.sync",
  "version": 1,
  "eventId": "uuid",
  "timestamp": "2026-02-20T12:34:56Z",
  "data": {
    "processing": true,
    "updates": false,
    "state": "idle | processing | updated | failed",
    "sequence": 12,
    "stateChangedAt": "datetime or null",
    "reason": "event_queued | ai_processing_started | ai_processing_done | eggbook_materialized | error",
    "sourceEventId": "optional-event-id",
    "updatedTabs": ["ideas", "todos", "notifications", "comments"]
  }
}

GET /v1/eggbook/stream
- Auth: query `token` or `Authorization: Bearer <token>` header
- Response `text/event-stream`
- Event name: `eggbook.sync`
- Data payload is the same JSON envelope as WS.

---

## Egg Book / Ideas

GET /v1/eggbook/ideas
Response:
{
  "items": [
    {
      "id": "string",
      "source_event_id": "string or null",
      "sourceEventId": "string or null",
      "title": "string or null",
      "content": "string",
      "screen_recording_url": "string or null",
      "screenRecordingUrl": "string or null",
      "audio_url": "string or null",
      "audioUrl": "string or null",
      "mediaKind": "voice | screen",
      "createdAt": "datetime",
      "updatedAt": "datetime"
    }
  ]
}

POST /v1/eggbook/ideas
Request body:
{
  "title": "string (optional)",
  "content": "string"
}
Response:
{
  "item": {
    "id": "string",
    "source_event_id": "string or null",
    "sourceEventId": "string or null",
    "title": "string or null",
    "content": "string",
    "screen_recording_url": "string or null",
    "screenRecordingUrl": "string or null",
    "audio_url": "string or null",
    "audioUrl": "string or null",
    "mediaKind": "voice | screen",
    "createdAt": "datetime",
    "updatedAt": "datetime"
  }
}

GET /v1/eggbook/ideas/{id}
Response:
{
  "item": {
    "id": "string",
    "source_event_id": "string or null",
    "sourceEventId": "string or null",
    "title": "string or null",
    "content": "string",
    "screen_recording_url": "string or null",
    "screenRecordingUrl": "string or null",
    "audio_url": "string or null",
    "audioUrl": "string or null",
    "mediaKind": "voice | screen",
    "createdAt": "datetime",
    "updatedAt": "datetime"
  }
}

DELETE /v1/eggbook/ideas/{id}
Response:
{
  "message": "Idea deleted"
}

---

## Egg Book / Todos

GET /v1/eggbook/todos
Response:
{
  "items": [
    {
      "id": "string",
      "title": "string",
      "isAccepted": true,
      "isPinned": false,
      "createdAt": "datetime",
      "updatedAt": "datetime"
    }
  ]
}

POST /v1/eggbook/todos
Request body:
{
  "title": "string"
}
Response:
{
  "item": {
    "id": "string",
    "title": "string",
    "isAccepted": false,
    "isPinned": false,
    "createdAt": "datetime",
    "updatedAt": "datetime"
  }
}

PATCH /v1/eggbook/todos/{id}
Request body:
{
  "title": "string (optional)",
  "isAccepted": true (optional)
}
Response:
{
  "item": {
    "id": "string",
    "title": "string",
    "isAccepted": true,
    "isPinned": false,
    "createdAt": "datetime",
    "updatedAt": "datetime"
  }
}

POST /v1/eggbook/todos/{id}/accept
Response:
{
  "item": {
    "id": "string",
    "title": "string",
    "isAccepted": true,
    "isPinned": true,
    "createdAt": "datetime",
    "updatedAt": "datetime"
  }
}

POST /v1/eggbook/todos/{id}/schedule
Request body:
{
  "notify_at": "datetime"
}
Response:
{
  "item": {
    "id": "string",
    "title": "string",
    "todoId": "string",
    "notifyAt": "datetime",
    "createdAt": "datetime",
    "updatedAt": "datetime"
  }
}

DELETE /v1/eggbook/todos/{id}
Response:
{
  "message": "Todo deleted"
}

---

## Egg Book / Notifications

GET /v1/eggbook/notifications
Response:
{
  "items": [
    {
      "id": "string",
      "title": "string",
      "todoId": "string or null",
      "notifyAt": "datetime",
      "createdAt": "datetime",
      "updatedAt": "datetime"
    }
  ]
}

POST /v1/eggbook/notifications
Request body:
{
  "title": "string",
  "notify_at": "datetime",
  "todo_id": "string (optional)"
}
Response:
{
  "item": {
    "id": "string",
    "title": "string",
    "todoId": "string or null",
    "notifyAt": "datetime",
    "createdAt": "datetime",
    "updatedAt": "datetime"
  }
}

PATCH /v1/eggbook/notifications/{id}
Request body:
{
  "notify_at": "datetime"
}
Response:
{
  "item": {
    "id": "string",
    "title": "string",
    "todoId": "string or null",
    "notifyAt": "datetime",
    "createdAt": "datetime",
    "updatedAt": "datetime"
  }
}

DELETE /v1/eggbook/notifications/{id}
Response:
{
  "message": "Notification deleted"
}

---

## Egg Book / Comments

GET /v1/eggbook/comments?date=YYYY-MM-DD&days=7
Notes:
- `days` max is 7 (only latest 7 days are retained).

Response:
{
  "myEgg": [
    {
      "id": "string",
      "content": "string",
      "eggName": "string or null",
      "eggComment": "string or null",
      "date": "date",
      "isCommunity": false,
      "createdAt": "datetime"
    }
  ],
  "community": [
    {
      "id": "string",
      "content": "string",
      "eggName": "string or null",
      "eggComment": "string or null",
      "date": "date",
      "isCommunity": true,
      "createdAt": "datetime"
    }
  ]
}

GET /v1/eggbook/comments/status?date=YYYY-MM-DD
Response:
{
  "date": "date",
  "status": "idle | generating | ready | failed",
  "hasInput": true,
  "activeDurationSec": 3600,
  "canManualTrigger": true
}

POST /v1/eggbook/comments/generate
Request body:
{
  "date": "date (optional, default today)"
}
Response:
{
  "date": "date",
  "status": "idle | generating | ready | failed",
  "hasInput": true,
  "activeDurationSec": 3600,
  "canManualTrigger": true
}

POST /v1/eggbook/comments
Request body:
{
  "content": "string (optional)",
  "egg_name": "string (optional, mainly for community comment)",
  "egg_comment": "string (optional, mainly for community comment)",
  "date": "date (optional)",
  "isCommunity": false (optional)
}
Response:
{
  "item": {
    "id": "string",
    "content": "string",
    "eggName": "string or null",
    "eggComment": "string or null",
    "date": "date",
    "isCommunity": false,
    "createdAt": "datetime"
  }
}

---

## Event Aggregation & AI Pipeline (Server Behavior)

- AI/STT trigger:
  - `POST /v1/events` stores event only (no immediate AI processing).
  - `PATCH /v1/events/{id}` triggers STT + AI queue processing.
- STT behavior:
  - If `transcript` is empty and `audio_url` is present, backend attempts STT first.
  - On STT success, transcript is written back to event.
- Event inference behavior:
  - Single inference for event when `screen_recording_url` is present.
  - Batch inference for unprocessed events where:
    - `screen_recording_url` is null
    - `transcript` is not null
- AI outputs are persisted into:
  - `eggbook_ideas`
  - `eggbook_todos`
  - `eggbook_notifications` (used as alert storage)
  - `eggbook_comments` (including structured community fields `egg_name`, `egg_comment`)
- Event status:
  - `transcribing` while STT/AI is in progress
  - `processed` on success
  - `failed` on STT/AI failure
- Comments generation rules:
  - Automatic generation is evaluated daily when AI pipeline runs.
  - Auto trigger requires:
    - At least one voice/screen input event on that date.
    - Daily active duration (`sum(duration_sec)`) >= 3600 seconds.
  - Manual trigger (`POST /v1/eggbook/comments/generate`) can run even when active duration < 3600, as long as there is input.
  - Generation status is tracked per day: `idle | generating | ready | failed`.
  - On successful generation, backend writes a notification entry indicating comments are ready.
  - Comment data is retained for the latest 7 days only.
