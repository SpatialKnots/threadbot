from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import get_settings


def _extension_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return suffix
    return ".jpg"


def image_filename(vk_post_id: int, image_index: int, url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"post_{vk_post_id}_{image_index}_{digest}{_extension_from_url(url)}"


async def download_image(url: str, post_id: int, image_index: int = 0, storage_path: Path | None = None) -> str:
    root = storage_path or get_settings(require_tokens=False).image_storage_path
    root.mkdir(parents=True, exist_ok=True)
    target = root / image_filename(post_id, image_index, url)
    if target.exists() and target.stat().st_size > 0:
        return str(target)

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "image" not in content_type.lower():
            raise RuntimeError(f"Downloaded URL is not an image: {url}")
        target.write_bytes(response.content)
    return str(target)

