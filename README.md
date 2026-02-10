# Egg Backend

FastAPI backend for Eggtart, including:
- Anonymous auth + device binding
- Event ingestion (`audio_url`, `screen_recording_url`, `transcript`)
- AI pipeline (STT + Gemini extraction)
- Eggbook modules (ideas, todos, notifications, comments)
- Realtime WS proxy to Gemini Live

## Tech Stack

- Python 3.11
- FastAPI + Uvicorn
- SQLAlchemy
- SQLite (local) or PostgreSQL (production)

## Quick Start

```bash
make venv
make install
make run
```

Server starts at `http://127.0.0.1:8000` by default.

## Environment Variables

Core:
- `DATABASE_URL` (optional; defaults to local SQLite)
- `JWT_SECRET`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`

AI/runtime tuning:
- `AI_USER_COOLDOWN_SEC`
- `AUDIO_BATCH_TRIGGER_COUNT`
- `AUDIO_BATCH_MAX_WAIT_HOURS`
- `AI_QUEUE_MAX_EVENTS_PER_RUN`
- `GEMINI_REQUEST_TIMEOUT_SEC`
- `GEMINI_RETRY_MAX_ATTEMPTS`
- `GEMINI_RETRY_BASE_DELAY_SEC`

Debug flags:
- `DEBUG_HEALTH_ENABLED=1`
- `DEBUG_RESET_ENABLED=1`
- `DEBUG_RESET_KEY=<secret>` (optional)
- `EVENT_DEBUG_ENABLED=1`
- `LIVE_DEBUG_CONTEXT_ENABLED=1`
- `DEBUG_DEVICE_LOOKUP_ENABLED=1`

## API Docs

- Main API reference: `API.md`
- Curl examples: `API-examples.md`

## Useful Endpoints

- `GET /` health check
- `POST /v1/auth/anonymous`
- `GET /v1/auth/whoami`
- `POST /v1/events`
- `PATCH /v1/events/{id}`
- `GET /v1/events/{id}/status`
- `GET /v1/eggbook/sync-status`
- `POST /v1/eggbook/comments/generate`

Debug (when enabled):
- `GET /v1/debug/health`
- `GET /v1/debug/events/{id}/ai-state`
- `GET /v1/debug/live-context`
- `GET /v1/debug/device-bindings`
- `POST /v1/debug/reset-data?scope=events|all`

## Tests

```bash
make test
```
