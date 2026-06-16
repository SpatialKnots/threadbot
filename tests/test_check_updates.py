import asyncio
from pathlib import Path

from app.check_updates import CheckResult, check_for_new_threads
from app.config import Settings
from app import check_updates


def make_settings():
    return Settings(
        telegram_bot_token="telegram",
        vk_access_token="vk",
        vk_group_domain="group",
        vk_api_version="5.199",
        database_url="sqlite:///:memory:",
        image_storage_path=Path("data/images"),
        results_per_page=5,
        startup_fetch_enabled=True,
        startup_fetch_limit=25,
        startup_fetch_batch_size=10,
        startup_rebuild_search=True,
    )


def test_check_for_new_threads_runs_fetch_ocr_and_rebuild(monkeypatch):
    calls = []

    async def fake_fetch_and_store(**kwargs):
        calls.append(("fetch", kwargs))
        return 25, 2, 23

    monkeypatch.setattr(check_updates, "_empty_ocr_post_ids", lambda: {1, 2})
    monkeypatch.setattr(check_updates, "fetch_and_store", fake_fetch_and_store)
    monkeypatch.setattr(check_updates, "_new_empty_ocr_post_ids", lambda previous: [3, 4])
    monkeypatch.setattr(check_updates, "_run_ocr_for_posts", lambda post_ids: calls.append(("ocr", post_ids)) or (3, 2, 1, 0))
    monkeypatch.setattr(check_updates, "_resolve_originals_for_posts", lambda post_ids: asyncio.sleep(0, (2, 1)))
    monkeypatch.setattr(check_updates, "rebuild_search_text", lambda database_url, batch_size: calls.append(("text", database_url, batch_size)))
    monkeypatch.setattr(check_updates, "make_engine", lambda database_url: object())

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def commit(self):
            calls.append(("commit",))

    monkeypatch.setattr(check_updates, "sessionmaker", lambda **kwargs: lambda: FakeSession())
    monkeypatch.setattr(check_updates, "rebuild_fts_index", lambda session: calls.append(("fts", session)))

    result = asyncio.run(check_for_new_threads(make_settings()))

    assert result == CheckResult(
        inspected=25,
        saved=2,
        skipped=23,
        ocr_selected=3,
        ocr_recognized=2,
        ocr_empty=1,
        ocr_failed=0,
        originals_checked=2,
        originals_found=1,
        search_rebuilt=True,
    )
    assert calls[0][0] == "fetch"
    assert calls[0][1]["limit"] == 25
    assert calls[0][1]["update_existing"] is False
    assert calls[1] == ("ocr", [3, 4])
    assert calls[2][0] == "text"
    assert calls[3][0] == "fts"
    assert calls[4] == ("commit",)


def test_check_for_new_threads_skips_rebuild_without_new_data(monkeypatch):
    calls = []

    async def fake_fetch_and_store(**kwargs):
        calls.append(("fetch", kwargs))
        return 25, 0, 25

    monkeypatch.setattr(check_updates, "_empty_ocr_post_ids", lambda: {1, 2})
    monkeypatch.setattr(check_updates, "fetch_and_store", fake_fetch_and_store)
    monkeypatch.setattr(check_updates, "_new_empty_ocr_post_ids", lambda previous: [])
    monkeypatch.setattr(check_updates, "_run_ocr_for_posts", lambda post_ids: calls.append(("ocr", post_ids)) or (0, 0, 0, 0))
    monkeypatch.setattr(check_updates, "_resolve_originals_for_posts", lambda post_ids: asyncio.sleep(0, (0, 0)))
    monkeypatch.setattr(check_updates, "rebuild_search_text", lambda *args, **kwargs: calls.append(("text",)))

    result = asyncio.run(check_for_new_threads(make_settings()))

    assert result.search_rebuilt is False
    assert [call[0] for call in calls] == ["fetch", "ocr"]
