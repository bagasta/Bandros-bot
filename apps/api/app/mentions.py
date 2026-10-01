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
        "Balas 1-3 kalimat seperti chat WhatsApp. Sebut @Nama hanya bila dia harus bertindak. Jika bukan untukmu, balas (diam).",
    ]
    if transcript.strip():
        lines.append(f"Riwayat:\n{transcript.strip()}")
    lines.append(content.strip())
    return "\n".join(lines)
