import asyncio
from pathlib import Path
from uuid import uuid4

from apps.api.app.database import Database
from apps.api.app.group_activity import describe_group_activity, load_group_activity
from apps.api.app.repository import Repository
from apps.api.app.runtime import RunRuntime


def test_waiting_members_are_not_marked_as_typing() -> None:
    members = [
        (uuid4(), "Bandros"),
        (uuid4(), "Tester Satu"),
        (uuid4(), "Tester Dua"),
        (uuid4(), "Tester Tiga"),
    ]
    described = describe_group_activity([], members)
    assert [name for _, name, _ in described] == ["Bandros", "Tester Satu", "Tester Dua", "Tester Tiga"]
    assert [status for _, _, status in described] == ["running", "queued", "queued", "queued"]


def test_live_speaker_is_running_and_not_listed_twice() -> None:
    speaker = uuid4()
    waiting = uuid4()
    described = describe_group_activity(
        [(speaker, "Tester Dua", "queued")],
        [(speaker, "Tester Dua"), (waiting, "Bandros")],
    )
    assert described == [(speaker, "Tester Dua", "running"), (waiting, "Bandros", "queued")]


def test_blank_activity_names_are_omitted() -> None:
    assert describe_group_activity([(uuid4(), "  ", "running")], [(uuid4(), "")]) == []


def test_live_runs_do_not_make_the_bench_look_like_it_is_typing() -> None:
    """Several queued runs marked running are not composers. None of them has left the queue."""
    head = uuid4()
    satu = uuid4()
    dua = uuid4()
    bandros = uuid4()
    roster = [
        (head, "Tester Tiga"),
        (satu, "Tester Satu"),
        (dua, "Tester Dua"),
        (bandros, "Bandros"),
    ]
    live = [(bot_id, name, "running") for bot_id, name in roster]
    described = describe_group_activity(live, roster, None)
    assert [status for _, _, status in described] == ["queued", "queued", "queued", "queued"]


def test_cross_instance_types_the_bot_who_already_left_the_queue() -> None:
    """No in-memory composer. The writer is the live run that is no longer queued."""
    composer = uuid4()
    nxt = uuid4()
    dua = uuid4()
    bandros = uuid4()
    described = describe_group_activity(
        [
            (nxt, "Tester Satu", "running"),
            (composer, "Tester Tiga", "running"),
        ],
        [(nxt, "Tester Satu"), (dua, "Tester Dua"), (bandros, "Bandros")],
        None,
    )
    assert [name for _, name, status in described if status == "running"] == ["Tester Tiga"]
    assert [name for _, name, status in described if status == "queued"] == ["Tester Satu", "Tester Dua", "Bandros"]


def test_queue_head_is_only_used_when_nobody_is_running() -> None:
    nxt = uuid4()
    described = describe_group_activity(
        [],
        [(nxt, "Tester Satu"), (uuid4(), "Tester Dua")],
        None,
    )
    assert [status for _, _, status in described] == ["running", "queued"]
    assert described[0][1] == "Tester Satu"


def test_composer_waiting_for_approval_stays_the_only_typing_status() -> None:
    composer = uuid4()
    waiter = uuid4()
    described = describe_group_activity(
        [(composer, "Tester Tiga", "waiting_approval"), (waiter, "Bandros", "running")],
        [(waiter, "Bandros")],
        None,
    )
    assert described == [
        (composer, "Tester Tiga", "waiting_approval"),
        (waiter, "Bandros", "queued"),
    ]


def test_old_group_lists_every_saved_member(tmp_path: Path) -> None:
    database = Database(tmp_path / "group.db")
    database.initialize()
    repository = Repository(database)
    ids = [
        repository.create_bot(name, "", "", None).id
        for name in ("Bandros", "Tester Tiga", "Tester Dua", "Tester Satu")
    ]
    group = repository.create_group("QA", "Bandros, Tester Tiga, Tester Dua", ids)
    assert [member.name for member in group.members] == [
        "Bandros",
        "Tester Tiga",
        "Tester Dua",
        "Tester Satu",
    ]
    reloaded = repository.get_group(group.id)
    assert [member.name for member in reloaded.members] == [member.name for member in group.members]
    assert len(reloaded.members) == 4


def test_finished_speaker_does_not_keep_the_typing_label(tmp_path: Path) -> None:
    database = Database(tmp_path / "handoff.db")
    database.initialize()
    repository = Repository(database)
    penulis = repository.create_bot("QA-Penulis", "", "TOKEN:penulis", None)
    analis = repository.create_bot("QA-Analis", "", "TOKEN:analis", None)
    group = repository.create_group("QA", "", [penulis.id, analis.id])
    repository.append_group_message(group.id, "user", "tulis lalu periksa")
    repository.enqueue_group_speaker(group.id, penulis.id, "tulis lalu periksa", None, 0)
    repository.enqueue_group_speaker(group.id, analis.id, "tulis lalu periksa", None, 0)

    class Gateway:
        async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
            return "Draf siap."

    runtime = RunRuntime(repository, Gateway(), "test-model", 3)
    speaker = asyncio.run(runtime.advance_group(group.id))
    assert speaker == "QA-Penulis"
    described = load_group_activity(repository, group.id, runtime.composing_bot_id(group.id))
    assert [name for _, name, status in described if status == "running"] == ["QA-Analis"]
    assert "QA-Penulis" not in [name for _, name, status in described if status == "running"]


def test_only_the_bot_inside_the_model_call_is_composing(tmp_path: Path) -> None:
    database = Database(tmp_path / "compose.db")
    database.initialize()
    repository = Repository(database)
    bots = [
        repository.create_bot(name, "", f"TOKEN:{name}", None)
        for name in ("Tester Tiga", "Tester Satu", "Tester Dua", "Bandros")
    ]
    group = repository.create_group("QA", "", [bot.id for bot in bots])
    for bot in bots:
        repository.enqueue_group_speaker(group.id, bot.id, "halo", None, 0)
    seen: list[object] = []

    class Gateway:
        async def complete(self, *, system: str, prompt: str, model: str, tools=(), request_limit: int = 8) -> str:
            seen.append(runtime.composing_bot_id(group.id))
            queued_now = repository.queued_bot_ids(group.id)
            assert bots[0].id not in queued_now
            live = [
                (run.bot_id, repository.get_bot(run.bot_id).name, str(run.status))
                for run in repository.live_runs_for_group(group.id)
            ]
            waiting = [(bot_id, repository.get_bot(bot_id).name) for bot_id in queued_now]
            # Another instance has the database rows and no in-memory composer.
            described = describe_group_activity(live, waiting, None)
            assert [name for _, name, status in described if status == "running"] == ["Tester Tiga"]
            assert "Tester Satu" not in [name for _, name, status in described if status == "running"]
            return "Siap."

    runtime = RunRuntime(repository, Gateway(), "test-model", 3)
    speaker = asyncio.run(runtime.advance_group(group.id))
    assert speaker == "Tester Tiga"
    assert seen == [bots[0].id]
    assert runtime.composing_bot_id(group.id) is None
    waiting = [(bot_id, repository.get_bot(bot_id).name) for bot_id in repository.queued_bot_ids(group.id)]
    described = describe_group_activity([], waiting)
    assert [name for _, name, status in described if status == "running"] == ["Tester Satu"]
    assert [status for _, _, status in described].count("queued") == 2
