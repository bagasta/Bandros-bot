from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

from apps.api.app.database import Database
from apps.api.app.domain import Bot
from apps.api.app.mentions import mentioned_bots
from apps.api.app.repository import Repository
from apps.api.app.runtime import RunRuntime
from apps.api.app.workspace_tools import WorkspaceToolset


def make_repository(path: Path) -> Repository:
    database = Database(path)
    database.initialize()
    return Repository(database)


def bot(name: str) -> Bot:
    return Bot.model_construct(name=name)


def test_mention_prefers_the_longest_bot_name() -> None:
    short = bot("Bot")
    research = bot("Bot Riset")
    found = mentioned_bots("@Bot Riset gas, @Bot standby", [short, research])
    assert [item.name for item in found] == ["Bot Riset", "Bot"]


class ScriptedGateway:
    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies
        self.prompts: list[str] = []

    async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
        self.prompts.append(prompt)
        for token, reply in self.replies.items():
            if f"Bot instructions:\n{token}" in system:
                return reply
        return "(diam)"


def test_unmentioned_message_wakes_the_lead_then_the_mention(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "group.db")
    manager = repository.create_bot("Manager", "", "TOKEN:manager", None)
    worker = repository.create_bot("Lamaran Kerja", "", "TOKEN:worker", None)
    group = repository.create_group("Tim Bot", "", [worker.id, manager.id])
    repository.append_group_message(group.id, "user", "Cek tim, siap kerja gak besok?")
    runtime = RunRuntime(
        repository,
        ScriptedGateway({
            "TOKEN:manager": "Siap. Tim standby — @Lamaran Kerja tahan dulu.",
            "TOKEN:worker": "Siap. Mekari tetap on hold.",
        }),
        "test-model",
        3,
    )

    asyncio.run(runtime.speak_in_group(group.id, "Cek tim, siap kerja gak besok?", None, 0))

    messages = repository.list_group_messages(group.id)
    assert [message.content for message in messages] == [
        "Cek tim, siap kerja gak besok?",
        "Siap. Tim standby — @Lamaran Kerja tahan dulu.",
        "Siap. Mekari tetap on hold.",
    ]
    assert messages[1].sender_bot_id == manager.id
    assert messages[2].sender_bot_id == worker.id
    assert "Riwayat:" in runtime.model_gateway.prompts[1]
    private = repository.list_messages(repository.conversation_for_bot(worker.id))
    assert all(message.role != "group" for message in private)
    assert all("Anggota:" not in message.content for message in private)


def test_explicit_mention_skips_the_other_bot(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "group.db")
    repository.create_bot("Manager", "", "TOKEN:manager", None)
    worker = repository.create_bot("Lamaran Kerja", "", "TOKEN:worker", None)
    group = repository.create_group("Tim Bot", "", [worker.id])
    # Manager is not a member; the mention selects the worker only.
    manager = repository.create_bot("Bandros", "", "TOKEN:manager", None)
    repository.add_group_member(group.id, manager.id)
    repository.append_group_message(group.id, "user", "Cek lamaran @Lamaran Kerja")
    runtime = RunRuntime(repository, ScriptedGateway({"TOKEN:worker": "Lagi saya isi."}), "test-model", 3)

    asyncio.run(runtime.speak_in_group(group.id, "Cek lamaran @Lamaran Kerja", None, 0))

    messages = repository.list_group_messages(group.id)
    assert [message.sender_bot_id for message in messages] == [None, worker.id]


def test_silence_is_not_posted_and_does_not_wake_anyone(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "group.db")
    manager = repository.create_bot("Manager", "", "TOKEN:manager", None)
    repository.create_bot("Lamaran Kerja", "", "TOKEN:worker", None)
    group = repository.create_group("Tim Bot", "", [manager.id])
    repository.append_group_message(group.id, "user", "Ping")
    runtime = RunRuntime(repository, ScriptedGateway({"TOKEN:manager": "(diam)"}), "test-model", 3)

    asyncio.run(runtime.speak_in_group(group.id, "Ping", None, 0))

    assert [message.content for message in repository.list_group_messages(group.id)] == ["Ping"]


def test_mention_chain_stops_after_three_replies(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "group.db")
    manager = repository.create_bot("Manager", "", "TOKEN:manager", None)
    worker = repository.create_bot("Worker", "", "TOKEN:worker", None)
    group = repository.create_group("Tim Bot", "", [manager.id, worker.id])
    repository.append_group_message(group.id, "user", "Gas")
    runtime = RunRuntime(
        repository,
        ScriptedGateway({"TOKEN:manager": "Lanjut @Worker", "TOKEN:worker": "Lanjut @Manager"}),
        "test-model",
        3,
    )

    asyncio.run(runtime.speak_in_group(group.id, "Gas", None, 0))

    bot_messages = [message for message in repository.list_group_messages(group.id) if message.sender_type == "bot"]
    assert len(bot_messages) == 4
    assert bot_messages[-1].sender_bot_id == worker.id


def test_create_group_tool_joins_the_caller_and_named_bot(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "group.db")
    manager = repository.create_bot("Manager", "", "", None)
    repository.create_bot("Bot Riset", "Riset lowongan.", "Jangan submit.", None)
    run = repository.create_run(manager.id, repository.conversation_for_bot(manager.id), "Buat grup", "test-model")
    toolset = WorkspaceToolset(repository, run.id, manager.id, lambda _: None)

    result = asyncio.run(toolset.create_group({"name": "Tim Bot", "member_names": ["Bot Riset"]}))

    group = repository.get_group(UUID(result["group_id"]))
    assert [member.name for member in group.members] == ["Manager", "Bot Riset"]


def test_group_run_delegates_only_by_mention(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "group.db")
    bandros = repository.create_bot("Bandros", "Orkestrator utama.", "TOKEN:bandros", None)
    frontend = repository.create_bot("Frontend", "Membuat antarmuka.", "TOKEN:frontend", None)
    group = repository.create_group("Tim Frontend & Backend", "", [bandros.id, frontend.id])
    run = repository.create_run(bandros.id, repository.conversation_for_bot(bandros.id), "Buat landing page", "test-model")
    repository.link_run_to_group(run.id, group.id)
    toolset = WorkspaceToolset(repository, run.id, bandros.id, lambda _: None)
    names = {tool.name for tool in toolset.definitions()}

    handoff = asyncio.run(toolset.handoff_to_bot({"target_bot_id": str(frontend.id), "task": "Buat landing page"}))
    posted = asyncio.run(toolset.post_to_group({"group_id": str(group.id), "content": "@Frontend buatkan landing page"}))

    assert "handoff_to_bot" not in names
    assert "post_to_group" not in names
    assert handoff["ok"] is False
    assert "@Nama" in handoff["error"]
    assert posted["ok"] is False
    assert repository.list_group_messages(group.id) == []


def test_same_reply_is_not_posted_twice(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "group.db")
    manager = repository.create_bot("Manager", "", "TOKEN:manager", None)
    worker = repository.create_bot("Worker", "", "TOKEN:worker", None)
    group = repository.create_group("Tim Bot", "", [manager.id, worker.id])
    run = repository.create_run(manager.id, repository.conversation_for_bot(manager.id), "Balas", "test-model")
    repository.link_run_to_group(run.id, group.id)
    runtime = RunRuntime(repository, ScriptedGateway({"TOKEN:manager": "Siap @Worker", "TOKEN:worker": "Siap."}), "test-model", 3)
    runtime._group_depth[run.id] = 0
    toolset = WorkspaceToolset(repository, run.id, manager.id, lambda _: None, on_group_post=runtime.queue_group_wake)

    asyncio.run(toolset.post_to_group({"group_id": str(group.id), "content": "Siap @Worker"}))
    asyncio.run(runtime.start_and_wait(run.id))

    contents = [message.content for message in repository.list_group_messages(group.id)]
    assert contents.count("Siap @Worker") == 1
    assert contents == ["Siap @Worker", "Siap."]
