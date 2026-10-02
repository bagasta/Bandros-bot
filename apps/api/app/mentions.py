from __future__ import annotations

import re

from .domain import Bot, WorkGroup
from .orchestrator import ORCHESTRATOR_NAME


def mentioned_bots(text: str, bots: list[Bot]) -> list[Bot]:
    """Match @Name, preferring the longest bot name so multi-word names stay intact."""
    remaining = text
    found: list[Bot] = []
    for bot in sorted(bots, key=lambda item: len(item.name), reverse=True):
        pattern = re.compile(rf"@{re.escape(bot.name)}(?!\w)", re.IGNORECASE)
        if pattern.search(remaining):
            found.append(bot)
            remaining = pattern.sub(" ", remaining)
    return found


def is_silence(text: str) -> bool:
    return text.strip().lower() in {"(diam)", "diam", "[diam]", "no_reply"}


def addresses_everyone(text: str) -> bool:
    lowered = text.lower()
    return "@all" in lowered or "@semua" in lowered


def asks_roll_call(text: str) -> bool:
    """The user wants every other member to answer, as in a Grok group check-in."""
    lowered = text.lower()
    return any(
        token in lowered
        for token in (
            "masing",
            "tiap",
            "setiap",
            "semua bot",
            "each bot",
            "mention mereka",
            "mention semua",
            "sebut mereka",
            "sebut semua",
        )
    )


def is_status_report(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered.startswith("siap") or lowered.startswith("idle") or "reply singkat status" in lowered


def with_roll_call_mentions(answer: str, members: list[Bot], sender: Bot) -> str:
    others = [member for member in members if member.id != sender.id]
    mentioned = {member.id for member in mentioned_bots(answer, others)}
    missing = [member for member in others if member.id not in mentioned]
    if not missing:
        return answer
    tags = " ".join(f"@{member.name}" for member in missing)
    if not mentioned:
        return f"Cek siap — reply singkat status kamu: {tags}"
    return f"{answer.rstrip()}\n{tags}"


def lead_bot(members: list[Bot]) -> Bot:
    orchestrator = next((member for member in members if member.name.lower() == ORCHESTRATOR_NAME.lower()), None)
    if orchestrator is not None:
        return orchestrator
    for needle in ("manager", "manajer"):
        match = next((member for member in members if needle in member.name.lower()), None)
        if match is not None:
            return match
    return members[0]


def group_prompt(group: WorkGroup, content: str, transcript: str = "") -> str:
    roster = ", ".join(f"@{member.name}" for member in group.members)
    lines = [
        f"Grup {group.name}. Anggota: {roster}.",
        "Ini chat grup. Bot lain hanya bereaksi bila pesan memuat @Nama mereka.",
        "Kalau kamu memberi tugas, satu balasan wajib menyebut @Nama dari daftar anggota. Jangan handoff dan jangan posting ulang.",
        "Kalau kamu yang disebut, kerjakan tugasnya dan laporkan hasilnya. Jangan menyalin pesan sebelumnya dan jangan mulai dengan nama pengirim.",
        "Kalau pengguna minta cek tiap bot, sebut setiap anggota lain dengan @Nama. Jangan mengarang status mereka.",
        "Kalau kamu diminta reply status, satu kalimat: Siap @Bandros — status singkatmu.",
        "Kalau rekan hanya mengirim status, balas (diam).",
        "Kalau pesan ini bukan untukmu, balas (diam).",
    ]
    if transcript.strip():
        lines.append(f"Riwayat:\n{transcript.strip()}")
    lines.append(content.strip())
    return "\n".join(lines)
