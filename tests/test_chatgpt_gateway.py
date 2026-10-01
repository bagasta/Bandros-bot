from __future__ import annotations

import asyncio

from apps.api.app.model_gateway import ChatGPTGateway, _codex_request, run_pydantic_agent
from apps.api.app.workspace_tools import ToolDefinition


def test_chatgpt_gateway_runs_codex_tools_through_pydantic(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        definition = kwargs["tools"][0]
        observed = await definition.handler({"kind": "preference", "content": "singkat"})
        captured["tool_result"] = observed
        return "Jawaban model"

    monkeypatch.setattr("apps.api.app.model_gateway.run_pydantic_agent", fake_run)
    gateway = ChatGPTGateway(
        lambda: {
            "auth_mode": "codex",
            "access_token": "oauth-token",
            "account_id": "acct_test",
        }
    )

    async def save_memory(payload: dict[str, object]) -> dict[str, object]:
        return {"ok": True, **payload}

    answer = asyncio.run(
        gateway.complete(
            system="Jawab jelas.",
            prompt="Ingat preferensi saya.",
            model="chatgpt/gpt-5.3-codex",
            tools=(
                ToolDefinition(
                    "save_memory",
                    "Save a durable preference.",
                    save_memory,
                ),
            ),
        )
    )

    assert answer == "Jawaban model"
    assert captured["tool_result"] == {
        "ok": True,
        "kind": "preference",
        "content": "singkat",
    }
    settings = captured["model_settings"]
    assert settings["openai_store"] is False
    assert settings["extra_headers"]["ChatGPT-Account-Id"] == "acct_test"
    assert settings["extra_headers"]["originator"] == "codex_cli_rs"
    assert captured["request_limit"] == 8
    assert captured["model"].request.__func__ is _codex_request


def test_codex_request_streams_until_the_response_is_complete() -> None:
    class Event:
        def __init__(self, event_type: str, response: object) -> None:
            self.type = event_type
            self.response = response

    class Stream:
        def __init__(self) -> None:
            self.events = [Event("response.created", None), Event("response.completed", {"id": "resp_1"})]
            self.entered = False

        async def __aenter__(self):
            self.entered = True
            return self

        async def __aexit__(self, *_args):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.events:
                raise StopAsyncIteration
            return self.events.pop(0)

    captured: dict[str, object] = {}
    stream = Stream()

    class Model:
        def prepare_request(self, model_settings, model_request_parameters):
            return model_settings, model_request_parameters

        async def _responses_create(self, messages, streaming, model_settings, model_request_parameters):
            captured["stream"] = streaming
            return stream

        def _process_response(self, response, model_settings, model_request_parameters):
            captured["response"] = response
            return "processed"

    answer = asyncio.run(_codex_request(Model(), [], None, None))

    assert answer == "processed"
    assert captured["stream"] is True
    assert captured["response"] == {"id": "resp_1"}
    assert stream.entered is True


def test_pydantic_agent_returns_model_text(monkeypatch) -> None:
    class FakeResult:
        output = "  Siap.  "

    class FakeAgent:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def tool_plain(self, *_args, **_kwargs) -> None:
            return None

        async def run(self, prompt: str, **_kwargs) -> FakeResult:
            assert prompt == "Uji"
            return FakeResult()

    monkeypatch.setattr("pydantic_ai.Agent", FakeAgent)
    answer = asyncio.run(
        run_pydantic_agent(
            model=object(),
            system="Sistem",
            prompt="Uji",
            tools=(),
        )
    )

    assert answer == "Siap."
