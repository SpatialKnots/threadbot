import asyncio
from types import SimpleNamespace

from app.bot import handlers
from app.db.models import Image, Post


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


def test_image_to_text_callback_sends_ocr_text(monkeypatch):
    sent_messages = []

    class FakeMessage:
        async def answer(self, text):
            sent_messages.append(text)

    class FakeCallback:
        data = "ocr:7"
        message = FakeMessage()

        async def answer(self, text=None):
            self.answer_text = text

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, model, post_id):
            assert model is Post
            assert post_id == 7
            return Post(
                id=post_id,
                vk_post_id=1,
                vk_owner_id=-1,
                vk_url="https://vk.com/wall-1_1",
                text="",
                ocr_text="recognized text",
            )

    callback = FakeCallback()
    monkeypatch.setattr(handlers, "get_session", lambda: FakeSession())

    asyncio.run(handlers.image_to_text_callback(callback))

    assert sent_messages == ["Image-to-text:\nrecognized text"]
    assert callback.answer_text is None


def test_start_text_message_sends_welcome_without_search(monkeypatch):
    sent_messages = []

    class FakeMessage:
        text = "START"

        async def answer(self, text, reply_markup=None):
            sent_messages.append((text, reply_markup))

    async def fail_search(message, query):
        raise AssertionError("START button must not run text search")

    monkeypatch.setattr(handlers, "handle_text_search", fail_search)

    asyncio.run(handlers.text_message(FakeMessage()))

    assert len(sent_messages) == 1
    assert sent_messages[0][0] == handlers.WELCOME_TEXT
    assert sent_messages[0][1].keyboard[0][0].text == "START"


def test_send_post_does_not_block_on_lazy_original_lookup(monkeypatch):
    sent = []

    class FakeMessage:
        async def answer_photo(self, photo, caption=None, reply_markup=None):
            sent.append((photo.path, caption, reply_markup))

    async def fail_find_original_from_text(text, number_limit=5):
        raise AssertionError("sending a post must not wait for external 2ch lookup")

    monkeypatch.setattr(handlers, "find_original_from_text", fail_find_original_from_text)
    post = Post(
        id=1,
        vk_post_id=1,
        vk_owner_id=-1,
        vk_url="https://vk.com/wall-1_1",
        text="",
        ocr_text="#300309226",
    )
    post.images = [Image(id=1, post_id=1, vk_photo_url="https://example.com/1.jpg", local_path="data/images/1.jpg")]

    asyncio.run(handlers._send_post(FakeMessage(), post))

    assert len(sent) == 1
    assert sent[0][0] == "data/images/1.jpg"
