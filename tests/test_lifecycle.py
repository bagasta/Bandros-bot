from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from apps.api.app.database import Database
from apps.api.app.domain import ApprovalStatus, RiskClass, RunStatus
from apps.api.app.policy import PolicyEngine
from apps.api.app.repository import Repository
from apps.api.app.runtime import RunRuntime
from apps.api.app.workspace_tools import WorkspaceToolset


class FakeGateway:
    async def complete(self, *, system: str, prompt: str, model: str, tools=()) -> str:
        return f"completed: {prompt}"


def make_repository(path: Path) -> Repository:
    database = Database(path)
    database.initialize()
    return Repository(database)


def test_run_completes_and_persists_message(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "product.db")
    bot = repository.create_bot("Research", "", "Use sources.", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    run = repository.create_run(bot.id, conversation_id, "Check project health", "test-model")
    runtime = RunRuntime(repository, FakeGateway(), "test-model", 1)

    async def execute() -> None:
        await runtime.start_and_wait(run.id)

    asyncio.run(execute())

    completed = repository.get_run(run.id)
    assert completed.status is RunStatus.COMPLETED
    assert repository.list_messages(conversation_id)[0].content == "completed: Check project health"


def test_run_prompt_includes_skill_and_shared_computer(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "product.db")
    bot = repository.create_bot("Research", "Riset sumber.", "Cek sumber terkini.", None)
    skill = repository.create_skill("research", "Riset", "Buka sumber, lalu simpan ringkasan di workspace.")
    repository.assign_skill(bot.id, skill.id)
    conversation_id = repository.conversation_for_bot(bot.id)
    run = repository.create_run(bot.id, conversation_id, "Rangkum risiko", "test-model")
    captured: dict[str, str] = {}

    class CaptureGateway(FakeGateway):
        async def complete(self, *, system: str, prompt: str, model: str, tools=()) -> str:
            captured["system"] = system
            captured["tools"] = ",".join(tool.name for tool in tools)
            return "selesai"

    runtime = RunRuntime(repository, CaptureGateway(), "test-model", 1)
    asyncio.run(runtime.start_and_wait(run.id))

    assert "shared computer" in captured["system"]
    assert "Buka sumber" in captured["system"]
    assert "save_memory" in captured["tools"]
    assert "write_workspace_file" in captured["tools"]


def test_protected_action_requires_a_durable_approval(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "product.db")
    bot = repository.create_bot("Ops", "", "", None)
    run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "Deploy", "test-model")
    repository.update_run(run.id, RunStatus.RUNNING)

    decision = PolicyEngine().decide(RiskClass.EXTERNAL_WRITE)
    assert decision.requires_approval
    approval = repository.create_approval(
        run.id, "git_push", RiskClass.EXTERNAL_WRITE, decision.reason or "", {"remote": "origin"}
    )
    repository.update_run(run.id, RunStatus.WAITING_APPROVAL)
    repository.decide_approval(approval.id, ApprovalStatus.REJECTED)

    assert repository.get_approval(approval.id).status is ApprovalStatus.REJECTED
    assert repository.get_run(run.id).status is RunStatus.WAITING_APPROVAL


def test_every_agent_has_orchestration_tools(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "product.db")
    manager = repository.create_bot("Manager", "", "", None)
    worker = repository.create_bot("Worker", "", "", None)
    skill = repository.create_skill("workspace_admin", "", "Create bots safely.")
    run = repository.create_run(manager.id, repository.conversation_for_bot(manager.id), "Organize team", "test-model")

    worker_tools = WorkspaceToolset(repository, run.id, worker.id, lambda _: None)
    assert {"handoff_to_bot", "post_to_group", "create_job"} <= {
        tool.name for tool in worker_tools.definitions()
    }

    repository.assign_skill(manager.id, skill.id)
    tools = WorkspaceToolset(repository, run.id, manager.id, lambda _: None)
    assert "create_bot" in {tool.name for tool in tools.definitions()}


def test_coordination_skill_creates_a_durable_handoff(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "product.db")
    manager = repository.create_bot("Manager", "", "", None)
    researcher = repository.create_bot("Research", "", "", None)
    skill = repository.create_skill("coordination", "", "Delegate work.")
    repository.assign_skill(manager.id, skill.id)
    run = repository.create_run(manager.id, repository.conversation_for_bot(manager.id), "Delegate", "test-model")
    started: list[object] = []
    toolset = WorkspaceToolset(repository, run.id, manager.id, started.append)
    handoff = next(tool for tool in toolset.definitions() if tool.name == "handoff_to_bot")

    result = asyncio.run(handoff.handler({"target_bot_id": str(researcher.id), "task": "Find three risks."}))

    assert result["ok"] is True
    assert len(started) == 1
    saved = repository.get_handoff(UUID(result["handoff_id"]))
    assert saved.status == "running"
    assert saved.child_run_id is not None


def test_message_metadata_and_run_events_are_persisted(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "metadata.db")
    bot = repository.create_bot("Writer", "", "", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    message = repository.append_message(
        conversation_id,
        "user",
        "Draft",
        model="test-model",
        attachments=[{"name": "brief.txt", "type": "text/plain"}],
    )
    run = repository.create_run(bot.id, conversation_id, message.content, "test-model")
    repository.record_event(run.id, "assistant.delta", {"content": "Done", "final": True})

    saved = repository.list_messages(conversation_id)[0]
    assert saved.model == "test-model"
    assert saved.attachments[0]["name"] == "brief.txt"
    assert repository.list_events(run.id)[-1].type == "assistant.delta"


def test_stop_marks_run_cancelled(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "stop.db")
    bot = repository.create_bot("Worker", "", "", None)
    run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "Stop", "test-model")
    class SlowGateway(FakeGateway):
        async def complete(self, *, system: str, prompt: str, model: str, tools=()) -> str:
            await asyncio.sleep(0.1)
            return "completed"

    runtime = RunRuntime(repository, SlowGateway(), "test-model", 1)

    async def execute() -> None:
        runtime.start(run.id)
        await asyncio.sleep(0.01)
        runtime.stop(run.id)

    asyncio.run(execute())
    assert repository.get_run(run.id).status is RunStatus.CANCELLED
    assert repository.list_events(run.id)[-1].type == "run.cancelled"


def test_bot_workspace_tools_persist_files_and_memory(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "workspace.db")
    bot = repository.create_bot("Builder", "", "", None)
    run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "Build", "test-model")
    tools = WorkspaceToolset(repository, run.id, bot.id, lambda _: None, workspace_root=tmp_path / "computer")
    definitions = {tool.name: tool for tool in tools.definitions()}

    saved = asyncio.run(definitions["write_workspace_file"].handler({"path": "notes/plan.md", "content": "Ship it"}))
    loaded = asyncio.run(definitions["read_workspace_file"].handler({"path": "notes/plan.md"}))
    memory = asyncio.run(definitions["save_memory"].handler({"kind": "preference", "content": "Use concise updates"}))
    recalled = asyncio.run(definitions["list_memory"].handler({"query": "concise"}))

    assert saved["ok"] is True
    assert loaded["content"] == "Ship it"
    assert memory["ok"] is True
    assert recalled["memories"][0]["content"] == "Use concise updates"


def test_bot_workspace_tools_reject_path_escape(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "workspace.db")
    bot = repository.create_bot("Builder", "", "", None)
    run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "Build", "test-model")
    tools = WorkspaceToolset(repository, run.id, bot.id, lambda _: None, workspace_root=tmp_path / "computer")
    write = next(tool for tool in tools.definitions() if tool.name == "write_workspace_file")

    result = asyncio.run(write.handler({"path": "../outside.txt", "content": "nope"}))

    assert result["ok"] is False
    assert "inside" in result["error"]


def test_oauth_transaction_is_persistent_and_one_time(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "oauth.db")
    repository.create_oauth_transaction(
        "state",
        "verifier",
        "nonce",
        "oaiapp_test",
        "urn:uuid:test",
        datetime.now(UTC) + timedelta(minutes=10),
    )

    transaction = repository.consume_oauth_transaction("state")

    assert transaction is not None
    assert transaction["code_verifier"] == "verifier"
    assert repository.consume_oauth_transaction("state") is None
