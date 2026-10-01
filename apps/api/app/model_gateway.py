from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Any, Awaitable, Callable, Protocol, Sequence

import httpx

from .workspace_tools import ToolDefinition


class ModelGateway(Protocol):
    async def complete(self, *, system: str, prompt: str, model: str, tools: Sequence[ToolDefinition] = ()) -> str: ...


@dataclass(slots=True)
class MockGateway:
    """Deterministic local gateway for browser and integration tests."""

    async def complete(self, *, system: str, prompt: str, model: str, tools: Sequence[ToolDefinition] = ()) -> str:
        return f"Mock response for: {prompt}"


@dataclass(slots=True)
class ChatGPTGateway:
    connection_provider: Callable[[], dict[str, Any] | None]
    refresh: Callable[[dict[str, Any]], Awaitable[str | None]] | None = None
    base_url: str = "https://api.openai.com/v1"

    async def complete(self, *, system: str, prompt: str, model: str, tools: Sequence[ToolDefinition] = ()) -> str:
        connection = self.connection_provider()
        if connection and self.refresh and connection.get("refresh_token") and self._expiring(connection.get("expires_at")):
            if not await self.refresh(connection):
                raise RuntimeError("Sesi ChatGPT kedaluwarsa. Hubungkan ulang akun ChatGPT.")
            connection = self.connection_provider()
        token = connection.get("access_token") if connection else None
        if not token:
            raise RuntimeError("ChatGPT belum terhubung. Hubungkan akun ChatGPT terlebih dahulu.")
        codex = connection.get("auth_mode") == "codex"
        if not codex and "chatgpt.tokens.use.direct" not in str(connection.get("scope") or "").split():
            raise RuntimeError("Akun ChatGPT belum mengizinkan penggunaan model.")
        headers = {"Authorization": f"Bearer {token}"}
        endpoint = f"{self.base_url}/responses"
        if codex:
            account_id = connection.get("account_id")
            if not account_id:
                raise RuntimeError("Token ChatGPT tidak memiliki account ID Codex.")
            endpoint = "https://chatgpt.com/backend-api/codex/responses"
            headers.update(
                {
                    "ChatGPT-Account-Id": str(account_id),
                    "originator": "codex_cli_rs",
                    "OpenAI-Beta": "responses=v1",
                    "User-Agent": "codex_cli_rs",
                }
            )
        async with httpx.AsyncClient(timeout=60) as client:
            answer_parts: list[str] = []
            completed = False
            async with client.stream(
                "POST",
                endpoint,
                headers=headers,
                json={
                    "model": model.removeprefix("chatgpt/"),
                    "instructions": system,
                    "input": prompt,
                    "store": False,
                    "stream": True,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line.removeprefix("data: ")
                    if raw == "[DONE]":
                        continue
                    event = json.loads(raw)
                    event_type = event.get("type")
                    if event_type == "response.output_text.delta":
                        answer_parts.append(str(event.get("delta") or ""))
                    elif event_type == "response.failed":
                        error = (event.get("response") or {}).get("error") or {}
                        raise RuntimeError(
                            f"ChatGPT gagal memproses jawaban: {error.get('code', 'unknown_error')}"
                        )
                    elif event_type == "response.completed":
                        completed = True
            if not completed:
                raise RuntimeError("Stream ChatGPT berakhir sebelum response.completed.")
        answer = "".join(answer_parts).strip()
        if not answer:
            raise RuntimeError("ChatGPT mengembalikan response kosong.")
        return answer

    @staticmethod
    def _expiring(value: str | None) -> bool:
        if not value:
            return False
        try:
            return datetime.fromisoformat(value) <= datetime.now(UTC) + timedelta(minutes=1)
        except ValueError:
            return True


@dataclass(slots=True)
class CompositeGateway:
    openrouter: ModelGateway
    chatgpt: ModelGateway

    async def complete(self, *, system: str, prompt: str, model: str, tools: Sequence[ToolDefinition] = ()) -> str:
        gateway = self.chatgpt if model.startswith("chatgpt/") else self.openrouter
        return await gateway.complete(system=system, prompt=prompt, model=model, tools=tools)


@dataclass(slots=True)
class OpenRouterGateway:
    api_key: str | None
    base_url: str

    async def complete(self, *, system: str, prompt: str, model: str, tools: Sequence[ToolDefinition] = ()) -> str:
        if not self.api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not configured. Add it to .env before starting model runs."
            )
        from pydantic_ai import Agent
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        async with httpx.AsyncClient(timeout=60) as client:
            provider = OpenAIProvider(
                base_url=f"{self.base_url}/", api_key=self.api_key, http_client=client
            )
            agent = Agent(OpenAIChatModel(model, provider=provider), instructions=system, retries=1)
            for definition in tools:
                agent.tool_plain(
                    self._tool_function(definition),
                    name=definition.name,
                    description=definition.description,
                    retries=1,
                    timeout=30,
                )
            result = await agent.run(prompt)
        return result.output

    @staticmethod
    def _tool_function(definition: ToolDefinition):
        async def invoke(payload: dict[str, object]) -> dict[str, object]:
            return await definition.handler(dict(payload))
        return invoke
