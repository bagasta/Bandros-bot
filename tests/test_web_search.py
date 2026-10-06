from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
from fastapi.testclient import TestClient

from apps.api.app import main
from apps.api.app.database import Database
from apps.api.app.domain import RunStatus
from apps.api.app.openrouter_search import citations_from_payload, openrouter_web_search, search_body, search_model
from apps.api.app.repository import Repository
from apps.api.app.runtime import RunRuntime, jakarta_context
from apps.api.app.tenancy import tenant_locations
from apps.api.app.workspace_tools import WorkspaceToolset, visible_page_text


WIKIPEDIA = """
<html><body>
<nav id="mw-panel"><ul><li>Main page</li><li>Contents</li><li>Random article</li></ul></nav>
<nav id="mw-panel-toc" class="vector-toc" aria-label="Contents">
  <div>Contents</div><ul><li>1 History</li><li>2 Economy</li></ul>
</nav>
<div id="mw-content-text"><div class="mw-parser-output">
  <div id="toc" class="toc"><div>Contents</div><ul><li>1 History</li><li>2 Economy</li></ul></div>
  <p>Jakarta is the capital of Indonesia.</p>
  <p>The city sits on the northwest coast of Java.</p>
</div></div>
<footer>Privacy policy</footer>
</body></html>
"""


def make_repository(path: Path) -> Repository:
    database = Database(path)
    database.initialize()
    return Repository(database)


def test_wikipedia_fetch_returns_the_article_not_the_chrome() -> None:
    text = visible_page_text(WIKIPEDIA)

    assert "Jakarta is the capital of Indonesia." in text
    assert "northwest coast of Java" in text
    assert "Random article" not in text
    assert "Privacy policy" not in text
    assert "1 History" not in text
    assert text.startswith("Jakarta is the capital")


def test_plain_page_text_still_keeps_the_body() -> None:
    html = "<html><body><script>Contents navigation</script><p>Hello from a simple page.</p></body></html>"

    assert visible_page_text(html) == "Hello from a simple page."


def test_fetch_url_returns_article_text(tmp_path: Path, monkeypatch) -> None:
    repository = make_repository(tmp_path / "fetch.db")
    bot = repository.create_bot("Riset", "Riset", "TOKEN:riset", None)
    run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "baca", "test-model")

    class Response:
        status_code = 200
        text = WIKIPEDIA

    class Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, url: str) -> Response:
            return Response()

    async def allow(_url: str) -> bool:
        return True

    monkeypatch.setattr("apps.api.app.workspace_tools.httpx.AsyncClient", Client)
    monkeypatch.setattr("apps.api.app.workspace_tools.public_https_url", allow)
    toolset = WorkspaceToolset(repository, run.id, bot.id, lambda _: None)
    result = asyncio.run(toolset.fetch_url({"url": "https://en.wikipedia.org/wiki/Jakarta"}))

    assert result["ok"] is True
    assert "Jakarta is the capital of Indonesia." in result["text"]
    assert "Random article" not in result["text"]


def test_search_request_uses_openrouter_web_search() -> None:
    body = search_body("USD IDR hari ini", "openrouter/free")

    assert body["tools"] == [
        {
            "type": "openrouter:web_search",
            "parameters": {
                "engine": "auto",
                "max_results": 5,
                "user_location": {
                    "type": "approximate",
                    "city": "Jakarta",
                    "country": "ID",
                    "timezone": "Asia/Jakarta",
                },
            },
        }
    ]
    assert "duckduckgo" not in json.dumps(body)
    assert search_model("chatgpt/gpt-5", "openrouter/free") == "openrouter/free"
    assert search_model("openai/gpt-4.1-mini", "openrouter/free") == "openai/gpt-4.1-mini"


def test_citations_keep_https_sources_from_openrouter() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": "Lihat [BI](https://example.com/bi) dan [BI](https://example.com/bi).",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url_citation": {
                                "url": "https://example.com/fx",
                                "title": "Kurs tengah",
                                "content": "USD/IDR 16.200",
                            },
                        },
                        {"type": "url_citation", "url_citation": {"url": "http://insecure.example/x", "title": "skip"}},
                    ],
                }
            }
        ]
    }

    assert citations_from_payload(payload) == [
        {"title": "Kurs tengah", "url": "https://example.com/fx", "snippet": "USD/IDR 16.200"},
        {"title": "BI", "url": "https://example.com/bi", "snippet": ""},
    ]


def test_openrouter_web_search_posts_to_the_api() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url_citation": {
                                        "url": "https://example.com/news",
                                        "title": "Berita",
                                        "content": "Hari ini",
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    async def search() -> dict[str, object]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await openrouter_web_search(
                "berita hari ini",
                api_key="test-key",
                base_url="https://openrouter.test/api/v1",
                model="openrouter/free",
                client=client,
            )

    result = asyncio.run(search())

    assert seen["url"] == "https://openrouter.test/api/v1/chat/completions"
    assert "duckduckgo" not in str(seen["url"])
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["tools"][0]["type"] == "openrouter:web_search"
    assert result["ok"] is True
    assert result["results"] == [{"title": "Berita", "url": "https://example.com/news", "snippet": "Hari ini"}]


def test_web_search_tool_returns_sources(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.test/api/v1")
    monkeypatch.setenv("MODEL_GATEWAY", "openrouter")
    called: dict[str, str] = {}

    async def fake_search(query: str, *, api_key: str, base_url: str, model: str, client: object) -> dict[str, object]:
        called["query"] = query
        called["api_key"] = api_key
        called["base_url"] = base_url
        called["model"] = model
        return {"ok": True, "results": [{"title": "Kurs", "url": "https://example.com/idr", "snippet": "USD/IDR"}]}

    monkeypatch.setattr("apps.api.app.workspace_tools.openrouter_web_search", fake_search)
    repository = make_repository(tmp_path / "search.db")
    bot = repository.create_bot("FX", "Kurs", "TOKEN:fx", None)
    run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "kurs", "openai/gpt-test")
    toolset = WorkspaceToolset(repository, run.id, bot.id, lambda _: None, default_model="openrouter/free")

    result = asyncio.run(toolset.web_search({"query": "USD IDR hari ini"}))

    assert result["ok"] is True
    assert result["results"][0]["url"] == "https://example.com/idr"
    assert called == {
        "query": "USD IDR hari ini",
        "api_key": "test-key",
        "base_url": "https://openrouter.test/api/v1",
        "model": "openai/gpt-test",
    }
    assert toolset.sources == [{"title": "Kurs", "url": "https://example.com/idr", "snippet": "USD/IDR"}]


def test_system_prompt_includes_jakarta_clock() -> None:
    moment = datetime(2026, 10, 6, 4, 22, tzinfo=UTC)
    prompt = RunRuntime._system_prompt("Kerjakan riset.", "Riset FX", when=moment)

    clock = (
        f"Current date and time: {jakarta_context(moment)}. "
        "Use this as today for news, exchange rates, and any other time-sensitive answer."
    )
    assert jakarta_context(moment) == "Tuesday, 6 October 2026, 11:22 WIB (Asia/Jakarta)"
    assert prompt.startswith("You are a persistent named teammate")
    assert prompt.endswith(clock)
    assert prompt.index("Active skills:") < prompt.index("Current date and time:")
    assert "May 2025" not in prompt


def test_search_sources_are_saved_and_the_running_tool_is_visible(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "sources.db")
    bot = repository.create_bot("FX", "Kurs", "TOKEN:fx", None)
    group = repository.create_group("Tim", "", [bot.id])
    conversation_id = repository.conversation_for_bot(bot.id)
    run = repository.create_run(bot.id, conversation_id, "kurs", "test-model")
    repository.update_run(run.id, RunStatus.RUNNING, expect=RunStatus.QUEUED)
    repository.link_run_to_group(run.id, group.id)
    source = {"title": "BI", "url": "https://example.com/bi", "snippet": "USD/IDR"}
    call_id = repository.create_tool_call(run.id, "web_search", {"query": "USD IDR"})

    assert repository.running_tool_name(run.id) == "web_search"
    assert repository.commit_assistant_turn(run.id, "Kurs hari ini.", "test-model", [source]) is True
    assert repository.list_messages(conversation_id)[-1].citations == [source]
    posted = repository.append_group_message(group.id, "bot", "Kurs hari ini.", bot.id, [source])
    assert repository.list_group_messages(group.id)[-1].citations == [source]
    assert posted.citations == [source]
    repository.complete_tool_call(call_id, {"ok": True, "results": [source]}, True)
    assert repository.running_tool_name(run.id) is None


def test_direct_chat_api_returns_citations(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL_BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.setattr(
        main,
        "settings",
        replace(
            main.settings,
            database_path=tmp_path / "workspace.db",
            workspace_root=tmp_path / "workspace",
            await_runs=True,
        ),
    )
    main._workspaces.clear()
    asyncio.run(
        main.credential_store.put(
            "sessions",
            "token-dm",
            {"auth_mode": "codex", "account_id": "acct_dm", "access_token": "a"},
        )
    )
    headers = {"X-Bandros-Session": "token-dm"}
    source = {"title": "BI", "url": "https://example.com/bi", "snippet": "USD/IDR"}

    with TestClient(main.app) as client:
        listed = client.get("/api/v1/bots", headers=headers)
        bot_id = next(bot["id"] for bot in listed.json() if bot["name"] == "Bandros")
        database_path, _, _ = tenant_locations("acct_dm", tmp_path, tmp_path / "workspace")
        repository = Repository(Database(database_path))
        conversation_id = repository.conversation_for_bot(UUID(bot_id))
        run = repository.create_run(UUID(bot_id), conversation_id, "kurs", "test-model")
        repository.update_run(run.id, RunStatus.RUNNING, expect=RunStatus.QUEUED)
        assert repository.commit_assistant_turn(run.id, "Kurs hari ini.", "test-model", [source]) is True
        direct = client.get(f"/api/v1/bots/{bot_id}/messages", headers=headers)
        enveloped = client.get(
            f"/api/v1/bots/{bot_id}/messages",
            headers={**headers, "X-Bandros-Envelope": "1"},
        )
    main._workspaces.clear()

    assert direct.status_code == 200
    assert _assistant_citations(direct.json()) == [source]
    assert enveloped.status_code == 200
    payload = enveloped.json()
    messages = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
    assert _assistant_citations(messages) == [source]


def _assistant_citations(messages: list[dict[str, object]]) -> list[object]:
    assistant = next(item for item in messages if item["role"] == "assistant")
    citations = assistant["citations"]
    assert isinstance(citations, list)
    return citations
