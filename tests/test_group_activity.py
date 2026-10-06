from uuid import uuid4

from apps.api.app.group_activity import describe_group_activity


def test_waiting_members_are_not_marked_as_typing() -> None:
    members = [
        (uuid4(), "Bandros"),
        (uuid4(), "Tester Satu"),
        (uuid4(), "Tester Dua"),
        (uuid4(), "Tester Tiga"),
    ]
    described = describe_group_activity([], members)
    assert [name for _, name, _ in described] == ["Bandros", "Tester Satu", "Tester Dua", "Tester Tiga"]
    assert [status for _, _, status in described] == ["queued", "queued", "queued", "queued"]


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
