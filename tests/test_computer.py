from __future__ import annotations

import asyncio
import json

import httpx

from apps.api.app.computer import SNAPSHOT, ComputerError, DaytonaComputer
from apps.api.app.database import Database
from apps.api.app.repository import Repository
from apps.api.app.runtime import RunRuntime
from apps.api.app.workspace_tools import WorkspaceToolset


class FakeGateway:
    async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
        return "done"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_reuses_small_sandbox_and_archives_without_desktop() -> None:
    state = {"value": "stopped"}
    created: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/sandbox":
            created.append(json.loads(request.content))
            return httpx.Response(500, json={"error": "should reuse"})
        if request.method == "GET" and path == "/sandbox":
            return httpx.Response(200, json={"items": [{"id": "sb", "state": state["value"], "labels": {"app": "bandros", "account": "acct"}}]})
        if path == "/sandbox/sb/start":
            state["value"] = "started"
            return httpx.Response(200, json={"id": "sb", "state": "started"})
        if request.method == "GET" and path == "/sandbox/sb":
            return httpx.Response(200, json={"id": "sb", "state": state["value"], "toolboxProxyUrl": "https://proxy.test"})
        if path == "/sandbox/sb/stop":
            state["value"] = "stopped"
            return httpx.Response(200, json={"state": "stopped"})
        if path == "/sandbox/sb/archive":
            state["value"] = "archived"
            return httpx.Response(200, json={"state": "archived"})
        if path == "/sb/files/folder":
            return httpx.Response(201, text="")
        if path == "/sb/process/execute":
            assert b"computeruse" not in request.content
            return httpx.Response(200, json={"exitCode": 0, "result": "ok"})
        return httpx.Response(404, json={"path": path})

    computer = DaytonaComputer("test-key", "https://api.test", "acct", client=_client(handler), sleep=lambda _: None)
    assert computer.run("pwd")["output"] == "ok"
    computer.park()
    assert created == []
    assert state["value"] == "archived"


def test_create_uses_smallest_snapshot() -> None:
    body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/sandbox":
            return httpx.Response(200, json={"items": []})
        if request.method == "POST" and request.url.path == "/sandbox":
            body.update(json.loads(request.content))
            return httpx.Response(200, json={"id": "new", "state": "started", "toolboxProxyUrl": "https://proxy.test"})
        if request.url.path == "/new/files/folder":
            return httpx.Response(201, text="")
        if request.url.path == "/new/files":
            return httpx.Response(200, json=[])
        return httpx.Response(404, text=request.url.path)

    computer = DaytonaComputer("test-key", "https://api.test", "acct", client=_client(handler), sleep=lambda _: None)
    assert computer.list_dir(".") == []
    assert body["snapshot"] == SNAPSHOT == "daytona-small"
    assert body["autoStopInterval"] == 5
    assert body["autoArchiveInterval"] == 60
    assert "gpu" not in body
    assert body["public"] is False


def test_archives_duplicate_account_sandboxes_before_use() -> None:
    actions: list[str] = []
    states = {"first": "started", "second": "started"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/sandbox":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": "first", "state": states["first"], "labels": {"app": "bandros", "account": "acct"}},
                        {"id": "second", "state": states["second"], "labels": {"app": "bandros", "account": "acct"}},
                    ]
                },
            )
        if path == "/sandbox/second/stop":
            actions.append("stop second")
            states["second"] = "stopped"
            return httpx.Response(200, json={"state": "stopped"})
        if request.method == "GET" and path == "/sandbox/second":
            return httpx.Response(200, json={"id": "second", "state": states["second"]})
        if path == "/sandbox/second/archive":
            actions.append("archive second")
            states["second"] = "archived"
            return httpx.Response(200, json={"state": "archived"})
        if request.method == "GET" and path == "/sandbox/first":
            return httpx.Response(
                200,
                json={"id": "first", "state": "started", "toolboxProxyUrl": "https://proxy.test"},
            )
        if path == "/first/files/folder":
            actions.append("use first")
            return httpx.Response(201, text="")
        if path == "/first/process/execute":
            return httpx.Response(200, json={"exitCode": 0, "result": ""})
        return httpx.Response(404, text=path)

    computer = DaytonaComputer("test-key", "https://api.test", "acct", client=_client(handler), sleep=lambda _: None)
    assert computer.run("pwd")["output"] == ""
    assert actions == ["stop second", "archive second", "use first"]


def test_wake_returns_preview_without_installing_chrome() -> None:
    commands: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/sandbox":
            return httpx.Response(
                200,
                json={"items": [{"id": "sb", "state": "started", "labels": {"app": "bandros", "account": "acct"}, "toolboxProxyUrl": "https://proxy.test"}]},
            )
        if path == "/sb/computeruse/start":
            return httpx.Response(200, json={"state": "started"})
        if path.endswith("/signed-preview-url"):
            return httpx.Response(200, json={"url": "https://6080-example.daytonaproxy01.net/vnc.html?token=secret"})
        if path == "/sb/process/execute":
            commands.append(json.loads(request.content)["command"])
            return httpx.Response(200, json={"exitCode": 0, "result": ""})
        return httpx.Response(404, text=path)

    computer = DaytonaComputer("test-key", "https://api.test", "acct", client=_client(handler), sleep=lambda _: None)
    status = computer.wake()

    assert status == {"state": "on", "screen_url": "https://6080-example.daytonaproxy01.net"}
    assert commands == []
    assert computer.status()["screen_url"] == "https://6080-example.daytonaproxy01.net"


def test_preview_origin_is_only_the_desktop_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/signed-preview-url"):
            return httpx.Response(200, json={"url": "https://6080-example.daytonaproxy01.net/vnc.html"})
        return httpx.Response(404, text=request.url.path)

    computer = DaytonaComputer("test-key", "https://api.test", "acct", client=_client(handler), sleep=lambda _: None)
    assert computer._preview_origin("sb") == "https://6080-example.daytonaproxy01.net"


def test_rejects_paths_outside_workspace() -> None:
    computer = DaytonaComputer("test-key", "https://api.test", "acct", client=_client(lambda request: httpx.Response(500)))
    try:
        computer.read_text("../secret")
    except ComputerError as error:
        assert "workspace" in str(error)
    else:
        raise AssertionError("path escaped")


def test_runtime_parks_after_the_turn(tmp_path) -> None:
    class Parking:
        def __init__(self) -> None:
            self.parked = False

        def park(self) -> None:
            self.parked = True

    database = Database(tmp_path / "park.db")
    database.initialize()
    repository = Repository(database)
    bot = repository.create_bot("Worker", "", "", None)
    run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "Hi", "test-model")
    computer = Parking()
    runtime = RunRuntime(repository, FakeGateway(), "test-model", 1, computer=computer)
    asyncio.run(runtime.start_and_wait(run.id))
    assert computer.parked is False


def test_runtime_wakes_and_parks_only_when_a_computer_tool_is_used(tmp_path) -> None:
    class Desktop:
        def __init__(self) -> None:
            self.wakes = 0
            self.parked = 0

        def wake(self) -> None:
            self.wakes += 1

        def park(self) -> None:
            self.parked += 1

        def run(self, command: str) -> dict[str, object]:
            return {"exit_code": 0, "output": command}

    class ComputerGateway(FakeGateway):
        async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
            tool = next(item for item in tools if item.name == "run_command")
            result = await tool.handler({"command": "pwd"})
            assert result["ok"] is True
            return "done"

    database = Database(tmp_path / "lazy.db")
    database.initialize()
    repository = Repository(database)
    bot = repository.create_bot("Worker", "", "", None)
    run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "Hi", "test-model")
    computer = Desktop()
    runtime = RunRuntime(repository, ComputerGateway(), "test-model", 1, computer=computer)

    asyncio.run(runtime.start_and_wait(run.id))

    assert computer.wakes == 1
    assert computer.parked == 1


def test_held_computer_stays_awake(tmp_path) -> None:
    class Parking:
        def __init__(self) -> None:
            self.parked = False

        def park(self) -> None:
            self.parked = True

    database = Database(tmp_path / "held.db")
    database.initialize()
    repository = Repository(database)
    bot = repository.create_bot("Worker", "", "", None)
    run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "Hi", "test-model")
    computer = Parking()
    runtime = RunRuntime(repository, FakeGateway(), "test-model", 1, computer=computer)
    runtime._computer_held = True
    asyncio.run(runtime.start_and_wait(run.id))
    assert computer.parked is False


def test_workspace_files_go_through_the_computer(tmp_path) -> None:
    class Files:
        def __init__(self) -> None:
            self.files: dict[str, str] = {}

        def list_dir(self, path: str) -> list[dict[str, str]]:
            return [{"path": name, "type": "file"} for name in self.files]

        def read_text(self, path: str) -> str:
            return self.files[path]

        def write_text(self, path: str, content: str) -> None:
            self.files[path] = content

        def run(self, command: str) -> dict[str, object]:
            return {"exit_code": 0, "output": command}

    database = Database(tmp_path / "files.db")
    database.initialize()
    repository = Repository(database)
    bot = repository.create_bot("Worker", "", "", None)
    run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "Hi", "test-model")
    computer = Files()
    tools = WorkspaceToolset(repository, run.id, bot.id, lambda _: None, computer=computer)
    definitions = {tool.name: tool for tool in tools.definitions()}
    saved = asyncio.run(definitions["write_workspace_file"].handler({"path": "notes/plan.md", "content": "Ship it"}))
    loaded = asyncio.run(definitions["read_workspace_file"].handler({"path": "notes/plan.md"}))
    ran = asyncio.run(definitions["run_command"].handler({"command": "ls"}))
    assert saved["ok"] is True
    assert loaded["content"] == "Ship it"
    assert ran["output"] == "ls"
