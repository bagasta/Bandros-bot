from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol, Sequence

import httpx

from .workspace_tools import ToolDefinition


class ModelGateway(Protocol):
    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        model: str,
        tools: Sequence[ToolDefinition] = (),
        request_limit: int = 8,
    ) -> str: ...


@dataclass(slots=True)
class MockGateway:
    """Deterministic local gateway for browser and integration tests."""

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        model: str,
        tools: Sequence[ToolDefinition] = (),
        request_limit: int = 8,
    ) -> str:
        return f"Mock response for: {prompt}"


@dataclass(slots=True)
class ChatGPTGateway:
    connection_provider: Callable[[], dict[str, Any] | None]
    refresh: Callable[[dict[str, Any]], Awaitable[str | None]] | None = None
    base_url: str = "https://api.openai.com/v1"

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        model: str,
        tools: Sequence[ToolDefinition] = (),
        request_limit: int = 8,
    ) -> str:
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
        if codex:
            account_id = connection.get("account_id")
            if not account_id:
                raise RuntimeError("Token ChatGPT tidak memiliki account ID Codex.")
            headers.update(
                {
                    "ChatGPT-Account-Id": str(account_id),
                    "originator": "codex_cli_rs",
                    "OpenAI-Beta": "responses=v1",
                    "User-Agent": "codex_cli_rs",
                }
            )
        from openai import AsyncOpenAI
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        model_name = model.removeprefix("chatgpt/")
        if codex:
            http_client = httpx.AsyncClient(timeout=120, headers=headers)
            openai_client = AsyncOpenAI(
                api_key=str(token),
                base_url="https://chatgpt.com/backend-api/codex",
                default_headers=headers,
                http_client=http_client,
            )
            provider = OpenAIProvider(openai_client=openai_client)
            model_settings = {
                "openai_store": False,
                "extra_headers": headers,
            }
        else:
            http_client = httpx.AsyncClient(timeout=120)
            provider = OpenAIProvider(
                base_url=f"{self.base_url}/",
                api_key=str(token),
                http_client=http_client,
            )
            model_settings = {"openai_store": False}
        try:
            return await run_pydantic_agent(
                model=OpenAIResponsesModel(model_name, provider=provider),
                system=system,
                prompt=prompt,
                tools=tools,
                model_settings=model_settings,
                request_limit=request_limit,
            )
        finally:
            await http_client.aclose()

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

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        model: str,
        tools: Sequence[ToolDefinition] = (),
        request_limit: int = 8,
    ) -> str:
        gateway = self.chatgpt if model.startswith("chatgpt/") else self.openrouter
        return await gateway.complete(
            system=system,
            prompt=prompt,
            model=model,
            tools=tools,
            request_limit=request_limit,
        )


@dataclass(slots=True)
class OpenRouterGateway:
    api_key: str | None
    base_url: str

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        model: str,
        tools: Sequence[ToolDefinition] = (),
        request_limit: int = 8,
    ) -> str:
        if not self.api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not configured. Add it to .env before starting model runs."
            )
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        async with httpx.AsyncClient(timeout=60) as client:
            provider = OpenAIProvider(
                base_url=f"{self.base_url}/", api_key=self.api_key, http_client=client
            )
            return await run_pydantic_agent(
                model=OpenAIChatModel(model, provider=provider),
                system=system,
                prompt=prompt,
                tools=tools,
                request_limit=request_limit,
            )


async def run_pydantic_agent(
    *,
    model: Any,
    system: str,
    prompt: str,
    tools: Sequence[ToolDefinition],
    model_settings: dict[str, Any] | None = None,
    request_limit: int = 8,
) -> str:
    """Run the PRD tool loop: model call, tool result, repeat."""
    from pydantic_ai import Agent
    from pydantic_ai.usage import UsageLimits

    agent = Agent(model, instructions=system, retries=1)
    for definition in tools:
        agent.tool_plain(
            _tool_function(definition),
            name=definition.name,
            description=definition.description,
            retries=1,
            timeout=definition.timeout_seconds,
        )
    result = await agent.run(
        prompt,
        model_settings=model_settings,
        usage_limits=UsageLimits(request_limit=request_limit),
    )
    answer = str(result.output).strip()
    if not answer:
        raise RuntimeError("Model mengembalikan response kosong.")
    return answer


def _tool_function(definition: ToolDefinition):
    async def invoke(payload: dict[str, object]) -> dict[str, object]:
        return await definition.handler(dict(payload))

    return invoke
