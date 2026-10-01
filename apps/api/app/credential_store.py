from __future__ import annotations

import hashlib
import json
import os
from typing import Any


class CredentialStore:
    """Persist OAuth flows and sessions in private Blob on Vercel."""

    def __init__(self) -> None:
        self._memory: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _key(kind: str, identifier: str) -> str:
        digest = hashlib.sha256(identifier.encode()).hexdigest()
        return f"oauth/{kind}/{digest}.json"

    @property
    def durable(self) -> bool:
        return bool(os.getenv("BLOB_STORE_ID") and os.getenv("VERCEL"))

    async def put(self, kind: str, identifier: str, value: dict[str, Any]) -> None:
        key = self._key(kind, identifier)
        if not self.durable:
            self._memory[key] = dict(value)
            return
        from vercel.blob import AsyncBlobClient

        await AsyncBlobClient().put(
            key,
            json.dumps(value).encode(),
            access="private",
            content_type="application/json",
            overwrite=True,
        )

    async def get(self, kind: str, identifier: str) -> dict[str, Any] | None:
        key = self._key(kind, identifier)
        if not self.durable:
            value = self._memory.get(key)
            return dict(value) if value else None
        from vercel.blob import AsyncBlobClient

        try:
            result = await AsyncBlobClient().get(key, access="private", use_cache=False)
        except Exception:
            return None
        if result is None or result.status_code != 200:
            return None
        value = json.loads(result.content)
        return value if isinstance(value, dict) else None

    async def delete(self, kind: str, identifier: str) -> None:
        key = self._key(kind, identifier)
        if not self.durable:
            self._memory.pop(key, None)
            return
        from vercel.blob import AsyncBlobClient

        try:
            await AsyncBlobClient().delete(key)
        except Exception:
            return
