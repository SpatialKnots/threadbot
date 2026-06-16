import asyncio
from types import SimpleNamespace

from app.vk.client import VKClient, build_vk_url, extract_post_photos, is_promotional_post, pick_largest_photo
from app.vk.downloader import image_filename


def test_pick_largest_photo_uses_area():
    photo = {
        "sizes": [
            {"url": "small.jpg", "width": 100, "height": 100},
            {"url": "wide.jpg", "width": 300, "height": 80},
            {"url": "large.jpg", "width": 200, "height": 200},
        ]
    }

    assert pick_largest_photo(photo).url == "large.jpg"


def test_extract_post_photos_ignores_non_photo_attachments():
    post = {
        "attachments": [
            {"type": "video", "video": {"id": 1}},
            {"type": "photo", "photo": {"sizes": [{"url": "image.jpg", "width": 10, "height": 10}]}},
        ]
    }

    photos = extract_post_photos(post)

    assert len(photos) == 1
    assert photos[0].url == "image.jpg"


def test_build_vk_url():
    assert build_vk_url(-1, 42) == "https://vk.com/wall-1_42"


def test_is_promotional_post_detects_vk_ad_flag():
    assert is_promotional_post({"marked_as_ads": 1, "text": "thread text"}) is True


def test_is_promotional_post_detects_club_link_ad_text():
    post = {"text": "[club27725025|PHOTO FILM] - атмосферный и интересный паблик c фотографиями!"}

    assert is_promotional_post(post) is True


def test_is_promotional_post_does_not_reject_plain_thread_text():
    post = {"text": "обычный тред с картинками и обсуждением"}

    assert is_promotional_post(post) is False


def test_image_filename_is_stable_and_keeps_extension():
    first = image_filename(42, 0, "https://example.com/image.png?size=large")
    second = image_filename(42, 0, "https://example.com/image.png?size=large")

    assert first == second
    assert first.startswith("post_42_0_")
    assert first.endswith(".png")


def test_fetch_wall_count_reads_vk_count(monkeypatch):
    async def fake_call(self, method, params):
        assert method == "wall.get"
        assert params["count"] == 1
        return {"count": 123, "items": []}

    monkeypatch.setattr(VKClient, "_call", fake_call)
    client = object.__new__(VKClient)
    client.settings = SimpleNamespace(vk_group_domain="thewebmthread")

    assert asyncio.run(client.fetch_wall_count()) == 123
