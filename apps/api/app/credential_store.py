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
        return bool(
            os.getenv("BLOB_READ_WRITE_TOKEN")
            or os.getenv("VERCEL_BLOB_READ_WRITE_TOKEN")
        )

    async def put(self, kind: str, identifier: str, value: dict[str, Any]) -> None:
        key = self._key(kind, identifier)
        if not self.durable:
            self._memory[key] = dict(value)
            return
        from vercel.blob import AsyncBlobClient

        payload = json.dumps(value).encode()
        async with AsyncBlobClient() as client:
            await client.put(
                key,
                payload,
                access="private",
                content_type="application/json",
                overwrite=True,
            )

    async def get(self, kind: str, identifier: str) -> dict[str, Any] | None:
        key = self._key(kind, identifier)
        if not self.durable:
            value = self._memory.get(key)
            return dict(value) if value else None
        from vercel._internal.blob.errors import BlobNotFoundError
        from vercel.blob import AsyncBlobClient

        try:
            async with AsyncBlobClient() as client:
                result = await client.get(key, access="private", use_cache=False)
        except BlobNotFoundError:
            return None
        except Exception:
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
            async with AsyncBlobClient() as client:
                await client.delete(key)
        except Exception:
            return
