from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from apps.api.app.database import Database
from apps.api.app.model_gateway import (
    MODEL_UNAVAILABLE,
    MockGateway,
    ModelUnavailable,
    resolve_group_model,
)
from apps.api.app.repository import Repository
from apps.api.app.runtime import RunRuntime


def test_group_model_prefers_the_chatgpt_account_model() -> None:
    model = resolve_group_model(
        "openrouter/free",
        {"auth_mode": "codex", "access_token": "token", "preferred_model": "gpt-6-luna"},
        openrouter_ready=False,
        default_model="openrouter/free",
        fallback_chatgpt_model="gpt-6-luna",
    )
    assert model == "chatgpt/gpt-6-luna"


def test_group_model_keeps_an_explicit_chatgpt_bot_model() -> None:
    model = resolve_group_model(
        "chatgpt/gpt-5.4",
        {"auth_mode": "codex", "access_token": "token", "preferred_model": "gpt-6-luna"},
        openrouter_ready=True,
        default_model="openrouter/free",
    )
    assert model == "chatgpt/gpt-5.4"


def test_group_model_without_a_usable_model_is_a_short_error() -> None:
    prompt = "Riwayat percakapan:\nBot instructions:\nsecret system prompt"
    with pytest.raises(ModelUnavailable) as caught:
        resolve_group_model(None, None, openrouter_ready=False, default_model="openrouter/free")
    assert str(caught.value) == MODEL_UNAVAILABLE
    assert prompt not in str(caught.value)
    assert "Mock response for:" not in str(caught.value)


def test_mock_gateway_refuses_on_vercel(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    prompt = "system instructions and the whole conversation"

    async def call() -> str:
        return await MockGateway().complete(system="secret", prompt=prompt, model="openrouter/free")

    with pytest.raises(ModelUnavailable) as caught:
        asyncio.run(call())
    assert str(caught.value) == MODEL_UNAVAILABLE
    assert prompt not in str(caught.value)
    assert "Mock response for:" not in str(caught.value)


def test_group_turn_uses_the_resolver_model(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "group.db")
    worker = repository.create_bot("IT Aplikasi", "", "TOKEN:worker", "openrouter/free")
    group = repository.create_group("Divisi IT", "", [worker.id])
    text = "@everyone cek status"
    repository.append_group_message(group.id, "user", text)

    class RecordingGateway:
        def __init__(self) -> None:
            self.models: list[str] = []

        async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
            self.models.append(model)
            return "Siap."

    gateway = RecordingGateway()
    runtime = RunRuntime(
        repository,
        gateway,
        "openrouter/free",
        3,
        resolve_group_model=lambda bot: "chatgpt/gpt-6-luna",
    )
    asyncio.run(runtime.speak_in_group(group.id, text, None, 0))

    assert gateway.models == ["chatgpt/gpt-6-luna"]
    posted = [message.content for message in repository.list_group_messages(group.id) if message.sender_type == "bot"]
    assert posted == ["Siap."]
    assert all("Mock response for:" not in message for message in posted)


def test_group_turn_without_a_model_does_not_echo_the_prompt(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "missing.db")
    worker = repository.create_bot("IT Aplikasi", "", "TOKEN:worker", None)
    group = repository.create_group("Divisi IT", "", [worker.id])
    text = "@everyone rahasia-prompt-pengguna"
    repository.append_group_message(group.id, "user", text)

    class NeverGateway:
        async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
            raise AssertionError(prompt)

    def unavailable(bot: object) -> str:
        raise ModelUnavailable(MODEL_UNAVAILABLE)

    runtime = RunRuntime(repository, NeverGateway(), "openrouter/free", 3, resolve_group_model=unavailable)
    asyncio.run(runtime.speak_in_group(group.id, text, None, 0))

    posted = [message.content for message in repository.list_group_messages(group.id) if message.sender_type == "bot"]
    assert posted == [MODEL_UNAVAILABLE]
    assert "rahasia-prompt-pengguna" not in posted[0]
    assert "Mock response for:" not in posted[0]


def _repository(path: Path) -> Repository:
    database = Database(path)
    database.initialize()
    return Repository(database)
