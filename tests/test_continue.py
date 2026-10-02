from __future__ import annotations

import asyncio

from vercel.cache.context import set_context

from apps.api.app.main import continue_after_response


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
