from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol, Sequence

import httpx

from .settings import running_on_vercel
from .workspace_tools import ToolDefinition

MODEL_UNAVAILABLE = "Model belum tersedia."


class ApprovalRequired(Exception):
    """Raised so a destructive tool stops the model loop and waits for the user."""


def _find_approval(error: BaseException) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ApprovalRequired):
            return True
        current = current.__cause__ or current.__context__
    return False


class ModelUnavailable(RuntimeError):
    """Raised when a turn has no real model to call."""


def preferred_chatgpt_model(connection: dict[str, Any] | None, fallback: str | None = None) -> str | None:
    """Return the session ChatGPT model, prefixed so the composite gateway will not use mock."""
    if not connection:
        return None
    scope = str(connection.get("scope") or "").split()
    usable = connection.get("auth_mode") == "codex" or "chatgpt.tokens.use.direct" in scope
    token = connection.get("access_token")
    if not usable or not isinstance(token, str) or not token.strip():
        return None
    preferred = connection.get("preferred_model")
    if isinstance(preferred, str) and preferred.strip():
        name = preferred.strip()
        return name if name.startswith("chatgpt/") else f"chatgpt/{name}"
    if isinstance(fallback, str) and fallback.strip():
        name = fallback.strip()
        return name if name.startswith("chatgpt/") else f"chatgpt/{name}"
    return None


def resolve_group_model(
    bot_model: str | None,
    connection: dict[str, Any] | None,
    *,
    openrouter_ready: bool,
    default_model: str,
    fallback_chatgpt_model: str | None = None,
) -> str:
    """Pick a real model for a group turn. Unprefixed models must not fall through to mock."""
    stored = (bot_model or "").strip()
    if stored.startswith("chatgpt/"):
        return stored
    chatgpt = preferred_chatgpt_model(connection, fallback_chatgpt_model)
    if chatgpt:
        return chatgpt
    if openrouter_ready:
        fallback = stored or default_model.strip()
        if fallback:
            return fallback
    raise ModelUnavailable(MODEL_UNAVAILABLE)


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
        if running_on_vercel():
            raise ModelUnavailable(MODEL_UNAVAILABLE)
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
        responses_model = CodexResponsesModel if codex else OpenAIResponsesModel
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
                model=responses_model(model_name, provider=provider),
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
            raise ModelUnavailable(MODEL_UNAVAILABLE)
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


class CodexResponsesModel:
    """Codex rejects non-streaming Responses calls, so every request is streamed."""

    def __new__(cls, model_name: str, *, provider: Any) -> Any:
        from pydantic_ai.models.openai import OpenAIResponsesModel

        model = OpenAIResponsesModel(model_name, provider=provider)
        model.request = _codex_request.__get__(model, OpenAIResponsesModel)  # type: ignore[method-assign]
        return model


async def _codex_request(self: Any, messages: list[Any], model_settings: Any, model_request_parameters: Any) -> Any:
    async with self.request_stream(messages, model_settings, model_request_parameters) as streamed:
        async for _event in streamed:
            pass
        return _with_visible_text(streamed.get())


def _with_visible_text(response: Any) -> Any:
    """Use reasoning text when Codex returns no message the agent can accept."""
    from pydantic_ai.messages import TextPart, ThinkingPart, ToolCallPart

    parts = list(response.parts)
    has_answer = any(
        isinstance(part, ToolCallPart) or (isinstance(part, TextPart) and part.content.strip())
        for part in parts
    )
    if has_answer:
        return response
    thoughts = [part.content.strip() for part in parts if isinstance(part, ThinkingPart) and part.content.strip()]
    if thoughts:
        response.parts = [*parts, TextPart("\n\n".join(thoughts))]
    return response


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

    agent = Agent(model, instructions=system, retries=0)
    for definition in tools:
        agent._function_toolset.add_tool(_tool(definition))
    try:
        result = await agent.run(
            prompt,
            model_settings=model_settings,
            usage_limits=UsageLimits(request_limit=request_limit),
        )
    except ApprovalRequired as error:
        return str(error)
    except Exception as error:
        if _find_approval(error):
            return "Butuh persetujuanmu sebelum langkah ini dijalankan."
        raise
    answer = str(result.output).strip()
    if not answer:
        raise RuntimeError("Model mengembalikan response kosong.")
    return answer


def _tool(definition: ToolDefinition) -> Any:
    from pydantic_ai.tools import Tool

    async def invoke(**payload: object) -> dict[str, object]:
        result = await definition.handler(dict(payload))
        if result.get("requires_approval"):
            raise ApprovalRequired("Butuh persetujuanmu sebelum langkah ini dijalankan.")
        return result

    tool = Tool.from_schema(
        invoke,
        name=definition.name,
        description=definition.description,
        json_schema={"type": "object", "additionalProperties": True},
    )
    tool.max_retries = 0
    tool.timeout = definition.timeout_seconds
    return tool
