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
  "recording_url": "string or null",
  "transcript": "string or null",
  "duration_sec": 0 (optional),
  "event_at": "datetime (optional, default now)"
}
Response:
{
  "eventId": "string",
  "status": "pending"
}

PATCH /v1/events/{id}
Request body:
{
  "recording_url": "string (optional)",
  "transcript": "string (optional)",
  "duration_sec": 0 (optional),
  "event_at": "datetime (optional)"
}
Response:
{
  "eventId": "string",
  "status": "pending | transcribing | processed | failed"
}

GET /v1/events/{id}
Response:
{
  "eventId": "string",
  "deviceId": "string",
  "recordingUrl": "string or null",
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

## Egg Book / Ideas

GET /v1/eggbook/ideas
Response:
{
  "items": [
    {
      "id": "string",
      "title": "string or null",
      "content": "string",
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
    "title": "string or null",
    "content": "string",
    "createdAt": "datetime",
    "updatedAt": "datetime"
  }
}

GET /v1/eggbook/ideas/{id}
Response:
{
  "item": {
    "id": "string",
    "title": "string or null",
    "content": "string",
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
Response:
{
  "myEgg": [
    {
      "id": "string",
      "content": "string",
      "date": "date",
      "isCommunity": false,
      "createdAt": "datetime"
    }
  ],
  "community": [
    {
      "id": "string",
      "content": "string",
      "date": "date",
      "isCommunity": true,
      "createdAt": "datetime"
    }
  ]
}

POST /v1/eggbook/comments
Request body:
{
  "content": "string",
  "date": "date (optional)",
  "isCommunity": false (optional)
}
Response:
{
  "item": {
    "id": "string",
    "content": "string",
    "date": "date",
    "isCommunity": false,
    "createdAt": "datetime"
  }
}

---

## Event Aggregation & AI Pipeline (Server Behavior)

Aggregation logic is per-user and per-day:
- Aggregate events where `recording_url` is null and `transcript` is not null.
- Use a rolling window (e.g. last 10 minutes) to batch LLM calls.
- LLM output is structured into `eggbook_ideas`, `eggbook_todos`, `eggbook_alerts`.
- Each output row stores `source_event_id` for traceability.
- Events are marked `processed` on success; `failed` on error.

Comment generation:
- A daily cron job generates comments based on the day’s eggbook entries.
- Writes to `eggbook_comments` with `date` for readback.
