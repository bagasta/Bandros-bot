from __future__ import annotations

import asyncio

from apps.api.app.credential_store import CredentialStore


def test_memory_credential_store_round_trip(monkeypatch) -> None:
    monkeypatch.delenv("BLOB_STORE_ID", raising=False)
    store = CredentialStore()

    async def exercise() -> None:
        await store.put("sessions", "secret-session", {"access_token": "token"})
        assert await store.get("sessions", "secret-session") == {
            "access_token": "token"
        }
        await store.delete("sessions", "secret-session")
        assert await store.get("sessions", "secret-session") is None

    asyncio.run(exercise())
