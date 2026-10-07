from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from apps.api.app.domain import RunStatus
from apps.api.app.mentions import prompt_for_direct_message
from apps.api.app.runtime import RunRuntime
from tests.test_lifecycle import make_repository

STAGE = "selesaikan daftar nama yang sama"
LONG_LIST = "RuangGagasan SahabatData LajuCerdas"
NEW_FILE = "ide-nama-bot-lanjutan.txt"


def test_send_control_accepts_stop_while_a_turn_is_active() -> None:
    script = r"""
import { messageDraft, sendControlDisabled } from "./apps/web/app/composer-send.ts";

if (sendControlDisabled(true, "")) {
  throw new Error("idle empty-composer rule disabled send during an active turn");
}
if (sendControlDisabled(true, "berhenti")) {
  throw new Error("stop message cannot be submitted during an active turn");
}
if (messageDraft("  berhenti  ") !== "berhenti") {
  throw new Error("stop draft was dropped");
}
if (!sendControlDisabled(false, "")) {
  throw new Error("idle composer should still require text");
}
if (sendControlDisabled(false, "berhenti")) {
  throw new Error("idle composer rejected a non-empty draft");
}
"""
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "--eval", script],
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout

    source = Path("apps/web/app/GrokDashboard.tsx").read_text()
    assert 'type="button" disabled={sendControlDisabled(turnActive, prompt)} aria-label="Send message"' in source
    assert "requestSubmit" not in source
    assert "canSendMessage" not in source


def test_berhenti_then_lanjut_resumes_the_interrupted_stage(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "stop-stage.db")
    bot = repository.create_bot("Worker", "", "", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    repository.append_message(conversation_id, "user", "buat 40 nama bot")
    first = repository.create_run(bot.id, conversation_id, "buat 40 nama bot", "test-model")
    repository.set_run_continuation(first.id, STAGE)
    started = asyncio.Event()
    seen: list[str] = []

    class ListingGateway:
        async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
            seen.append(prompt)
            if "berhenti" in prompt.lower() and "Catatan tahap sebelumnya" not in prompt:
                return "Dihentikan."
            if STAGE in prompt:
                return f"melanjutkan tahap: {STAGE}"
            started.set()
            await asyncio.sleep(30)
            return f"{LONG_LIST} {NEW_FILE}"

    runtime = RunRuntime(repository, ListingGateway(), "test-model", 1)

    async def execute() -> None:
        runtime.queue_dm(bot.id, first.id, first.prompt)
        await asyncio.wait_for(started.wait(), timeout=1)
        runtime.interrupt_bot(bot.id)
        repository.append_message(conversation_id, "user", "berhenti")
        stopped = repository.create_run(bot.id, conversation_id, "berhenti", "test-model")
        runtime.queue_dm(bot.id, stopped.id, "berhenti")
        await asyncio.wait_for(runtime._burst_tasks[("dm", bot.id)], timeout=2)

        interrupted = repository.latest_interrupted_run(bot.id)
        assert interrupted is not None
        assert interrupted.id == first.id
        assert interrupted.continuation == STAGE
        resume = prompt_for_direct_message("lanjut", interrupted.continuation)
        assert STAGE in resume
        assert NEW_FILE not in resume
        follow = repository.create_run(bot.id, conversation_id, resume, "test-model")
        repository.append_message(conversation_id, "user", "lanjut")
        runtime.queue_dm(bot.id, follow.id, "lanjut")
        await asyncio.wait_for(runtime._burst_tasks[("dm", bot.id)], timeout=2)

    asyncio.run(execute())

    saved = [message.content for message in repository.list_messages(conversation_id)]
    assert LONG_LIST not in "\n".join(saved)
    assert NEW_FILE not in "\n".join(saved)
    assert any(STAGE in prompt for prompt in seen)
    assert repository.get_run(first.id).status is RunStatus.CANCELLED
    assert saved[-1] == f"melanjutkan tahap: {STAGE}"


def test_disconnect_drops_the_answer_before_it_is_saved(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "disconnect-stage.db")
    bot = repository.create_bot("Worker", "", "", None)
    conversation_id = repository.conversation_for_bot(bot.id)
    repository.append_message(conversation_id, "user", "buat 40 nama bot")
    first = repository.create_run(bot.id, conversation_id, "buat 40 nama bot", "test-model")
    repository.set_run_continuation(first.id, STAGE)
    started = asyncio.Event()
    disconnected = {"value": False}

    class ListingGateway:
        async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
            started.set()
            while not disconnected["value"]:
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            return f"{LONG_LIST} {NEW_FILE}"

    runtime = RunRuntime(repository, ListingGateway(), "test-model", 1)

    async def left() -> bool:
        return disconnected["value"]

    async def execute() -> None:
        runtime._disconnect_probes[first.id] = left
        runtime.queue_dm(bot.id, first.id, first.prompt)
        await asyncio.wait_for(started.wait(), timeout=1)
        disconnected["value"] = True
        await asyncio.wait_for(runtime._burst_tasks[("dm", bot.id)], timeout=2)
        runtime._disconnect_probes.pop(first.id, None)

    asyncio.run(execute())

    saved = [message.content for message in repository.list_messages(conversation_id)]
    assert LONG_LIST not in "\n".join(saved)
    assert NEW_FILE not in "\n".join(saved)
    interrupted = repository.latest_interrupted_run(bot.id)
    assert interrupted is not None
    assert interrupted.id == first.id
    assert interrupted.continuation == STAGE
    assert STAGE in prompt_for_direct_message("lanjut", interrupted.continuation)
