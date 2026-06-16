import asyncio
from types import SimpleNamespace

from app.bot import handlers
from app.db.models import Post


def test_ensure_original_url_skips_posts_without_2ch_number(monkeypatch):
    async def fail_find_original_from_text(text):
        raise AssertionError("2ch lookup must not run without post numbers")

    monkeypatch.setattr(handlers, "find_original_from_text", fail_find_original_from_text)
    post = Post(id=1, vk_post_id=1, vk_owner_id=-1, vk_url="https://vk.com/wall-1_1", text="", ocr_text="no number")

    result = asyncio.run(handlers._ensure_original_url(post))

    assert result == ""


def test_ensure_original_url_saves_found_2ch_original(monkeypatch):
    saved = {}

    async def fake_find_original_from_text(text, number_limit=5):
        return SimpleNamespace(url="https://2ch.hk/b/res/123.html#456")

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, model, post_id):
            saved["post"] = Post(
                id=post_id,
                vk_post_id=1,
                vk_owner_id=-1,
                vk_url="https://vk.com/wall-1_1",
                text="",
                ocr_text="#123456",
            )
            return saved["post"]

        def commit(self):
            saved["committed"] = True

    monkeypatch.setattr(handlers, "find_original_from_text", fake_find_original_from_text)
    monkeypatch.setattr(handlers, "get_session", lambda: FakeSession())
    post = Post(id=1, vk_post_id=1, vk_owner_id=-1, vk_url="https://vk.com/wall-1_1", text="", ocr_text="#123456")

    result = asyncio.run(handlers._ensure_original_url(post))

    assert result == "https://2ch.hk/b/res/123.html#456"
    assert post.original_url == "https://2ch.hk/b/res/123.html#456"
    assert saved["post"].original_url == "https://2ch.hk/b/res/123.html#456"
    assert saved["committed"] is True


def test_ensure_original_url_times_out_without_blocking(monkeypatch):
    async def slow_find_original_from_text(text, number_limit=5):
        await asyncio.sleep(0.05)
        return SimpleNamespace(url="https://2ch.hk/b/res/123.html#456")

    monkeypatch.setattr(handlers, "find_original_from_text", slow_find_original_from_text)
    monkeypatch.setattr(handlers, "LAZY_ORIGINAL_LOOKUP_TIMEOUT_SECONDS", 0.001)
    post = Post(id=1, vk_post_id=1, vk_owner_id=-1, vk_url="https://vk.com/wall-1_1", text="", ocr_text="#123456")

    result = asyncio.run(handlers._ensure_original_url(post))

    assert result == ""
    assert post.original_url in {"", None}
