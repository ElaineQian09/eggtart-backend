# tests/test_smoke.py

import os
import tempfile
from datetime import date

from fastapi.testclient import TestClient


tmp_db = tempfile.NamedTemporaryFile(prefix="egg_test_", suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{tmp_db.name}"

from main import app


client = TestClient(app)


def get_token(device_id: str = "device-test-001") -> str:
    resp = client.post(
        "/v1/auth/anonymous",
        json={
            "device_id": device_id,
            "device_model": "iPhone",
            "os": "iOS",
            "language": "en",
            "timezone": "UTC-6"
        }
    )
    assert resp.status_code == 200
    return resp.json()["token"]


def test_health_check():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_auth_and_device_register():
    token = get_token()
    resp = client.post(
        "/v1/devices",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": "device-test-001",
            "device_model": "iPhone",
            "os": "iOS",
            "language": "en",
            "timezone": "UTC-6"
        }
    )
    assert resp.status_code == 200
    assert resp.json()["deviceId"] == "device-test-001"


def test_memory_create():
    token = get_token("device-test-002")
    resp = client.post(
        "/v1/memory",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "type": "note",
            "content": "hello",
            "importance": 0.5
        }
    )
    assert resp.status_code == 200


def test_eggbook_idea_and_todo_flow():
    token = get_token("device-test-003")

    idea_resp = client.post(
        "/v1/eggbook/ideas",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "Idea A", "content": "Build a thing"}
    )
    assert idea_resp.status_code == 200
    idea_id = idea_resp.json()["item"]["id"]

    list_resp = client.get(
        "/v1/eggbook/ideas",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert list_resp.status_code == 200
    assert any(item["id"] == idea_id for item in list_resp.json()["items"])

    todo_resp = client.post(
        "/v1/eggbook/todos",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "Ship MVP"}
    )
    assert todo_resp.status_code == 200
    todo_id = todo_resp.json()["item"]["id"]

    accept_resp = client.post(
        f"/v1/eggbook/todos/{todo_id}/accept",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert accept_resp.status_code == 200
    assert accept_resp.json()["item"]["isAccepted"] is True


def test_eggbook_comments():
    token = get_token("device-test-004")
    today = date.today().isoformat()
    resp = client.post(
        "/v1/eggbook/comments",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "content": "Nice progress"
        }
    )
    assert resp.status_code == 200

    list_resp = client.get(
        f"/v1/eggbook/comments?date={today}&days=1",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()["myEgg"]) >= 1
