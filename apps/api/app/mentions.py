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


_SILENCE_TAIL = re.compile(
    r"(?:\s*[\(\[](?:diam|no_reply)[\)\]]|\s+(?:diam|no_reply))+$",
    re.IGNORECASE,
)


def is_silence(text: str) -> bool:
    return text.strip().lower() in {"", "(diam)", "diam", "[diam]", "no_reply"}


def visible_reply(text: str) -> str:
    """Drop a trailing silence token so a real answer is not posted as '(diam)'."""
    cleaned = _SILENCE_TAIL.sub("", text).strip()
    if is_silence(cleaned):
        return ""
    return cleaned


def same_task(previous: str, nxt: str) -> bool:
    """A follow-up stays in the same task when it continues the last line or repeats its subject."""
    lowered = nxt.lower().strip()
    if any(token in lowered for token in ("lanjut", "juga", "tambah", "sekalian", "yang tadi", "sama saja", "plus")):
        return True
    def words(value: str) -> set[str]:
        return {word for word in re.findall(r"[a-zA-Z0-9]{4,}", value.lower())}
    shared = words(previous) & words(nxt)
    return len(shared) >= 2 or (len(lowered) < 48 and bool(shared))


def cluster_topics(messages: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    for message in messages:
        if groups and same_task(groups[-1][-1], message):
            groups[-1].append(message)
        else:
            groups.append([message])
    return groups


def burst_prompt(lines: list[str]) -> str:
    if len(lines) == 1:
        return lines[0]
    body = "\n".join(f"{index}. {line}" for index, line in enumerate(lines, 1))
    return "Pesan beruntun dari bos untuk satu tugas yang sama. Jawab sekali dan mencakup semuanya.\n" + body


_STOP_PHRASES = {
    "stop",
    "stop now",
    "stop semua",
    "berhenti",
    "berhenti sekarang",
    "berhenti semua",
    "kalian berhenti",
    "kalian stop",
    "semua berhenti",
    "tolong berhenti",
    "tolong stop",
}


def is_stop_request(text: str, bots: list[Bot] | None = None) -> bool:
    """A direct stop ends the turn, including '@Nama berhenti'."""
    lowered = text.strip().lower()
    if lowered in _STOP_PHRASES:
        return True
    cleaned = lowered
    for bot in sorted(bots or [], key=lambda item: len(item.name), reverse=True):
        cleaned = re.sub(rf"@{re.escape(bot.name)}(?!\w)", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"@\S+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,!")
    return cleaned in _STOP_PHRASES


def bots_to_stop(text: str, bots: list[Bot]) -> list[Bot] | None:
    """None means this is not a stop. An empty list stops the whole group."""
    if not is_stop_request(text, bots):
        return None
    if addresses_everyone(text):
        return []
    return mentioned_bots(text, bots)


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


def without_peer_mentions(answer: str, members: list[Bot], sender: Bot, only_ids: set | None = None) -> str:
    """Drop @mentions of teammates who already answered. A new owner stays mentioned."""
    lead = lead_bot(members)
    if sender.id == lead.id:
        return answer
    for member in sorted(members, key=lambda item: len(item.name), reverse=True):
        if member.id in {sender.id, lead.id}:
            continue
        if only_ids is not None and member.id not in only_ids:
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


def group_prompt(group: WorkGroup, content: str, transcript: str = "", already_replied: list[str] | None = None, jobs: str = "") -> str:
    roster = ", ".join(f"@{member.name}" for member in group.members)
    lines = [
        f"Grup {group.name}. Anggota: {roster}.",
        "Ini grup WhatsApp. Bos memberi arahan. Kamu ahli di bidangmu dan membalas seperti manusia: 2-6 kalimat, hasilnya dulu, tanpa judul atau laporan.",
        "Satu tahap, satu pemilik. Orkestrator menyebut tepat satu @Nama yang mengerjakan sekarang, plus data yang ada di pesan bos ini.",
        "Pesan baru dari bos adalah tugas baru, kecuali ia menulis lanjut, revisi, atau menunjuk hasil yang baru dikirim.",
        "Tugas baru tidak mewarisi usaha, menu, harga, atau asumsi dari job yang sudah selesai. Kalau pesan itu tidak menjelaskan bisnisnya, tanyakan satu kalimat dan jangan menugaskan rekan.",
        "Riwayat di bawah sudah kamu baca, termasuk pesan yang tidak menyebutmu. Jangan mengulang pekerjaan yang selesai.",
        "Kamu dipilih untuk bicara. Kerjakan tahapmu sendiri sampai ada hasil, lalu balas singkat. Jangan menulis (diam).",
        "Kalau tahapmu belum selesai dan masih butuh alat, panggil continue_own_work. Kamu akan dibangunkan lagi. Pengguna bisa menghentikanmu kapan saja dengan berhenti.",
        "Kalau perlu satu rekan mengerjakan langkah berikutnya, sebut tepat satu @Nama beserta datanya.",
        "@everyone: setiap anggota menjawab sekali. Sebut satu @Nama hanya untuk langkah berikutnya.",
    ]
    if jobs.strip():
        lines.append(f"Status job: {jobs.strip()}. Job selesai bukan bahan tugas berikutnya.")
    if transcript.strip():
        lines.append("Riwayat ini konteks yang sudah kamu baca, bukan tugas terpisah.")
        lines.append(f"Riwayat:\n{transcript.strip()}")
    if already_replied:
        lines.append(
            f"Sudah membalas: {', '.join(already_replied)}. "
            "Jangan menyuruh mereka mengulang. Kalau kamu orkestrator dan masih ada tahap berikutnya, sebut satu rekan yang belum selesai. "
            "Kalau pekerjaan sudah selesai, balas ke pengguna tanpa @mention."
        )
    lines.append(content.strip())
    return "\n".join(lines)
