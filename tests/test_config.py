from app.config import get_settings


def test_get_settings_parses_admin_ids(monkeypatch):
    monkeypatch.setenv("THREADBOT_ADMIN_IDS", "123, 456")

    settings = get_settings(require_tokens=False)

    assert settings.admin_ids == (123, 456)
