from __future__ import annotations

from uuid import UUID

LIVE_TYPING_STATUSES = frozenset({"running", "waiting_approval"})


def describe_group_activity(
    live: list[tuple[UUID, str, str]],
    queued: list[tuple[UUID, str]],
    composer_id: UUID | None = None,
) -> list[tuple[UUID, str, str]]:
    """Activity rows for the group chat.

    Only the bot that is composing (or, before that call starts, the bot at
    the head of the queue) is ``running`` or ``waiting_approval``. Everyone
    else still waiting stays ``queued`` so the client can keep Stop without
    painting a typing bubble. A live task is not enough: queued runs used to
    be promoted to ``running``, which made the whole bench look like it was
    typing.
    """
    live_status: dict[UUID, str] = {}
    names: dict[UUID, str] = {}
    for bot_id, name, status in live:
        cleaned = name.strip()
        if not cleaned:
            continue
        names.setdefault(bot_id, cleaned)
        live_status.setdefault(bot_id, status)

    queue_ids: list[UUID] = []
    for bot_id, name in queued:
        cleaned = name.strip()
        if not cleaned:
            continue
        names.setdefault(bot_id, cleaned)
        if bot_id not in queue_ids:
            queue_ids.append(bot_id)

    if composer_id is not None and composer_id in names:
        typing_id: UUID | None = composer_id
    elif queue_ids:
        typing_id = queue_ids[0]
    else:
        typing_id = None

    order: list[UUID] = []
    if typing_id is not None and typing_id in names:
        order.append(typing_id)
    for bot_id in queue_ids:
        if bot_id not in order:
            order.append(bot_id)
    for bot_id in live_status:
        if bot_id not in order and bot_id in names:
            order.append(bot_id)

    described: list[tuple[UUID, str, str]] = []
    seen: set[str] = set()
    for bot_id in order:
        cleaned = names[bot_id]
        if cleaned in seen:
            continue
        seen.add(cleaned)
        if bot_id == typing_id:
            status = live_status.get(bot_id, "running")
            if status not in LIVE_TYPING_STATUSES:
                status = "running"
        else:
            status = "queued"
        described.append((bot_id, cleaned, status))
    return described
