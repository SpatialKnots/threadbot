import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repositories import ImageInput, PostInput, upsert_post
from scripts import fetch_vk_posts
from scripts.fetch_vk_posts import resolve_total_to_inspect


def make_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_resolve_total_to_inspect_defaults_to_100_without_all():
    assert resolve_total_to_inspect(wall_count=17230, offset=0, limit=None, fetch_all=False) == 100


def test_resolve_total_to_inspect_all_without_limit_uses_remaining_wall_count():
    assert resolve_total_to_inspect(wall_count=17230, offset=0, limit=None, fetch_all=True) == 17230


def test_resolve_total_to_inspect_all_with_limit_uses_safety_cap():
    assert resolve_total_to_inspect(wall_count=17230, offset=0, limit=500, fetch_all=True) == 500


def test_resolve_total_to_inspect_all_respects_offset():
    assert resolve_total_to_inspect(wall_count=17230, offset=100, limit=None, fetch_all=True) == 17130


def test_store_posts_skips_existing_posts_without_downloading(monkeypatch):
    session_factory = make_session_factory()
    with session_factory() as session:
        upsert_post(
            session,
            PostInput(
                vk_post_id=10,
                vk_owner_id=-1,
                vk_url="https://vk.com/wall-1_10",
                text="existing",
                published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                images=(ImageInput("https://example.com/old.jpg", "data/images/old.jpg", 100, 100),),
            ),
        )
        session.commit()

    async def fail_download(*args, **kwargs):
        raise AssertionError("existing posts must not download images")

    monkeypatch.setattr(fetch_vk_posts, "get_session", session_factory)
    monkeypatch.setattr(fetch_vk_posts, "download_image", fail_download)

    saved, skipped = asyncio.run(
        fetch_vk_posts._store_posts(
            [
                {
                    "id": 10,
                    "owner_id": -1,
                    "date": 1704067200,
                    "attachments": [
                        {
                            "type": "photo",
                            "photo": {
                                "sizes": [
                                    {
                                        "url": "https://example.com/new.jpg",
                                        "width": 100,
                                        "height": 100,
                                    }
                                ]
                            },
                        }
                    ],
                }
            ],
            update_existing=False,
        )
    )

    assert (saved, skipped) == (0, 1)


def test_fetch_and_store_returns_import_statistics(monkeypatch):
    class FakeClient:
        def __init__(self, settings):
            pass

        async def fetch_wall_count(self):
            return 1

        async def fetch_wall_posts(self, offset=0, count=100):
            return [{"id": 10, "owner_id": -1, "attachments": []}]

    monkeypatch.setattr(fetch_vk_posts, "get_settings", lambda require_tokens=True: object())
    monkeypatch.setattr(fetch_vk_posts, "init_db", lambda: None)
    monkeypatch.setattr(fetch_vk_posts, "VKClient", FakeClient)
    monkeypatch.setattr(fetch_vk_posts, "_store_posts", lambda posts, update_existing: asyncio.sleep(0, (0, len(posts))))

    result = asyncio.run(
        fetch_vk_posts.fetch_and_store(
            limit=1,
            offset=0,
            update_existing=False,
            batch_size=100,
        )
    )

    assert result == (1, 0, 1)
