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
    return "@everyone" in lowered or "@all" in lowered or "@semua" in lowered


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


def continues_the_work(text: str) -> bool:
    """A specialist moved the job forward, so the lead should route the next step."""
    if is_silence(text) or is_status_report(text):
        return False
    lowered = text.lower()
    if any(token in lowered for token in ("selesai", "hasil", "berikut", "temuan", "draft", "sudah", "laporan", "rekomendasi")):
        return True
    if "?" in text:
        return True
    return len(text.strip()) >= 180


def route_next_owner(answer: str, members: list[Bot], sender: Bot, finished_ids: set, allow_many: bool) -> str:
    """The lead keeps one unfinished owner. A finished teammate is not mentioned again."""
    if sender.id != lead_bot(members).id:
        return answer
    for member in sorted(members, key=lambda item: len(item.name), reverse=True):
        if member.id in finished_ids:
            answer = re.sub(rf"@{re.escape(member.name)}(?!\w)", member.name, answer, flags=re.IGNORECASE)
    if not allow_many:
        mentioned = [member for member in mentioned_bots(answer, members) if member.id != sender.id]
        for extra in mentioned[1:]:
            answer = re.sub(rf"@{re.escape(extra.name)}(?!\w)", extra.name, answer, flags=re.IGNORECASE)
    return re.sub(r"[ \t]{2,}", " ", answer).strip()


def without_self_mention(answer: str, sender: Bot) -> str:
    """A bot never wakes itself. @OwnName becomes the plain name."""
    return re.sub(rf"@{re.escape(sender.name)}(?!\w)", sender.name, answer, flags=re.IGNORECASE)


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
        "Ini tim Grok Bot. Pengguna hanya menerima hasil. Jangan menunggu pengguna mengatur langkah.",
        "Satu tahap, satu pemilik. Orkestrator menyebut tepat satu @Nama yang mengerjakan sekarang, plus data yang sudah ada.",
        "Kalau kamu disebut: kerjakan tuntas di balasan ini. Jangan bertanya scope. Tulis satu kalimat asumsi, lalu hasilnya.",
        "Untuk riset, panggil web_search atau fetch_url dulu. Untuk berkas, panggil write_workspace_file. Jangan hanya berjanji akan mencari.",
        "Setelah hasil ada, akhiri dengan @Bandros dan apa yang selesai. Jangan hanya bilang siap.",
        "Kalau pesan ini bukan untukmu, balas (diam). @everyone berarti setiap anggota menjawab sekali.",
        "Jangan menulis @ di depan namamu sendiri, jangan handoff, dan jangan menyalin pesan orang lain.",
    ]
    if transcript.strip():
        lines.append(f"Riwayat:\n{transcript.strip()}")
    if already_replied:
        lines.append(
            f"Sudah membalas: {', '.join(already_replied)}. "
            "Jangan menyuruh mereka mengulang. Kalau kamu orkestrator dan masih ada tahap berikutnya, sebut satu rekan yang belum selesai. "
            "Kalau pekerjaan sudah selesai, balas ke pengguna tanpa @mention."
        )
    lines.append(content.strip())
    return "\n".join(lines)
