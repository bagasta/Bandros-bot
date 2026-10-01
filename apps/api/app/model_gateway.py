from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol, Sequence

import httpx
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

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
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/responses",
                headers={"Authorization": f"Bearer {token}"},
                json={"model": model.removeprefix("chatgpt/"), "instructions": system, "input": prompt, "store": False},
            )
            response.raise_for_status()
            payload = response.json()
        output = payload.get("output", [])
        texts = [
            item.get("text", "")
            for message in output
            if isinstance(message, dict)
            for item in message.get("content", [])
            if isinstance(item, dict)
        ]
        answer = "".join(texts).strip()
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
