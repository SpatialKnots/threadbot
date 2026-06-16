import asyncio

from app.twoch.originals import (
    TwochClient,
    build_2ch_post_url,
    extract_2ch_post_numbers,
    find_original_from_text,
)


def test_extract_2ch_post_numbers_from_ocr_text():
    text = f"Аноним 19/07/20 {chr(0x2116)}224967819\n>>224967890\n#224967819"

    assert extract_2ch_post_numbers(text) == [224967819]


def test_build_2ch_post_url():
    assert build_2ch_post_url("b", 123, 456) == "https://2ch.hk/b/res/123.html#456"


def test_find_original_from_text_uses_parent_thread(monkeypatch):
    async def fake_fetch_post(self, board, post_num):
        assert board == "b"
        assert post_num == 224967819
        return {"result": 1, "num": 224967819, "parent": 224967800, "board": "b"}

    monkeypatch.setattr(TwochClient, "fetch_post", fake_fetch_post)

    original = asyncio.run(
        find_original_from_text(
            "OCR text №224967819",
            client=TwochClient(),
            board_ids=["b"],
        )
    )

    assert original is not None
    assert original.url == "https://2ch.hk/b/res/224967800.html#224967819"
