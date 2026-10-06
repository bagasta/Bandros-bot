from __future__ import annotations

from uuid import UUID

LIVE_TYPING_STATUSES = frozenset({"running", "waiting_approval"})


def describe_group_activity(
    live: list[tuple[UUID, str, str]],
    queued: list[tuple[UUID, str]],
    composer_id: UUID | None = None,
) -> list[tuple[UUID, str, str]]:
    """Activity rows for the group chat.

    The bot who is typing is the one whose run is alive (``running`` or
    ``waiting_approval``) and who has already left the queue. That row is in
    the shared database, so another API instance can see it without the
    in-memory composer id. The queue head is only a fallback when nothing is
    actually running; otherwise the next waiter must not take the bubble.
    """
    live_status: dict[UUID, str] = {}
    names: dict[UUID, str] = {}
    alive_ids: list[UUID] = []
    for bot_id, name, status in live:
        cleaned = name.strip()
        if not cleaned:
            continue
        names.setdefault(bot_id, cleaned)
        if status not in LIVE_TYPING_STATUSES:
            continue
        live_status[bot_id] = status
        if bot_id not in alive_ids:
            alive_ids.append(bot_id)

    queue_ids: list[UUID] = []
    for bot_id, name in queued:
        cleaned = name.strip()
        if not cleaned:
            continue
        names.setdefault(bot_id, cleaned)
        if bot_id not in queue_ids:
            queue_ids.append(bot_id)

    waiting = set(queue_ids)
    unqueued_alive = [bot_id for bot_id in alive_ids if bot_id not in waiting]
    typing_id: UUID | None
    if unqueued_alive:
        typing_id = composer_id if composer_id in unqueued_alive else unqueued_alive[0]
    elif not alive_ids and queue_ids:
        typing_id = queue_ids[0]
    elif len(alive_ids) == 1:
        typing_id = alive_ids[0]
    else:
        typing_id = None

    order: list[UUID] = []
    if typing_id is not None and typing_id in names:
        order.append(typing_id)
    for bot_id in queue_ids:
        if bot_id not in order:
            order.append(bot_id)
    for bot_id in alive_ids:
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
