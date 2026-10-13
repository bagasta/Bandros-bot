from pathlib import Path

from apps.api.app.firecracker_computer import FirecrackerComputer


def test_status_stays_off_until_the_microvm_is_running(tmp_path: Path) -> None:
    computer = FirecrackerComputer(account_id="acct", root=tmp_path)
    assert computer.status() == {"state": "off", "screen_url": None}
