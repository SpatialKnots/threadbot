from app.bot.formatting import format_ocr_debug_messages, format_post_caption
from app.db.models import Post


def test_format_ocr_debug_messages_reports_missing_ocr_text():
    post = Post(vk_post_id=1, vk_owner_id=-1, vk_url="https://vk.com/wall-1_1", text="", ocr_text="")

    assert format_ocr_debug_messages(post) == ["Image-to-text:\nNo OCR text saved for this thread yet."]


def test_format_ocr_debug_messages_splits_long_text():
    post = Post(
        vk_post_id=1,
        vk_owner_id=-1,
        vk_url="https://vk.com/wall-1_1",
        text="",
        ocr_text="first line\n" + "word " * 40,
    )

    messages = format_ocr_debug_messages(post, max_len=80)

    assert len(messages) > 1
    assert all(message.startswith("Image-to-text:\n") for message in messages)
    assert all(len(message) <= 80 for message in messages)


def test_format_post_caption_does_not_fallback_to_ocr_text():
    post = Post(vk_post_id=1, vk_owner_id=-1, vk_url="https://vk.com/wall-1_1", text="", ocr_text="recognized")

    caption = format_post_caption(post)

    assert "recognized" not in caption
    assert "Text:\nNo text saved." in caption


def test_format_post_caption_includes_2ch_original_when_available():
    post = Post(
        vk_post_id=1,
        vk_owner_id=-1,
        vk_url="https://vk.com/wall-1_1",
        original_url="https://2ch.hk/b/res/123.html#456",
        text="",
        ocr_text="recognized",
    )

    caption = format_post_caption(post)

    assert "Original 2ch:\nhttps://2ch.hk/b/res/123.html#456" in caption
    assert "VK source:\nhttps://vk.com/wall-1_1" in caption
