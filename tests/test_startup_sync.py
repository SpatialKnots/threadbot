import asyncio
from pathlib import Path

from app.config import Settings
from app import startup_sync


def make_settings(**overrides):
    data = {
        "telegram_bot_token": "telegram",
        "vk_access_token": "vk",
        "vk_group_domain": "group",
        "vk_api_version": "5.199",
        "database_url": "sqlite:///:memory:",
        "image_storage_path": Path("data/images"),
        "results_per_page": 5,
        "startup_fetch_enabled": True,
        "startup_fetch_limit": 25,
        "startup_fetch_batch_size": 10,
        "startup_rebuild_search": True,
    }
    data.update(overrides)
    return Settings(**data)


def test_startup_sync_fetches_latest_posts_and_rebuilds_when_saved(monkeypatch):
    calls = []

    async def fake_fetch_and_store(**kwargs):
        calls.append(("fetch", kwargs))
        return 25, 2, 23

    monkeypatch.setattr(startup_sync, "fetch_and_store", fake_fetch_and_store)
    monkeypatch.setattr(startup_sync, "rebuild_search_text", lambda database_url, batch_size: calls.append(("text", database_url, batch_size)))
    monkeypatch.setattr(startup_sync, "make_engine", lambda database_url: object())

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def commit(self):
            calls.append(("commit",))

    monkeypatch.setattr(startup_sync, "sessionmaker", lambda **kwargs: lambda: FakeSession())
    monkeypatch.setattr(startup_sync, "rebuild_fts_index", lambda session: calls.append(("fts", session)))

    result = asyncio.run(startup_sync.sync_new_threads_on_startup(make_settings()))

    assert result == (25, 2, 23)
    assert calls[0] == (
        "fetch",
        {
            "limit": 25,
            "offset": 0,
            "update_existing": False,
            "batch_size": 10,
            "fetch_all": False,
            "checkpoint_file": None,
        },
    )
    assert calls[1][0] == "text"
    assert calls[2][0] == "fts"
    assert calls[3] == ("commit",)


def test_startup_sync_skips_rebuild_when_no_new_threads(monkeypatch):
    calls = []

    async def fake_fetch_and_store(**kwargs):
        calls.append(("fetch", kwargs))
        return 25, 0, 25

    monkeypatch.setattr(startup_sync, "fetch_and_store", fake_fetch_and_store)
    monkeypatch.setattr(startup_sync, "rebuild_search_text", lambda *args, **kwargs: calls.append(("text",)))
    monkeypatch.setattr(startup_sync, "rebuild_fts_index", lambda session: calls.append(("fts",)))

    result = asyncio.run(startup_sync.sync_new_threads_on_startup(make_settings()))

    assert result == (25, 0, 25)
    assert [call[0] for call in calls] == ["fetch"]


def test_startup_sync_can_be_disabled(monkeypatch):
    async def fail_fetch_and_store(**kwargs):
        raise AssertionError("disabled startup sync must not fetch")

    monkeypatch.setattr(startup_sync, "fetch_and_store", fail_fetch_and_store)

    result = asyncio.run(startup_sync.sync_new_threads_on_startup(make_settings(startup_fetch_enabled=False)))

    assert result == (0, 0, 0)
