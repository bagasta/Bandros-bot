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
    def __init__(self, **_):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def stream(self, *_args, **_kwargs):
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
