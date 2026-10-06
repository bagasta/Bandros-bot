"""Shared-db lease: another instance must not call a live run interrupted."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.app import main
from apps.api.app.database import Database
from apps.api.app.domain import RunStatus
from apps.api.app.repository import ORPHAN_NOTICE, Repository
from apps.api.app.runtime import RunRuntime
from apps.api.app.tenancy import tenant_locations


def make_repository(path: Path) -> Repository:
    database = Database(path)
    database.initialize()
    return Repository(database)


def _backdate(repository: Repository, run_id) -> None:
    stale = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    with repository.database.connection() as db:
        db.execute(
            "UPDATE runs SET heartbeat_at = ?, started_at = ?, created_at = ? WHERE id = ?",
            (stale, stale, stale, str(run_id)),
        )


def test_fresh_database_run_survives_another_instance(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "lease.db")
    bot = repository.create_bot("Bandros", "", "", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    run = repository.create_run(bot.id, conversation_id, "lanjut", "test-model")
    repository.update_run(run.id, RunStatus.RUNNING)

    stranger = RunRuntime(repository, None, "test-model", 1)  # type: ignore[arg-type]
    assert stranger.recover(resume=False) == 0
    assert repository.get_run(run.id).status is RunStatus.RUNNING
    assert all(message.content != ORPHAN_NOTICE for message in repository.list_messages(conversation_id))


def test_stale_running_run_is_closed_and_leaves_the_group_typing_set(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "stale.db")
    bot = repository.create_bot("Tester", "", "", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    group = repository.create_group("QA", "", [bot.id])
    run = repository.create_run(bot.id, conversation_id, "kerja", "test-model")
    repository.link_run_to_group(run.id, group.id)
    repository.update_run(run.id, RunStatus.RUNNING)
    _backdate(repository, run.id)

    stranger = RunRuntime(repository, None, "test-model", 1)  # type: ignore[arg-type]
    assert stranger.reconcile_runs() == 1

    closed = repository.get_run(run.id)
    assert closed.status is RunStatus.FAILED
    assert closed.error == ORPHAN_NOTICE
    assert repository.list_messages(conversation_id)[-1].content == ORPHAN_NOTICE
    assert repository.list_group_messages(group.id)[-1].content == ORPHAN_NOTICE
    assert repository.live_runs_for_group(group.id) == []


def test_owner_keeps_a_live_run_when_the_heartbeat_row_is_stale(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "owner.db")
    bot = repository.create_bot("Bandros", "", "", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    run = repository.create_run(bot.id, conversation_id, "kerja", "test-model")
    started = asyncio.Event()
    release = asyncio.Event()

    class Gate:
        async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
            started.set()
            await release.wait()
            return "masih jalan"

    owner = RunRuntime(repository, Gate(), "test-model", 1)

    async def scenario() -> None:
        work = asyncio.create_task(owner.start_and_wait(run.id))
        await started.wait()
        _backdate(repository, run.id)
        assert owner.reconcile_runs() == 0
        assert repository.get_run(run.id).status is RunStatus.RUNNING
        release.set()
        await work

    asyncio.run(scenario())
    assert repository.get_run(run.id).status is RunStatus.COMPLETED
    assert all(message.content != ORPHAN_NOTICE for message in repository.list_messages(conversation_id))


def test_finished_local_task_does_not_leave_running(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "dead-task.db")
    bot = repository.create_bot("Bandros", "", "", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    run = repository.create_run(bot.id, conversation_id, "kerja", "test-model")
    repository.update_run(run.id, RunStatus.RUNNING)
    owner = RunRuntime(repository, None, "test-model", 1)  # type: ignore[arg-type]

    async def scenario() -> None:
        async def dead() -> None:
            return None

        owner._tasks[run.id] = asyncio.create_task(dead())
        await asyncio.sleep(0)
        assert owner.reconcile_runs() == 1

    asyncio.run(scenario())
    assert repository.get_run(run.id).status is RunStatus.FAILED
    assert repository.get_run(run.id).error == ORPHAN_NOTICE


def _client(tmp_path: Path, monkeypatch, account: str, token: str) -> tuple[TestClient, dict[str, str], Path]:
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
            token,
            {"auth_mode": "codex", "account_id": account, "access_token": "a"},
        )
    )
    database_path, _, _ = tenant_locations(account, tmp_path, tmp_path / "workspace")
    return TestClient(main.app), {"X-Bandros-Session": token}, database_path


def test_activity_on_a_new_instance_keeps_a_leased_run(tmp_path: Path, monkeypatch) -> None:
    client, headers, database_path = _client(tmp_path, monkeypatch, "acct_lease", "token-lease")
    with client:
        listed = client.get("/api/v1/bots", headers=headers)
        assert listed.status_code == 200
        repository = Repository(Database(database_path))
        bot = next(item for item in repository.list_bots() if item.name == "Bandros")
        run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "status", "test-model")
        repository.update_run(run.id, RunStatus.RUNNING)
        main._workspaces.clear()

        activity = client.get(f"/api/v1/bots/{bot.id}/activity", headers=headers)
        messages = client.get(f"/api/v1/bots/{bot.id}/messages", headers=headers)

    assert activity.status_code == 200
    assert activity.json()["working"] is True
    assert activity.json()["error"] is None
    assert repository.get_run(run.id).status is RunStatus.RUNNING
    assert all(message["content"] != ORPHAN_NOTICE for message in messages.json())
    main._workspaces.clear()


def test_activity_on_a_new_instance_clears_an_orphaned_run(tmp_path: Path, monkeypatch) -> None:
    client, headers, database_path = _client(tmp_path, monkeypatch, "acct_orphan", "token-orphan")
    with client:
        listed = client.get("/api/v1/bots", headers=headers)
        assert listed.status_code == 200
        repository = Repository(Database(database_path))
        bot = next(item for item in repository.list_bots() if item.name == "Bandros")
        group = repository.create_group("QA", "", [bot.id])
        run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "status", "test-model")
        repository.link_run_to_group(run.id, group.id)
        repository.update_run(run.id, RunStatus.RUNNING)
        _backdate(repository, run.id)
        main._workspaces.clear()

        activity = client.get(f"/api/v1/bots/{bot.id}/activity", headers=headers)
        group_activity = client.get(f"/api/v1/groups/{group.id}/activity", headers=headers)
        messages = client.get(f"/api/v1/bots/{bot.id}/messages", headers=headers)
        group_messages = client.get(f"/api/v1/groups/{group.id}/messages", headers=headers)

    assert activity.status_code == 200
    assert activity.json()["working"] is False
    assert activity.json()["error"] == ORPHAN_NOTICE
    assert repository.get_run(run.id).status is RunStatus.FAILED
    assert any(message["content"] == ORPHAN_NOTICE for message in messages.json())
    assert group_activity.json() == []
    assert any(message["content"] == ORPHAN_NOTICE for message in group_messages.json())
    main._workspaces.clear()
