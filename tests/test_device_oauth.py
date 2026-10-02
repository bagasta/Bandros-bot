from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.app.main import app


class _Response:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def is_error(self) -> bool:
        return not self.is_success

    def json(self) -> dict[str, object]:
        return self._payload


class _Client:
    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, url: str, **kwargs):
        if url.endswith("/usercode"):
            return _Response(
                200,
                {
                    "device_auth_id": "deviceauth_test",
                    "user_code": "ABCD-1234",
                    "interval": "5",
                    "verification_uri": "https://auth.openai.com/codex/device",
                },
            )
        return _Response(403, {"error": {"message": "authorization pending", "type": "invalid_request_error"}})


def _jwt(payload: dict[str, object]) -> str:
    import base64
    import json

    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{body}.sig"


class _ApprovedClient(_Client):
    async def post(self, url: str, **kwargs):
        if url.endswith("/usercode"):
            return await super().post(url, **kwargs)
        if url.endswith("/token") and "oauth" not in url:
            return _Response(200, {"authorization_code": "code", "code_verifier": "verifier"})
        return _Response(
            200,
            {
                "access_token": _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_test"}}),
                "refresh_token": "refresh",
                "id_token": _jwt({"email": "owner@example.com", "sub": "user"}),
                "expires_in": 3600,
            },
        )

    async def get(self, url: str, **kwargs):
        return _Response(401, {"detail": "Unauthorized"})


def test_device_poll_connects_when_model_catalog_fails(monkeypatch) -> None:
    monkeypatch.setattr("apps.api.app.main.httpx.AsyncClient", _ApprovedClient)
    with TestClient(app) as client:
        started = client.post("/api/v1/auth/chatgpt/device/start")
        polled = client.post(
            "/api/v1/auth/chatgpt/device/poll",
            json={"flow_id": started.json()["flow_id"]},
        )

    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "connected"
    assert body["preferred_model"] == "gpt-6-luna"
    assert body["email"] == "owner@example.com"
    from apps.api.app.credential_store import CredentialStore

    sealed = CredentialStore().unseal(body["session_token"])
    assert sealed is not None
    assert sealed["account_id"] == "acct_test"
    assert "models" not in sealed
    assert "id_token" not in sealed


def test_device_poll_treats_pending_error_object_as_pending(monkeypatch) -> None:
    monkeypatch.setattr("apps.api.app.main.httpx.AsyncClient", _Client)
    with TestClient(app) as client:
        started = client.post("/api/v1/auth/chatgpt/device/start")
        flow_id = started.json()["flow_id"]
        polled = client.post("/api/v1/auth/chatgpt/device/poll", json={"flow_id": flow_id})

    assert started.status_code == 200
    assert started.json()["user_code"] == "ABCD-1234"
    assert polled.status_code == 200
    assert polled.json()["status"] == "pending"
