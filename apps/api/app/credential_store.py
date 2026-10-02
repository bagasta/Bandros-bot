from __future__ import annotations

import base64
import hashlib
import hmac
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

    def seal(self, value: dict[str, Any]) -> str:
        """Carry a login payload inside the token so a suspended Blob store cannot block sign-in."""
        body = base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")
        signature = hmac.new(self._secret(), body.encode(), hashlib.sha256).hexdigest()
        return f"v1.{body}.{signature}"

    def unseal(self, token: str) -> dict[str, Any] | None:
        if not token.startswith("v1."):
            return None
        try:
            _, body, signature = token.split(".", 2)
        except ValueError:
            return None
        expected = hmac.new(self._secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None
        try:
            value = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except (ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _secret(self) -> bytes:
        raw = (
            os.getenv("BANDROS_TOKEN_SECRET")
            or os.getenv("BLOB_READ_WRITE_TOKEN")
            or os.getenv("VERCEL_BLOB_READ_WRITE_TOKEN")
            or "bandros-dev"
        )
        return raw.encode()

    async def put(self, kind: str, identifier: str, value: dict[str, Any]) -> None:
        key = self._key(kind, identifier)
        if not self.durable:
            self._memory[key] = dict(value)
            return
        from vercel.blob import AsyncBlobClient

        payload = json.dumps(value).encode()
        try:
            async with AsyncBlobClient() as client:
                await client.put(
                    key,
                    payload,
                    access="private",
                    content_type="application/json",
                    overwrite=True,
                )
        except Exception:
            self._memory[key] = dict(value)

    async def get(self, kind: str, identifier: str) -> dict[str, Any] | None:
        key = self._key(kind, identifier)
        if not self.durable:
            value = self._memory.get(key)
            return dict(value) if value else self.unseal(identifier)
        from vercel._internal.blob.errors import BlobNotFoundError
        from vercel.blob import AsyncBlobClient

        try:
            async with AsyncBlobClient() as client:
                result = await client.get(key, access="private", use_cache=False)
        except BlobNotFoundError:
            return self.unseal(identifier)
        except Exception:
            remembered = self._memory.get(key)
            return dict(remembered) if remembered else self.unseal(identifier)
        value = json.loads(result.content)
        return value if isinstance(value, dict) else self.unseal(identifier)

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
