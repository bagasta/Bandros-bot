from __future__ import annotations

import asyncio

from apps.api.app.credential_store import CredentialStore


def test_memory_credential_store_round_trip(monkeypatch) -> None:
    monkeypatch.delenv("BLOB_STORE_ID", raising=False)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL_BLOB_READ_WRITE_TOKEN", raising=False)
    store = CredentialStore()
    assert store.durable is False

    async def exercise() -> None:
        await store.put("sessions", "secret-session", {"access_token": "token"})
        assert await store.get("sessions", "secret-session") == {
            "access_token": "token"
        }
        await store.delete("sessions", "secret-session")
        assert await store.get("sessions", "secret-session") is None

    asyncio.run(exercise())


def test_credential_store_is_durable_with_blob_token(monkeypatch) -> None:
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_token")
    assert CredentialStore().durable is True


def test_sealed_session_survives_a_suspended_blob_store(monkeypatch) -> None:
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_token")
    monkeypatch.setenv("BANDROS_TOKEN_SECRET", "test-secret")

    class SuspendedBlob:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def put(self, *args, **kwargs):
            raise RuntimeError("Vercel Blob: This store has been suspended.")

        async def get(self, *args, **kwargs):
            raise RuntimeError("Vercel Blob: This store has been suspended.")

    monkeypatch.setattr("vercel.blob.AsyncBlobClient", lambda: SuspendedBlob())
    store = CredentialStore()
    payload = {"account_id": "acct_a", "access_token": "token"}
    token = store.seal(payload)

    async def exercise() -> None:
        await store.put("sessions", token, payload)
        assert await store.get("sessions", token) == payload
        assert await CredentialStore().get("sessions", token) == payload

    asyncio.run(exercise())
