from app.bot.formatting import format_ocr_debug_messages
from app.db.models import Post


def test_format_ocr_debug_messages_reports_missing_ocr_text():
    post = Post(vk_post_id=1, vk_owner_id=-1, vk_url="https://vk.com/wall-1_1", text="", ocr_text="")

    assert format_ocr_debug_messages(post) == ["OCR diagnostic:\nNo OCR text saved for this thread yet."]


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
    assert all(message.startswith("OCR diagnostic:\n") for message in messages)
    assert all(len(message) <= 80 for message in messages)
