"""Early Stop and 1:1 history on the shared Postgres database."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from apps.api.app.database import Database
from apps.api.app.domain import RunStatus
from apps.api.app.mentions import prompt_for_direct_message
from apps.api.app.repository import Repository
from apps.api.app.runtime import RunRuntime
from apps.api.app.tenancy import tenant_locations

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is not set")

LATE = "LATE-REPLY-SHOULD-NOT-PERSIST"


def _pair(tmp_path: Path) -> tuple[Path, str, Repository, Repository]:
    url = os.environ["DATABASE_URL"]
    path = tenant_locations(f"early-{uuid4()}", tmp_path, tmp_path / "files")[0]
    database = Database(path, database_url=url)
    database.initialize()
    return path, url, Repository(database), Repository(Database(path, database_url=url))


def _reread(path: Path, url: str) -> Repository:
    return Repository(Database(path, database_url=url))


def _texts(repository: Repository, conversation_id) -> list[str]:
    return [message.content for message in repository.list_messages(conversation_id)]


def test_stop_before_the_model_starts_keeps_the_user_message(tmp_path: Path) -> None:
    path, url, owner_repo, other_repo = _pair(tmp_path)
    bot = owner_repo.create_bot("Bandros", "", "", None)
    conversation_id = owner_repo.conversation_for_bot(bot.id)
    owner_repo.append_message(conversation_id, "user", "Halo")
    run = owner_repo.create_run(bot.id, conversation_id, "Halo", "test-model")
    calls: list[str] = []

    class Gate:
        async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
            calls.append(prompt)
            await asyncio.sleep(0.3)
            return LATE

    owner = RunRuntime(owner_repo, Gate(), "test-model", 1)
    stranger = RunRuntime(other_repo, Gate(), "test-model", 1)

    async def scenario() -> None:
        owner.queue_dm(bot.id, run.id, "Halo")
        await asyncio.sleep(0.015)
        stranger.stop(run.id)
        await asyncio.wait_for(owner._burst_tasks[("dm", bot.id)], timeout=2)
        stranger.reconcile_runs()

    asyncio.run(scenario())

    fresh = _reread(path, url)
    assert calls == []
    assert fresh.get_run(run.id).status is RunStatus.CANCELLED
    assert _texts(fresh, conversation_id) == ["Halo"]
    assert LATE not in "\n".join(_texts(other_repo, conversation_id))


def test_inflight_stop_drops_the_late_reply_and_lanjut_does_not_resend_it(tmp_path: Path) -> None:
    path, url, owner_repo, other_repo = _pair(tmp_path)
    bot = owner_repo.create_bot("Bandros", "", "", None)
    conversation_id = owner_repo.conversation_for_bot(bot.id)
    owner_repo.append_message(conversation_id, "user", "Halo")
    run = owner_repo.create_run(bot.id, conversation_id, "Halo", "test-model")
    started = asyncio.Event()
    release = asyncio.Event()
    prompts: list[str] = []

    class Gate:
        async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
            prompts.append(prompt)
            if "Pesan pengguna: lanjut" in prompt:
                return "jawaban lanjut"
            started.set()
            await release.wait()
            return LATE

    owner = RunRuntime(owner_repo, Gate(), "test-model", 1)
    stranger = RunRuntime(other_repo, Gate(), "test-model", 1)

    async def scenario() -> None:
        work = asyncio.create_task(owner.start_and_wait(run.id))
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.sleep(0.015)
        stranger.stop(run.id)
        release.set()
        await asyncio.wait_for(work, timeout=2)
        stranger.reconcile_runs()
        assert owner_repo.commit_assistant_turn(run.id, LATE, "test-model") is False

        fresh = _reread(path, url)
        assert _texts(fresh, conversation_id) == ["Halo"]
        interrupted = fresh.latest_interrupted_run(bot.id)
        resume = prompt_for_direct_message("lanjut", interrupted.continuation if interrupted else None)
        fresh.append_message(conversation_id, "user", "lanjut")
        follow = fresh.create_run(bot.id, conversation_id, resume, "test-model")
        await owner.start_and_wait(follow.id)

    asyncio.run(scenario())

    fresh = _reread(path, url)
    saved = _texts(fresh, conversation_id)
    assert saved[0] == "Halo"
    assert "lanjut" in saved
    assert LATE not in saved
    assert all(LATE not in prompt for prompt in prompts)
    assert fresh.get_run(run.id).status is RunStatus.CANCELLED


def test_disconnect_during_the_quiet_burst_cancels_before_the_model_call(tmp_path: Path) -> None:
    path, url, owner_repo, other_repo = _pair(tmp_path)
    bot = owner_repo.create_bot("Bandros", "", "", None)
    conversation_id = owner_repo.conversation_for_bot(bot.id)
    owner_repo.append_message(conversation_id, "user", "Halo")
    run = owner_repo.create_run(bot.id, conversation_id, "Halo", "test-model")
    disconnected = {"value": False}
    calls: list[str] = []

    class Gate:
        async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
            calls.append(prompt)
            return LATE

    async def left() -> bool:
        return disconnected["value"]

    owner = RunRuntime(owner_repo, Gate(), "test-model", 1)
    stranger = RunRuntime(other_repo, Gate(), "test-model", 1)

    async def scenario() -> None:
        owner._disconnect_probes[run.id] = left
        owner.queue_dm(bot.id, run.id, "Halo")
        await asyncio.sleep(0.015)
        disconnected["value"] = True
        await asyncio.wait_for(owner._burst_tasks[("dm", bot.id)], timeout=2)
        stranger.reconcile_runs()

    asyncio.run(scenario())

    fresh = _reread(path, url)
    assert calls == []
    assert fresh.get_run(run.id).status is RunStatus.CANCELLED
    assert _texts(fresh, conversation_id) == ["Halo"]


def test_direct_history_on_postgres_skips_group_replies(tmp_path: Path) -> None:
    path, url, repository, other = _pair(tmp_path)
    worker = repository.create_bot("Worker", "", "TOKEN:worker", None)
    group = repository.create_group("Tim", "", [worker.id])
    repository.append_group_message(group.id, "user", "Cek tim")
    conversation_id = repository.conversation_for_bot(worker.id)
    prompts: list[str] = []

    class Gate:
        async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
            prompts.append(prompt)
            if "halo pribadi" in prompt:
                return "Jawaban pribadi."
            return "Dari grup."

    runtime = RunRuntime(repository, Gate(), "test-model", 2)
    asyncio.run(runtime.speak_in_group(group.id, "Cek tim", None, 0))
    repository.append_message(conversation_id, "assistant", "Dari grup.")
    repository.append_message(conversation_id, "user", "halo pribadi")
    assert _texts(other, conversation_id) == ["halo pribadi"]

    follow = repository.create_run(worker.id, conversation_id, "halo pribadi", "test-model")
    asyncio.run(runtime.start_and_wait(follow.id))
    assert "Dari grup." not in prompts[-1]
    repository.drop_copied_group_context()

    fresh = _reread(path, url)
    assert _texts(fresh, conversation_id) == ["halo pribadi", "Jawaban pribadi."]
    with fresh.database.connection() as db:
        stored = [row["content"] for row in db.execute("SELECT content FROM messages").fetchall()]
    assert "Dari grup." not in stored
    assert [message.content for message in fresh.list_group_messages(group.id) if message.sender_type == "bot"] == ["Dari grup."]
