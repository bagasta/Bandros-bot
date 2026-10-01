from __future__ import annotations

import asyncio

from apps.api.app.model_gateway import ChatGPTGateway


class FakeStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        yield 'data: {"type":"response.output_text.delta","delta":"Jawaban "}'
        yield 'data: {"type":"response.output_text.delta","delta":"model"}'
        yield 'data: {"type":"response.completed","response":{}}'


class FakeClient:
    last_call: dict[str, object] = {}

    def __init__(self, **_):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def stream(self, method: str, url: str, **kwargs):
        FakeClient.last_call = {"method": method, "url": url, **kwargs}
        return FakeStream()


def test_chatgpt_gateway_consumes_responses_stream(monkeypatch) -> None:
    monkeypatch.setattr("apps.api.app.model_gateway.httpx.AsyncClient", FakeClient)
    gateway = ChatGPTGateway(
        lambda: {
            "access_token": "oauth-token",
            "scope": "openid chatgpt.tokens.use.direct",
        }
    )

    answer = asyncio.run(
        gateway.complete(
            system="Jawab jelas.",
            prompt="Uji",
            model="chatgpt/gpt-test",
        )
    )

    assert answer == "Jawaban model"
    assert FakeClient.last_call["url"] == "https://api.openai.com/v1/responses"


def test_chatgpt_gateway_uses_codex_endpoint(monkeypatch) -> None:
    monkeypatch.setattr("apps.api.app.model_gateway.httpx.AsyncClient", FakeClient)
    gateway = ChatGPTGateway(
        lambda: {
            "auth_mode": "codex",
            "access_token": "oauth-token",
            "account_id": "acct_test",
        }
    )

    answer = asyncio.run(
        gateway.complete(
            system="Jawab jelas.",
            prompt="Uji",
            model="chatgpt/gpt-5.3-codex",
        )
    )

    assert answer == "Jawaban model"
    assert FakeClient.last_call["url"] == "https://chatgpt.com/backend-api/codex/responses"
    headers = FakeClient.last_call["headers"]
    assert headers["ChatGPT-Account-Id"] == "acct_test"
    assert headers["originator"] == "codex_cli_rs"


def test_chatgpt_gateway_consumes_responses_stream(monkeypatch) -> None:
    monkeypatch.setattr("apps.api.app.model_gateway.httpx.AsyncClient", FakeClient)
    gateway = ChatGPTGateway(
        lambda: {
            "access_token": "oauth-token",
            "scope": "openid chatgpt.tokens.use.direct",
        }
    )

    answer = asyncio.run(
        gateway.complete(
            system="Jawab jelas.",
            prompt="Uji",
            model="chatgpt/gpt-test",
        )
    )

    assert answer == "Jawaban model"
