from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_selected_bot_survives_empty_bot_list_refresh() -> None:
    completed = subprocess.run(
        ["node", "--experimental-strip-types", "--test", "tests/chat-presentation.test.ts"],
        check=False,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout

    source = (ROOT / "apps/web/app/GrokDashboard.tsx").read_text()
    assert "resolveSelectedBot" in source
    assert "mergeBotDirectory" in source
    assert "[selectedBot?.id, chatGPT.connected]" in source
    assert "selectedBotId.current != null && selectedBotId.current !== botId" in source
    assert "if (current) return nextBots.find((bot) => bot.id === current.id) ?? null;" not in source
