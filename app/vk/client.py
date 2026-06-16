from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from typing import Any

import httpx

from app.config import Settings, get_settings


VK_API_URL = "https://api.vk.com/method"
VK_CLUB_LINK_RE = re.compile(r"^\s*\[club\d+\|[^\]]+\]\s*[-—:]")
PROMOTIONAL_TEXT_MARKERS = (
    "акци",
    "канал",
    "магазин",
    "паблик",
    "подпис",
    "реклам",
    "скидк",
    "сообществ",
)


@dataclass(frozen=True)
class VKPhoto:
    url: str
    width: int | None
    height: int | None


def pick_largest_photo(photo: dict[str, Any]) -> VKPhoto | None:
    sizes = photo.get("sizes") or []
    candidates = [item for item in sizes if item.get("url")]
    if not candidates:
        return None
    best = max(candidates, key=lambda item: (item.get("width") or 0) * (item.get("height") or 0))
    return VKPhoto(url=best["url"], width=best.get("width"), height=best.get("height"))


def extract_post_photos(post: dict[str, Any]) -> list[VKPhoto]:
    photos: list[VKPhoto] = []
    for attachment in post.get("attachments") or []:
        if attachment.get("type") != "photo":
            continue
        photo = pick_largest_photo(attachment.get("photo") or {})
        if photo is not None:
            photos.append(photo)
    return photos


def post_has_original_photos(post: dict[str, Any]) -> bool:
    return bool(extract_post_photos(post))


def is_promotional_text(text: str | None) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    return bool(VK_CLUB_LINK_RE.match(normalized)) and any(
        marker in normalized for marker in PROMOTIONAL_TEXT_MARKERS
    )


def is_promotional_post(post: dict[str, Any]) -> bool:
    if int(post.get("marked_as_ads") or 0) == 1:
        return True
    return is_promotional_text(post.get("text"))


class VKClient:
    def __init__(self, settings: Settings | None = None, timeout: float = 30.0) -> None:
        self.settings = settings or get_settings(require_tokens=True)
        self.timeout = timeout

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = {
            **params,
            "access_token": self.settings.vk_access_token,
            "v": self.settings.vk_api_version,
        }
        async with httpx.AsyncClient(base_url=VK_API_URL, timeout=self.timeout) as client:
            response = await client.get(f"/{method}", params=payload)
            response.raise_for_status()
            data = response.json()
        if "error" in data:
            error = data["error"]
            message = error.get("error_msg", str(error))
            raise RuntimeError(f"VK API error {error.get('error_code')}: {message}")
        return data["response"]

    async def fetch_wall_posts(self, offset: int = 0, count: int = 100) -> list[dict[str, Any]]:
        response = await self._call(
            "wall.get",
            {
                "domain": self.settings.vk_group_domain,
                "offset": offset,
                "count": min(count, 100),
                "extended": 0,
            },
        )
        return list(response.get("items") or [])

    async def fetch_wall_count(self) -> int:
        response = await self._call(
            "wall.get",
            {
                "domain": self.settings.vk_group_domain,
                "offset": 0,
                "count": 1,
                "extended": 0,
            },
        )
        return int(response.get("count") or 0)

    async def fetch_posts_batched(self, limit: int, offset: int = 0, batch_size: int = 100) -> list[dict[str, Any]]:
        posts: list[dict[str, Any]] = []
        current_offset = offset
        while len(posts) < limit:
            count = min(batch_size, limit - len(posts))
            batch = await self.fetch_wall_posts(offset=current_offset, count=count)
            if not batch:
                break
            posts.extend(batch)
            current_offset += len(batch)
            await asyncio.sleep(0.35)
        return posts


def build_vk_url(owner_id: int, post_id: int, domain: str = "vk.com") -> str:
    return f"https://{domain}/wall{owner_id}_{post_id}"
