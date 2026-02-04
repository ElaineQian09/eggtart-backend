# Egg Backend API Examples (Postman/Insomnia)

Base URL:
`https://eggtart-backend-production-2361.up.railway.app`

Headers:
- `Content-Type: application/json`
- `Authorization: Bearer <token>` (for protected endpoints)

---

## 1) Auth (Anonymous)

POST `/v1/auth/anonymous`
```json
{
  "device_id": "device-001",
  "device_model": "iPhone",
  "os": "iOS 17.2",
  "language": "en",
  "timezone": "UTC-6"
}
```

Response (example):
```json
{
  "userId": "uuid",
  "token": "jwt-token",
  "deviceId": "device-001"
}
```

---

## 2) Devices

POST `/v1/devices`
```json
{
  "device_id": "device-001",
  "device_model": "iPhone",
  "os": "iOS 17.2",
  "language": "en",
  "timezone": "UTC-6"
}
```

---

## 3) Events

POST `/v1/events`
```json
{
  "device_id": "device-001",
  "recording_url": null,
  "transcript": "I want to remember to call Alex tomorrow",
  "duration_sec": 0,
  "event_at": "2026-02-04T09:00:00Z"
}
```

PATCH `/v1/events/{event_id}`
```json
{
  "recording_url": "https://cdn.example.com/recordings/abc.mp4",
  "duration_sec": 120
}
```

GET `/v1/events/{event_id}`  
GET `/v1/events/{event_id}/status`

---

## 4) Uploads (Signed URL)

POST `/v1/uploads/recording`
```json
{
  "content_type": "video/mp4",
  "filename": "recording.mp4",
  "size_bytes": 12345678
}
```

Response (example):
```json
{
  "uploadUrl": "https://storage.example.com/signed-upload",
  "fileUrl": "https://storage.example.com/recordings/recording.mp4",
  "expiresAt": "2026-02-04T09:30:00Z"
}
```

Then:
- Use `PUT {uploadUrl}` with raw file body.
- Store `fileUrl` in `events.recording_url`.

---

## 5) Eggbook Ideas

POST `/v1/eggbook/ideas`
```json
{
  "title": "Build MVP",
  "content": "Focus on core features first"
}
```

GET `/v1/eggbook/ideas`

---

## 6) Eggbook Todos

POST `/v1/eggbook/todos`
```json
{
  "title": "Ship v1"
}
```

POST `/v1/eggbook/todos/{todo_id}/accept`

GET `/v1/eggbook/todos`

---

## 7) Eggbook Comments

POST `/v1/eggbook/comments`
```json
{
  "content": "Nice progress today"
}
```

GET `/v1/eggbook/comments?date=2026-02-04&days=1`

---

## 8) Memory

POST `/v1/memory`
```json
{
  "type": "note",
  "content": "Remember to review roadmap",
  "importance": 0.5
}
```
