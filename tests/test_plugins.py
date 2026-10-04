from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from apps.api.app.database import Database
from apps.api.app.mcp_client import McpError, discover_tools
from apps.api.app.repository import Repository
from apps.api.app.workspace_tools import WorkspaceToolset


def repository(path: Path) -> Repository:
    database = Database(path)
    database.initialize()
    return Repository(database)


class FakeResponse:
    def __init__(self, payload: dict, headers: dict[str, str] | None = None, text: str | None = None, status: int = 200) -> None:
        self.status_code = status
        self.headers = headers or {"content-type": "application/json"}
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> FakeResponse:
        return self.responses[json["method"]]


class WeatherClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def __aenter__(self) -> "WeatherClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.urls.append(url)
        if "geocoding" in url:
            return FakeResponse({"results": [{"name": "Jakarta", "country": "Indonesia", "latitude": -6.2, "longitude": 106.8}]})
        return FakeResponse({
            "timezone": "Asia/Jakarta",
            "current": {"temperature_2m": 30.1, "weather_code": 1},
            "daily": {
                "weather_code": [1],
                "temperature_2m_max": [32.0],
                "temperature_2m_min": [26.0],
                "precipitation_probability_max": [20],
            },
        })


def test_mcp_server_tools_are_discovered(monkeypatch, tmp_path: Path) -> None:
    async def allow(url: str) -> bool:
        return True

    monkeypatch.setattr("apps.api.app.mcp_client.public_https_url", allow)
    client = FakeClient({
        "initialize": FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {}}, headers={"content-type": "application/json", "mcp-session-id": "sess"}),
        "tools/list": FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "search", "description": "Find records"}]}}),
    })
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)

    tools = asyncio.run(discover_tools("https://example.com/mcp", "secret"))

    assert tools == [{"name": "search", "description": "Find records"}]
    saved = repository(tmp_path / "plugins.db").create_plugin("Records", "https://example.com/mcp", "secret", tools)
    assert saved.tools == ["search"]
    assert "token" not in saved.model_dump()


def test_mcp_event_stream_is_read(monkeypatch) -> None:
    async def allow(url: str) -> bool:
        return True

    monkeypatch.setattr("apps.api.app.mcp_client.public_https_url", allow)
    stream = "event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"tools\":[{\"name\":\"list_issues\",\"description\":\"Issues\"}]}}\n\n"
    client = FakeClient({
        "initialize": FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {}}),
        "tools/list": FakeResponse({}, headers={"content-type": "text/event-stream"}, text=stream),
    })
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: client)

    tools = asyncio.run(discover_tools("https://example.com/mcp", None))

    assert tools[0]["name"] == "list_issues"


def test_bandros_can_install_an_mcp_from_chat(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    from apps.api.app.workspace_tools import WorkspaceToolset

    async def fake_discover(url: str, token: str | None) -> list[dict[str, str]]:
        assert url == "https://aisenseapi.com/mcp"
        assert token is None
        return [{"name": "get_current_time", "description": "Current time"}]

    monkeypatch.setattr("apps.api.app.mcp_client.discover_tools", fake_discover)
    repo = repository(tmp_path / "chat.db")
    bot = repo.create_bot("Bandros", "Orkestrator utama.", "Atur tim.", None)
    run = repo.create_run(bot.id, repo.conversation_for_bot(bot.id), "Pasang MCP AI Sense", "test")
    toolset = WorkspaceToolset(repo, run.id, bot.id, lambda _run: None)
    tool = next(item for item in toolset.definitions() if item.name == "connect_plugin")

    result = asyncio.run(tool.handler({"name": "AI Sense", "url": "https://aisenseapi.com/mcp"}))

    assert result["ok"] is True
    assert result["tools"] == ["get_current_time"]
    assert repo.list_plugins()[0].name == "AI Sense"


def test_clawhub_skill_markdown_becomes_a_bandros_skill() -> None:
    from apps.api.app.clawhub import parse_skill_markdown

    name, description, content = parse_skill_markdown("---\nname: demo-skill\ndescription: Keep notes short.\n---\n\nWrite the result first.\n")
    assert name == "demo-skill"
    assert description == "Keep notes short."
    assert content == "Write the result first."


def test_private_plugin_url_is_rejected() -> None:
    with pytest.raises(McpError):
        asyncio.run(discover_tools("http://127.0.0.1/mcp", None))


def test_installed_skill_is_callable_by_the_assigned_bot(tmp_path: Path) -> None:
    repo = repository(tmp_path / "skill.db")
    bot = repo.create_bot("WeatherBot", "Weather helper.", "Use tools.", None)
    skill = repo.create_skill("Weather", "Current weather", "Use the public weather endpoint.")
    repo.assign_skill(bot.id, skill.id)
    run = repo.create_run(bot.id, repo.conversation_for_bot(bot.id), "Weather", "test")
    tool = next(item for item in WorkspaceToolset(repo, run.id, bot.id, lambda _: None).definitions() if item.name == "use_skill")

    result = asyncio.run(tool.handler({"skill": "weather"}))

    assert result == {
        "ok": True,
        "name": "Weather",
        "description": "Current weather",
        "instructions": "Use the public weather endpoint.",
    }


def test_weather_skill_fetches_a_forecast_from_its_allowlisted_service(
    tmp_path: Path, monkeypatch
) -> None:
    client = WeatherClient()
    monkeypatch.setattr("apps.api.app.weather.httpx.AsyncClient", lambda *args, **kwargs: client)
    repo = repository(tmp_path / "weather.db")
    bot = repo.create_bot("WeatherBot", "Weather helper.", "Use weather tools.", None)
    skill = repo.create_skill("Weather", "Current weather", "Use the weather forecast tool.")
    repo.assign_skill(bot.id, skill.id)
    run = repo.create_run(bot.id, repo.conversation_for_bot(bot.id), "Forecast Jakarta", "test")
    tool = next(
        item for item in WorkspaceToolset(repo, run.id, bot.id, lambda _: None).definitions()
        if item.name == "weather_forecast"
    )

    result = asyncio.run(tool.handler({"location": "Jakarta"}))

    assert result["ok"] is True
    assert result["location"]["name"] == "Jakarta"
    assert result["today"]["temperature_2m_max"] == 32.0
    assert client.urls == [
        "https://geocoding-api.open-meteo.com/v1/search",
        "https://api.open-meteo.com/v1/forecast",
    ]
