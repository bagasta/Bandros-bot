from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import pytest

from apps.api.app.database import Database
from apps.api.app.main import _seed_workspace
from apps.api.app.mentions import lead_bot
from apps.api.app.orchestrator import ORCHESTRATOR_NAME
from apps.api.app.repository import Repository
from apps.api.app.workspace_tools import WorkspaceToolset


def make_repository(path: Path) -> Repository:
    database = Database(path)
    database.initialize()
    return Repository(database)


def test_each_workspace_starts_with_one_orchestrator(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "team.db")
    _seed_workspace(repository)
    _seed_workspace(repository)

    bots = repository.list_bots()
    assert [bot.name for bot in bots if bot.name == ORCHESTRATOR_NAME] == [ORCHESTRATOR_NAME]
    assert bots[0].name == ORCHESTRATOR_NAME
    assert "Tugas" in bots[0].instructions
    assert "Batasan" in bots[0].instructions


def test_orchestrator_is_the_group_lead_and_stays_first(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "order.db")
    bandros = repository.create_bot("Bandros", "Orkestrator utama.", "Mengatur tim.", None)
    repository.create_bot("Frontend", "Membuat antarmuka.", "Tugas UI.", None)
    manager = repository.create_bot("Manager", "", "", None)

    assert repository.list_bots()[0].id == bandros.id
    assert lead_bot(repository.list_bots()).id == bandros.id
    assert lead_bot([repository.list_bots()[1], manager]).id == manager.id


def test_deleting_a_bot_removes_it_without_recreating_the_orchestrator(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "delete.db")
    _seed_workspace(repository)
    specialist = repository.create_bot("Riset", "Mencari sumber.", "Tugas riset.", None)
    group = repository.create_group("Tim", "", [specialist.id])
    repository.append_group_message(group.id, "bot", "Siap.", specialist.id)
    repository.append_message(repository.conversation_for_bot(specialist.id), "user", "Cari sumber")

    repository.delete_bot(specialist.id)
    _seed_workspace(repository)

    assert [bot.name for bot in repository.list_bots()] == [ORCHESTRATOR_NAME]
    assert repository.list_group_messages(group.id)[0].sender_bot_id is None
    with pytest.raises(KeyError):
        repository.get_bot(specialist.id)


def test_create_bot_requires_detailed_instructions(tmp_path: Path) -> None:
    repository = make_repository(tmp_path / "create.db")
    bandros = repository.create_bot("Bandros", "Orkestrator utama.", "Mengatur tim dengan cukup detail.", None)
    run = repository.create_run(bandros.id, repository.conversation_for_bot(bandros.id), "Buat bot", "test-model")
    tool = next(item for item in WorkspaceToolset(repository, run.id, bandros.id, lambda _: None).definitions() if item.name == "create_bot")

    rejected = asyncio.run(tool.handler({"name": "Riset", "description": "Riset", "instructions": "Bantu riset."}))
    assert rejected["ok"] is False
    assert "Tugas" in rejected["error"]

    instructions = (
        "Tugas: kumpulkan lowongan yang diminta pengguna dan berhenti setelah tiga temuan relevan. "
        "Cara kerja: baca permintaan, cari sumber, catat tautan, lalu tanyakan hanya jika peran atau lokasinya kosong. "
        "Output: tiga poin singkat berisi peran, tautan, dan alasan cocok. "
        "Batasan: jangan mengirim lamaran, jangan menghubungi perusahaan, dan di grup balas (diam) jika tidak disebut."
    )
    created = asyncio.run(tool.handler({
        "name": "Riset",
        "description": "Mencari lowongan yang sesuai permintaan pengguna.",
        "instructions": instructions,
    }))
    assert created["ok"] is True
    assert repository.get_bot(UUID(created["bot_id"])).name == "Riset"
