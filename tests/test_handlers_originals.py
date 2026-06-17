import asyncio
from types import SimpleNamespace

from app.bot import handlers
from app.bot.keyboards import HELP_BUTTON, LATEST_BUTTON, RANDOM_BUTTON, SEARCH_BUTTON
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

        def commit(self):
            calls.append("commit")

        def commit(self):
            calls.append("commit")

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

    assert len(sent_messages) == 2
    assert sent_messages[0][0] == handlers.WELCOME_TEXT
    assert sent_messages[0][1].keyboard[0][0].text == SEARCH_BUTTON
    assert sent_messages[1][1].inline_keyboard[0][0].callback_data == "search_help"


def test_inline_query_disabled_answers_empty(monkeypatch):
    answers = []

    class FakeInlineQuery:
        query = "батя"

        async def answer(self, results, cache_time=None, is_personal=None):
            answers.append((results, cache_time, is_personal))

    monkeypatch.setenv("THREADBOT_ENABLE_INLINE", "false")

    asyncio.run(handlers.inline_query(FakeInlineQuery()))

    assert answers == [([], 5, True)]


def test_inline_query_returns_text_articles(monkeypatch):
    answers = []

    class FakeInlineQuery:
        query = "батя"

        async def answer(self, results, cache_time=None, is_personal=None):
            answers.append((results, cache_time, is_personal))

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    post = Post(
        id=7,
        vk_post_id=100,
        vk_owner_id=-1,
        vk_url="https://vk.com/wall-1_100",
        text="батя чинит роутер",
        ocr_text="батя чинит роутер на кухне",
    )

    monkeypatch.setenv("THREADBOT_ENABLE_INLINE", "true")
    monkeypatch.setattr(handlers, "get_session", lambda: FakeSession())
    monkeypatch.setattr(handlers, "search_posts", lambda session, query, limit, offset: [post])

    asyncio.run(handlers.inline_query(FakeInlineQuery()))

    results, cache_time, is_personal = answers[0]
    assert cache_time == 30
    assert is_personal is True
    assert len(results) == 1
    assert results[0].id == "7"
    assert results[0].title == "батя чинит роутер"
    assert "https://vk.com/wall-1_100" in results[0].input_message_content.message_text


def test_reply_action_buttons_do_not_run_text_search(monkeypatch):
    calls = []
    sent_messages = []

    class FakeMessage:
        text = ""

        async def answer(self, text, reply_markup=None):
            sent_messages.append(text)

    async def fail_search(message, query):
        raise AssertionError("action buttons must not run text search")

    async def fake_random(message):
        calls.append("random")

    async def fake_latest(message):
        calls.append("latest")

    monkeypatch.setattr(handlers, "handle_text_search", fail_search)
    monkeypatch.setattr(handlers, "random_command", fake_random)
    monkeypatch.setattr(handlers, "latest_command", fake_latest)

    message = FakeMessage()

    message.text = SEARCH_BUTTON
    asyncio.run(handlers.text_message(message))
    message.text = RANDOM_BUTTON
    asyncio.run(handlers.text_message(message))
    message.text = LATEST_BUTTON
    asyncio.run(handlers.text_message(message))
    message.text = HELP_BUTTON
    asyncio.run(handlers.text_message(message))

    assert sent_messages == [handlers.SEARCH_HELP_TEXT, handlers.HELP_TEXT]
    assert calls == ["random", "latest"]


def test_favorite_callback_adds_favorite(monkeypatch):
    calls = []

    class FakeUser:
        id = 42

    class FakeCallback:
        data = "fav:add:7"
        from_user = FakeUser()

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
            return Post(id=post_id, vk_post_id=1, vk_owner_id=-1, vk_url="https://vk.com/wall-1_1", text="")

        def commit(self):
            calls.append("commit")

    def fake_add_favorite(session, user_id, post_id):
        calls.append((user_id, post_id))

    def fake_add_search_event(session, user_id, post_id, event_type, query=None):
        calls.append((user_id, post_id, event_type, query))

    monkeypatch.setattr(handlers, "get_session", lambda: FakeSession())
    monkeypatch.setattr(handlers, "add_favorite", fake_add_favorite)
    monkeypatch.setattr(handlers, "add_search_event", fake_add_search_event)

    callback = FakeCallback()
    asyncio.run(handlers.favorite_callback(callback))

    assert calls == [(42, 7), (42, 7, "favorite_added", None), "commit"]
    assert callback.answer_text == "Добавлено в избранное"


def test_favorite_callback_removes_favorite(monkeypatch):
    calls = []

    class FakeUser:
        id = 42

    class FakeCallback:
        data = "fav:remove:7"
        from_user = FakeUser()

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
            return Post(id=post_id, vk_post_id=1, vk_owner_id=-1, vk_url="https://vk.com/wall-1_1", text="")

        def commit(self):
            calls.append("commit")

    def fake_remove_favorite(session, user_id, post_id):
        calls.append((user_id, post_id))

    monkeypatch.setattr(handlers, "get_session", lambda: FakeSession())
    monkeypatch.setattr(handlers, "remove_favorite", fake_remove_favorite)

    callback = FakeCallback()
    asyncio.run(handlers.favorite_callback(callback))

    assert calls == [(42, 7), "commit"]
    assert callback.answer_text == "Удалено из избранного"


def test_favorites_command_sends_empty_message(monkeypatch):
    sent_messages = []

    class FakeUser:
        id = 42

    class FakeMessage:
        from_user = FakeUser()

        async def answer(self, text, reply_markup=None):
            sent_messages.append(text)

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(handlers, "get_session", lambda: FakeSession())
    monkeypatch.setattr(handlers, "get_favorite_posts", lambda session, user_id, limit: [])

    asyncio.run(handlers.favorites_command(FakeMessage()))

    assert sent_messages == ["У тебя пока нет избранных тредов"]


def test_similar_callback_sends_first_similar_post(monkeypatch):
    calls = []

    class FakeUser:
        id = 42

    class FakeMessage:
        pass

    class FakeCallback:
        data = "similar:7"
        from_user = FakeUser()
        message = FakeMessage()

        async def answer(self, text=None):
            self.answer_text = text

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def commit(self):
            calls.append("commit")

    async def fake_send_post(message, post, index=0, total=1, query_id=0, favorite_action="add"):
        calls.append((message, post.id, index, total, query_id, favorite_action))

    post = Post(id=8, vk_post_id=1, vk_owner_id=-1, vk_url="https://vk.com/wall-1_1", text="")

    monkeypatch.setattr(handlers, "get_session", lambda: FakeSession())
    monkeypatch.setattr(handlers, "find_similar_posts", lambda session, post_id, limit: [post])
    monkeypatch.setattr(handlers, "_send_post", fake_send_post)
    monkeypatch.setattr(
        handlers,
        "add_search_event",
        lambda session, user_id, post_id, event_type, query=None: calls.append(
            (user_id, post_id, event_type, query)
        ),
    )

    callback = FakeCallback()
    asyncio.run(handlers.similar_callback(callback))

    assert calls == [
        (42, 7, "similar_clicked", None),
        "commit",
        (callback.message, 8, 0, 1, handlers.SIMILAR_QUERY_ID, "add"),
    ]
    assert handlers.user_search_state[42].results == [8]
    assert callback.answer_text is None


def test_similar_callback_answers_when_empty(monkeypatch):
    class FakeUser:
        id = 42

    class FakeMessage:
        pass

    class FakeCallback:
        data = "similar:7"
        from_user = FakeUser()
        message = FakeMessage()

        async def answer(self, text=None):
            self.answer_text = text

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(handlers, "get_session", lambda: FakeSession())
    monkeypatch.setattr(handlers, "find_similar_posts", lambda session, post_id, limit: [])

    callback = FakeCallback()
    asyncio.run(handlers.similar_callback(callback))

    assert callback.answer_text == "Похожие треды не найдены"


def test_feedback_bad_callback_records_disliked_event(monkeypatch):
    calls = []

    class FakeUser:
        id = 42

    class FakeCallback:
        data = "feedback:bad:7"
        from_user = FakeUser()

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
            return Post(id=post_id, vk_post_id=1, vk_owner_id=-1, vk_url="https://vk.com/wall-1_1", text="")

        def commit(self):
            calls.append("commit")

    def fake_add_search_event(session, user_id, post_id, event_type, query=None):
        calls.append((user_id, post_id, event_type, query))

    monkeypatch.setattr(handlers, "get_session", lambda: FakeSession())
    monkeypatch.setattr(handlers, "add_search_event", fake_add_search_event)

    callback = FakeCallback()
    asyncio.run(handlers.feedback_bad_callback(callback))

    assert calls == [(42, 7, "disliked", None), "commit"]
    assert callback.answer_text == "Понял, буду показывать меньше похожего"


def test_tag_command_requires_admin(monkeypatch):
    sent_messages = []

    class FakeUser:
        id = 100

    class FakeMessage:
        text = "/tag 7 батя"
        from_user = FakeUser()

        async def answer(self, text, reply_markup=None):
            sent_messages.append(text)

    monkeypatch.setattr(handlers, "is_admin", lambda user_id: False)

    asyncio.run(handlers.tag_command(FakeMessage()))

    assert sent_messages == ["Admin only."]


def test_tag_command_adds_tags_for_admin(monkeypatch):
    sent_messages = []
    calls = []

    class FakeUser:
        id = 42

    class FakeMessage:
        text = "/tag 7 батя техника"
        from_user = FakeUser()

        async def answer(self, text, reply_markup=None):
            sent_messages.append(text)

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, model, post_id):
            assert model is Post
            assert post_id == 7
            return Post(id=post_id, vk_post_id=1, vk_owner_id=-1, vk_url="https://vk.com/wall-1_1", text="")

        def commit(self):
            calls.append("commit")

    def fake_add_tags_to_post(session, post_id, tag_names):
        calls.append((post_id, tag_names))
        return [SimpleNamespace(name=name) for name in tag_names]

    monkeypatch.setattr(handlers, "is_admin", lambda user_id: True)
    monkeypatch.setattr(handlers, "get_session", lambda: FakeSession())
    monkeypatch.setattr(handlers, "add_tags_to_post", fake_add_tags_to_post)

    asyncio.run(handlers.tag_command(FakeMessage()))

    assert calls == [(7, ["батя", "техника"]), "commit"]
    assert sent_messages == ["Added tags: батя, техника"]


def test_untag_command_removes_tags_for_admin(monkeypatch):
    sent_messages = []
    calls = []

    class FakeUser:
        id = 42

    class FakeMessage:
        text = "/untag 7 техника"
        from_user = FakeUser()

        async def answer(self, text, reply_markup=None):
            sent_messages.append(text)

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, model, post_id):
            assert model is Post
            assert post_id == 7
            return Post(id=post_id, vk_post_id=1, vk_owner_id=-1, vk_url="https://vk.com/wall-1_1", text="")

        def commit(self):
            calls.append("commit")

    def fake_remove_tags_from_post(session, post_id, tag_names):
        calls.append((post_id, tag_names))
        return [SimpleNamespace(name="техника")]

    monkeypatch.setattr(handlers, "is_admin", lambda user_id: True)
    monkeypatch.setattr(handlers, "get_session", lambda: FakeSession())
    monkeypatch.setattr(handlers, "remove_tags_from_post", fake_remove_tags_from_post)

    asyncio.run(handlers.untag_command(FakeMessage()))

    assert calls == [(7, ["техника"]), "commit"]
    assert sent_messages == ["Removed tags: техника"]


def test_tags_command_shows_tags(monkeypatch):
    sent_messages = []

    class FakeMessage:
        text = "/tags 7"
        from_user = None

        async def answer(self, text, reply_markup=None):
            sent_messages.append(text)

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, model, post_id):
            assert model is Post
            assert post_id == 7
            return Post(id=post_id, vk_post_id=1, vk_owner_id=-1, vk_url="https://vk.com/wall-1_1", text="")

    monkeypatch.setattr(handlers, "get_session", lambda: FakeSession())
    monkeypatch.setattr(
        handlers,
        "get_post_tags",
        lambda session, post_id: [SimpleNamespace(name="батя"), SimpleNamespace(name="техника")],
    )

    asyncio.run(handlers.tags_command(FakeMessage()))

    assert sent_messages == ["Tags: батя, техника"]


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
