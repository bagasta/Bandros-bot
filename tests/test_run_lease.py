"""Shared-db lease: another instance must not call a live run interrupted."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.app import main
from apps.api.app import repository as repository_module
from apps.api.app import runtime as runtime_module
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
    repository.update_run(run.id, RunStatus.RUNNING, expect=RunStatus.QUEUED)

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
    repository.update_run(run.id, RunStatus.RUNNING, expect=RunStatus.QUEUED)
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
    repository.update_run(run.id, RunStatus.RUNNING, expect=RunStatus.QUEUED)
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


def test_tool_longer_than_the_lease_stays_alive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(repository_module, "RUN_LEASE", timedelta(milliseconds=350))
    monkeypatch.setattr(runtime_module, "HEARTBEAT_INTERVAL", 0.05)
    repository = make_repository(tmp_path / "tool.db")
    bot = repository.create_bot("Bandros", "", "", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    run = repository.create_run(bot.id, conversation_id, "cari sumber", "test-model")
    started = asyncio.Event()

    class SlowToolGateway:
        async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
            started.set()
            await asyncio.sleep(0.9)
            return "hasil alat"

    owner = RunRuntime(repository, SlowToolGateway(), "test-model", 1)

    async def scenario() -> None:
        work = asyncio.create_task(owner.start_and_wait(run.id))
        await started.wait()
        _backdate(repository, run.id)
        await asyncio.sleep(0.25)
        stranger = RunRuntime(repository, SlowToolGateway(), "test-model", 1)
        assert stranger.reconcile_runs() == 0
        current = repository.get_run(run.id)
        assert current.status is RunStatus.RUNNING
        assert current.heartbeat_at is not None
        assert datetime.now(UTC) - current.heartbeat_at < timedelta(milliseconds=350)
        await work

    asyncio.run(scenario())
    assert repository.get_run(run.id).status is RunStatus.COMPLETED
    assert repository.list_messages(conversation_id)[-1].content == "hasil alat"
    assert all(message.content != ORPHAN_NOTICE for message in repository.list_messages(conversation_id))


def test_unstarted_queued_run_expires_after_ten_minutes(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "expired-queue.db")
    bot = repository.create_bot("Bandros", "", "", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    group = repository.create_group("QA", "", [bot.id])
    run = repository.create_run(bot.id, conversation_id, "menunggu giliran", "test-model")
    repository.link_run_to_group(run.id, group.id)
    assert repository.enqueue_group_speaker(group.id, bot.id, "menunggu giliran", None, 0) is True
    stale = (datetime.now(UTC) - timedelta(minutes=10, seconds=5)).isoformat()
    with repository.database.connection() as db:
        db.execute("UPDATE runs SET created_at = ? WHERE id = ?", (stale, str(run.id)))
        db.execute("UPDATE group_queue SET created_at = ?", (stale,))

    stranger = RunRuntime(repository, None, "test-model", 1)  # type: ignore[arg-type]
    assert stranger.reconcile_runs() == 1

    closed = repository.get_run(run.id)
    assert closed.status is RunStatus.FAILED
    assert closed.error == ORPHAN_NOTICE
    assert repository.active_runs_for_bot(bot.id) == []
    assert repository.queued_bot_ids(group.id) == []
    assert repository.live_runs_for_group(group.id) == []


def test_recent_queued_run_is_not_expired(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "fresh-queue.db")
    bot = repository.create_bot("Bandros", "", "", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    group = repository.create_group("QA", "", [bot.id])
    run = repository.create_run(bot.id, conversation_id, "sebentar", "test-model")
    repository.link_run_to_group(run.id, group.id)
    assert repository.enqueue_group_speaker(group.id, bot.id, "sebentar", None, 0) is True
    waiting = (datetime.now(UTC) - timedelta(minutes=9)).isoformat()
    with repository.database.connection() as db:
        db.execute("UPDATE runs SET created_at = ? WHERE id = ?", (waiting, str(run.id)))
        db.execute("UPDATE group_queue SET created_at = ?", (waiting,))

    stranger = RunRuntime(repository, None, "test-model", 1)  # type: ignore[arg-type]
    assert stranger.reconcile_runs() == 0
    assert repository.get_run(run.id).status is RunStatus.QUEUED
    assert repository.queued_bot_ids(group.id) == [bot.id]
    assert repository.active_runs_for_bot(bot.id)


def test_local_task_keeps_an_old_queued_run(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "owned-queue.db")
    bot = repository.create_bot("Bandros", "", "", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    run = repository.create_run(bot.id, conversation_id, "masih di proses ini", "test-model")
    stale = (datetime.now(UTC) - timedelta(minutes=11)).isoformat()
    with repository.database.connection() as db:
        db.execute("UPDATE runs SET created_at = ? WHERE id = ?", (stale, str(run.id)))
    owner = RunRuntime(repository, None, "test-model", 1)  # type: ignore[arg-type]

    async def scenario() -> None:
        async def still_here() -> None:
            await asyncio.Event().wait()

        owner._tasks[run.id] = asyncio.create_task(still_here())
        assert owner.reconcile_runs() == 0
        owner._tasks[run.id].cancel()

    asyncio.run(scenario())
    assert repository.get_run(run.id).status is RunStatus.QUEUED


def test_two_writers_cannot_both_finish_one_run(tmp_path: Path) -> None:
    import threading

    repository = make_repository(tmp_path / "writers.db")
    bot = repository.create_bot("Bandros", "", "", None)
    run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "satu", "test-model")
    assert repository.update_run(run.id, RunStatus.RUNNING, expect=RunStatus.QUEUED) is True
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def finish() -> None:
        barrier.wait()
        results.append(repository.update_run(run.id, RunStatus.COMPLETED, expect=RunStatus.RUNNING))

    threads = [threading.Thread(target=finish) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]
    assert repository.get_run(run.id).status is RunStatus.COMPLETED


def test_two_writers_queue_one_bot_once(tmp_path: Path) -> None:
    import threading

    repository = make_repository(tmp_path / "queue-writers.db")
    bot = repository.create_bot("Bandros", "", "", None)
    group = repository.create_group("QA", "", [bot.id])
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def enqueue() -> None:
        barrier.wait()
        results.append(repository.enqueue_group_speaker(group.id, bot.id, "giliran", None, 0))

    threads = [threading.Thread(target=enqueue) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]
    assert repository.queued_bot_ids(group.id) == [bot.id]


def test_queued_run_survives_a_long_wait(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "queued.db")
    bot = repository.create_bot("Bandros", "", "", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    run = repository.create_run(bot.id, conversation_id, "menunggu giliran", "test-model")
    stale = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    with repository.database.connection() as db:
        db.execute("UPDATE runs SET created_at = ? WHERE id = ?", (stale, str(run.id)))

    stranger = RunRuntime(repository, None, "test-model", 1)  # type: ignore[arg-type]
    assert stranger.reconcile_runs() == 0
    assert repository.get_run(run.id).status is RunStatus.QUEUED
    assert repository.list_messages(conversation_id) == []


def test_orphan_close_is_conditional(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "conditional.db")
    bot = repository.create_bot("Tester", "", "", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    group = repository.create_group("QA", "", [bot.id])
    run = repository.create_run(bot.id, conversation_id, "kerja", "test-model")
    repository.link_run_to_group(run.id, group.id)
    repository.update_run(run.id, RunStatus.RUNNING, expect=RunStatus.QUEUED)

    assert repository.close_orphaned_run(run.id) is True
    assert repository.close_orphaned_run(run.id) is False
    assert repository.update_run(run.id, RunStatus.RUNNING, expect=RunStatus.QUEUED) is False
    assert repository.update_run(run.id, RunStatus.COMPLETED, expect=RunStatus.RUNNING) is False
    assert repository.commit_assistant_turn(run.id, "jawaban asli", "test-model") is False

    assert repository.get_run(run.id).status is RunStatus.FAILED
    assert [message.content for message in repository.list_messages(conversation_id)] == [ORPHAN_NOTICE]
    assert [message.content for message in repository.list_group_messages(group.id)] == [ORPHAN_NOTICE]


def test_activity_on_a_new_instance_keeps_a_leased_run(tmp_path: Path, monkeypatch) -> None:
    client, headers, database_path = _client(tmp_path, monkeypatch, "acct_lease", "token-lease")
    with client:
        listed = client.get("/api/v1/bots", headers=headers)
        assert listed.status_code == 200
        repository = Repository(Database(database_path))
        bot = next(item for item in repository.list_bots() if item.name == "Bandros")
        run = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "status", "test-model")
        repository.update_run(run.id, RunStatus.RUNNING, expect=RunStatus.QUEUED)
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
        repository.update_run(run.id, RunStatus.RUNNING, expect=RunStatus.QUEUED)
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
