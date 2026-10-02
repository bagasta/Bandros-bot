from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

from apps.api.app.database import Database
from apps.api.app.domain import Bot
from apps.api.app.mentions import mentioned_bots
from apps.api.app.repository import Repository
from apps.api.app.runtime import RunRuntime
from apps.api.app.workspace_tools import WorkspaceToolset, parse_search_results, public_https_url


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
                return reply(prompt) if callable(reply) else reply
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


def test_lead_does_not_send_a_second_message_when_mentioned_back(tmp_path: Path) -> None:
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
    assert [message.sender_bot_id for message in bot_messages] == [manager.id, worker.id]


def test_create_group_tool_joins_the_caller_and_named_bot(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "group.db")
    manager = repository.create_bot("Manager", "", "", None)
    repository.create_bot("Bot Riset", "Riset lowongan.", "Jangan submit.", None)
    run = repository.create_run(manager.id, repository.conversation_for_bot(manager.id), "Buat grup", "test-model")
    toolset = WorkspaceToolset(repository, run.id, manager.id, lambda _: None)

    result = asyncio.run(toolset.create_group({"name": "Tim Bot", "member_names": ["Bot Riset"]}))

    group = repository.get_group(UUID(result["group_id"]))
    assert [member.name for member in group.members] == ["Manager", "Bot Riset"]


def test_roll_call_mentions_every_teammate_and_their_status_does_not_bounce(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "rollcall.db")
    bandros = repository.create_bot("Bandros", "Orkestrator utama.", "TOKEN:bandros", None)
    frontend = repository.create_bot("Frontend", "Membuat antarmuka.", "TOKEN:frontend", None)
    backend = repository.create_bot("Backend", "Membuat API.", "TOKEN:backend", None)
    group = repository.create_group("Tim Frontend & Backend", "", [bandros.id, frontend.id, backend.id])
    text = "@Bandros cek kesiapan masing2 bot"
    repository.append_group_message(group.id, "user", text)
    runtime = RunRuntime(
        repository,
        ScriptedGateway({
            "TOKEN:bandros": "Frontend aktif. Backend aktif.",
            "TOKEN:frontend": "Siap @Bandros — antarmuka siap.",
            "TOKEN:backend": "Siap @Bandros — API siap.",
        }),
        "test-model",
        3,
    )

    asyncio.run(runtime.speak_in_group(group.id, text, None, 0))

    messages = repository.list_group_messages(group.id)
    assert "@Frontend" in messages[1].content and "@Backend" in messages[1].content
    assert "Frontend aktif" not in messages[1].content
    assert [message.sender_bot_id for message in messages] == [None, bandros.id, frontend.id, backend.id]


def test_everyone_mention_posts_a_reply_from_each_member(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "everyone.db")
    bandros = repository.create_bot("Bandros", "Orkestrator utama.", "TOKEN:bandros", None)
    frontend = repository.create_bot("Frontend", "Membuat antarmuka.", "TOKEN:frontend", None)
    backend = repository.create_bot("Backend", "Membuat API.", "TOKEN:backend", None)
    group = repository.create_group("Tim Produk", "", [bandros.id, frontend.id, backend.id])
    text = "@everyone kabar hari ini?"
    repository.append_group_message(group.id, "user", text)
    runtime = RunRuntime(
        repository,
        ScriptedGateway({
            "TOKEN:bandros": "Saya standby.",
            "TOKEN:frontend": "Landing page selesai.",
            "TOKEN:backend": "API siap.",
        }),
        "test-model",
        4,
    )

    asyncio.run(runtime.speak_in_group(group.id, text, None, 0))

    posted = [message.sender_bot_id for message in repository.list_group_messages(group.id) if message.sender_type == "bot"]
    assert posted == [bandros.id, frontend.id, backend.id]


def test_a_result_wakes_bandros_once_without_reassigning_the_same_bot(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "once.db")
    bandros = repository.create_bot("Bandros", "Orkestrator utama.", "TOKEN:bandros", None)
    frontend = repository.create_bot("Frontend", "Membuat antarmuka.", "TOKEN:frontend", None)
    group = repository.create_group("Tim Produk", "", [bandros.id, frontend.id])
    text = "Buatkan landing page rental mobil"
    repository.append_group_message(group.id, "user", text)

    def bandros_reply(prompt: str) -> str:
        if "landing page sudah jadi" in prompt:
            return "Hasil akhir: landing page rental mobil sudah disusun."
        return "@Frontend buat landing page dari riset ini."

    runtime = RunRuntime(
        repository,
        ScriptedGateway({
            "TOKEN:bandros": bandros_reply,
            "TOKEN:frontend": "Selesai @Bandros, landing page sudah jadi.",
        }),
        "test-model",
        3,
    )

    asyncio.run(runtime.speak_in_group(group.id, text, None, 0))

    messages = repository.list_group_messages(group.id)
    assert [message.sender_bot_id for message in messages] == [None, bandros.id, frontend.id, bandros.id]
    assert messages[-1].content == "Hasil akhir: landing page rental mobil sudah disusun."
    assert "@Frontend" not in messages[-1].content


def test_bandros_hands_a_result_to_the_next_specialist(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "pipeline.db")
    bandros = repository.create_bot("Bandros", "Orkestrator utama.", "TOKEN:bandros", None)
    market = repository.create_bot("MarketRiset", "Mencari usaha.", "TOKEN:market", None)
    landing = repository.create_bot("LandingPage", "Menyusun halaman.", "TOKEN:landing", None)
    group = repository.create_group("Riset", "", [bandros.id, market.id, landing.id])
    text = "Cari usaha rental mobil lalu buatkan landing page"
    repository.append_group_message(group.id, "user", text)

    def bandros_reply(prompt: str) -> str:
        if "Draft halaman" in prompt:
            return "Hasil akhir: lima usaha rental dan landing page-nya sudah ada."
        if "temuan rental" in prompt:
            return "@LandingPage susun halamannya dari temuan ini. @MarketRiset sudah selesai."
        return "@MarketRiset @LandingPage kerjakan sekarang."

    runtime = RunRuntime(
        repository,
        ScriptedGateway({
            "TOKEN:bandros": bandros_reply,
            "TOKEN:market": "Hasil temuan rental: lima usaha di Indonesia. @Bandros",
            "TOKEN:landing": "Draft halaman selesai. @Bandros",
        }),
        "test-model",
        4,
    )

    asyncio.run(runtime.speak_in_group(group.id, text, None, 0))

    messages = repository.list_group_messages(group.id)
    assert [message.sender_bot_id for message in messages] == [None, bandros.id, market.id, bandros.id, landing.id, bandros.id]
    assert "@LandingPage" not in messages[1].content
    assert "@MarketRiset" in messages[1].content
    assert "@LandingPage" in messages[3].content
    assert "@" not in messages[-1].content


def test_bandros_does_not_mention_himself(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "self-mention.db")
    bandros = repository.create_bot("Bandros", "Orkestrator utama.", "TOKEN:bandros", None)
    frontend = repository.create_bot("Frontend", "Membuat antarmuka.", "TOKEN:frontend", None)
    group = repository.create_group("Tim Produk", "", [bandros.id, frontend.id])
    text = "@Bandros cek kesiapan masing2 bot"
    repository.append_group_message(group.id, "user", text)
    runtime = RunRuntime(
        repository,
        ScriptedGateway({
            "TOKEN:bandros": "@Frontend balas status. @Bandros akan ikut menilai.",
            "TOKEN:frontend": "Siap @Bandros — siap tugas baru.",
        }),
        "test-model",
        3,
    )

    asyncio.run(runtime.speak_in_group(group.id, text, None, 0))

    messages = repository.list_group_messages(group.id)
    bandros_message = next(message for message in messages if message.sender_bot_id == bandros.id)
    assert "@Bandros" not in bandros_message.content
    assert "Bandros akan ikut menilai" in bandros_message.content
    assert "@Frontend" in bandros_message.content
    assert [message.sender_bot_id for message in messages].count(bandros.id) == 1


def test_next_bot_sees_who_already_replied_and_does_not_ping_them(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "context.db")
    bandros = repository.create_bot("Bandros", "Orkestrator utama.", "TOKEN:bandros", None)
    frontend = repository.create_bot("Frontend", "Membuat antarmuka.", "TOKEN:frontend", None)
    backend = repository.create_bot("Backend", "Membuat API.", "TOKEN:backend", None)
    group = repository.create_group("Tim Produk", "", [bandros.id, frontend.id, backend.id])
    text = "Cek kesiapan semua tim @Bandros"
    repository.append_group_message(group.id, "user", text)
    gateway = ScriptedGateway({
        "TOKEN:bandros": "Saya cek sendiri.",
        "TOKEN:frontend": "Siap @Bandros — landingpage tokyo8 selesai.",
        "TOKEN:backend": "@Frontend @Backend — mohon balas status satu kalimat. @Bandros akan menilai.",
    })
    runtime = RunRuntime(repository, gateway, "test-model", 3)

    asyncio.run(runtime.speak_in_group(group.id, text, None, 0))

    assert any("Sudah membalas: Frontend" in prompt and "landingpage tokyo8 selesai" in prompt for prompt in gateway.prompts)
    messages = repository.list_group_messages(group.id)
    backend_message = next(message for message in messages if message.sender_bot_id == backend.id)
    assert "@Frontend" not in backend_message.content
    assert [message.sender_bot_id for message in messages].count(frontend.id) == 1
    assert [message.sender_bot_id for message in messages].count(bandros.id) == 1


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
    assert "web_search" in names
    assert "fetch_url" in names
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


def test_search_results_keep_the_public_target() -> None:
    html = '<a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Frent">Rental <b>mobil</b></a>'
    assert parse_search_results(html) == [{"title": "Rental mobil", "url": "https://example.com/rent"}]


def test_fetch_rejects_private_and_non_https_urls() -> None:
    assert asyncio.run(public_https_url("http://example.com")) is False
    assert asyncio.run(public_https_url("https://127.0.0.1/secret")) is False
    assert asyncio.run(public_https_url("https://169.254.169.254/latest")) is False
