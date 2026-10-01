from pathlib import Path

from apps.api.app.database import Database
from apps.api.app.main import listed_codex_models
from apps.api.app.repository import Repository


def test_listed_codex_models_keeps_picker_models_in_priority_order() -> None:
    models = listed_codex_models(
        {
            "models": [
                {"slug": "gpt-hidden", "display_name": "Hidden", "visibility": "hide", "priority": 0},
                {"slug": "gpt-api-off", "display_name": "Off", "visibility": "list", "supported_in_api": False, "priority": 0},
                {"slug": "gpt-5.4", "display_name": "GPT-5.4", "visibility": "list", "supported_in_api": True, "priority": 20},
                {"slug": "gpt-5.3-codex", "display_name": "GPT-5.3 Codex", "visibility": "list", "supported_in_api": True, "priority": 1},
                {"display_name": "Missing slug", "visibility": "list"},
            ]
        }
    )

    assert models == [
        {"id": "gpt-5.3-codex", "display_name": "GPT-5.3 Codex"},
        {"id": "gpt-5.4", "display_name": "GPT-5.4"},
    ]


def test_bot_model_can_return_to_automatic(tmp_path: Path) -> None:
    database = Database(tmp_path / "product.db")
    database.initialize()
    repository = Repository(database)
    bot = repository.create_bot("Research", "", "", "chatgpt/gpt-5.4")

    cleared = repository.update_bot(bot.id, {"model": None})

    assert cleared.model is None
