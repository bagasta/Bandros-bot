from __future__ import annotations

import asyncio

from vercel.cache.context import set_context

from apps.api.app.main import continue_after_response
from apps.api.app.database import Database
from apps.api.app.repository import Repository
from apps.api.app.runtime import RunRuntime
from apps.api.app.workspace_tools import WorkspaceToolset
from apps.api.app.domain import RunStatus


def test_work_runs_inline_without_a_vercel_callback() -> None:
    set_context(wait_until=None)
    done: list[str] = []

    async def work() -> None:
        done.append("ran")

    asyncio.run(continue_after_response(work()))

    assert done == ["ran"]


def test_work_is_scheduled_when_vercel_can_wait() -> None:
    scheduled: list[object] = []
    set_context(wait_until=scheduled.append)
    try:
        async def work() -> None:
            return None

        asyncio.run(continue_after_response(work()))
        assert len(scheduled) == 1
        coroutine = scheduled[0]
        coroutine.close()
    finally:
        set_context(wait_until=None)


def test_continue_request_keeps_the_interrupted_stage(tmp_path) -> None:
    database = Database(tmp_path / "resume.db")
    database.initialize()
    repository = Repository(database)
    bot = repository.create_bot("Worker", "", "", None)
    first = repository.create_run(bot.id, repository.conversation_for_bot(bot.id), "Build the report", "test")
    tool = next(
        item
        for item in WorkspaceToolset(repository, first.id, bot.id, lambda _: None).definitions()
        if item.name == "continue_own_work"
    )
    asyncio.run(tool.handler({"note": "finish the report table"}))
    repository.update_run(first.id, RunStatus.CANCELLED, "stopped", expect=RunStatus.QUEUED)

    interrupted = repository.latest_interrupted_run(bot.id)

    assert interrupted is not None
    assert interrupted.continuation == "finish the report table"
