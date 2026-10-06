from __future__ import annotations

from uuid import UUID

LIVE_TYPING_STATUSES = frozenset({"running", "waiting_approval"})


def describe_group_activity(
    live: list[tuple[UUID, str, str]],
    queued: list[tuple[UUID, str]],
) -> list[tuple[UUID, str, str]]:
    """Activity rows for the group chat.

    A live run is someone composing. Everyone else still waiting in the queue
    stays ``queued`` so the client can keep the turn (Stop) without painting
    a typing bubble that has nothing to show.
    """
    described: list[tuple[UUID, str, str]] = []
    seen: set[str] = set()
    for bot_id, name, status in live:
        cleaned = name.strip()
        if not cleaned or cleaned in seen:
            continue
        if status == "queued":
            status = "running"
        described.append((bot_id, cleaned, status))
        seen.add(cleaned)
    for bot_id, name in queued:
        cleaned = name.strip()
        if not cleaned or cleaned in seen:
            continue
        described.append((bot_id, cleaned, "queued"))
        seen.add(cleaned)
    return described
