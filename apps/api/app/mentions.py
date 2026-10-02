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
            "semua tim",
            "each bot",
            "mention mereka",
            "mention semua",
            "sebut mereka",
            "sebut semua",
        )
    )


def is_status_report(text: str) -> bool:
    lowered = text.strip().lower()
    return (
        lowered.startswith("siap")
        or lowered.startswith("idle")
        or "reply singkat status" in lowered
        or "mohon balas" in lowered
        or "balas status" in lowered
    )


def without_peer_mentions(answer: str, members: list[Bot], sender: Bot) -> str:
    """A specialist reports its own status. It does not @mention teammates who already spoke."""
    lead = lead_bot(members)
    if sender.id == lead.id:
        return answer
    for member in sorted(members, key=lambda item: len(item.name), reverse=True):
        if member.id in {sender.id, lead.id}:
            continue
        answer = re.sub(rf"@{re.escape(member.name)}(?!\w)", "", answer, flags=re.IGNORECASE)
    return re.sub(r"[ \t]{2,}", " ", answer).strip()


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


def group_prompt(group: WorkGroup, content: str, transcript: str = "", already_replied: list[str] | None = None) -> str:
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
    if already_replied:
        lines.append(
            f"Sudah membalas: {', '.join(already_replied)}. "
            "Jangan mention mereka dan jangan menyuruh mereka balas lagi. Balas hanya statusmu, sebut hanya @Bandros."
        )
    lines.append(content.strip())
    return "\n".join(lines)
