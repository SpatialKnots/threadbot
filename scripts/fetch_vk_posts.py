from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.db.repositories import ImageInput, PostInput, existing_vk_post_ids, normalize_vk_datetime, upsert_post
from app.db.session import get_session, init_db
from app.vk.client import VKClient, build_vk_url, extract_post_photos
from app.vk.downloader import download_image


LOGGER = logging.getLogger("fetch_vk_posts")


def resolve_total_to_inspect(wall_count: int, offset: int, limit: int | None, fetch_all: bool) -> int:
    if fetch_all:
        total = max(wall_count - offset, 0)
        if limit is not None and limit > 0:
            return min(total, limit)
        return total
    return limit if limit is not None else 100


async def _store_posts(posts: list[dict], update_existing: bool) -> tuple[int, int]:
    saved = 0
    skipped = 0
    with get_session() as session:
        existing_ids = set()
        if not update_existing:
            existing_ids = existing_vk_post_ids(session, (int(post["id"]) for post in posts))
        for post in posts:
            post_id = int(post["id"])
            if post_id in existing_ids:
                skipped += 1
                LOGGER.info("Skipping existing VK post %s.", post_id)
                continue
            photos = extract_post_photos(post)
            if not photos:
                skipped += 1
                continue
            images: list[ImageInput] = []
            for index, photo in enumerate(photos):
                try:
                    local_path = await download_image(photo.url, post_id=post_id, image_index=index)
                except Exception:
                    LOGGER.exception("Failed to download image for VK post %s", post_id)
                    continue
                images.append(
                    ImageInput(
                        vk_photo_url=photo.url,
                        local_path=local_path,
                        width=photo.width,
                        height=photo.height,
                    )
                )
            if not images:
                skipped += 1
                continue
            upsert_post(
                session,
                PostInput(
                    vk_post_id=post_id,
                    vk_owner_id=int(post["owner_id"]),
                    vk_url=build_vk_url(int(post["owner_id"]), post_id),
                    text=post.get("text") or "",
                    published_at=normalize_vk_datetime(post.get("date")),
                    likes_count=(post.get("likes") or {}).get("count", 0),
                    comments_count=(post.get("comments") or {}).get("count", 0),
                    reposts_count=(post.get("reposts") or {}).get("count", 0),
                    views_count=(post.get("views") or {}).get("count", 0),
                    images=tuple(images),
                ),
                update_existing=update_existing,
            )
            saved += 1
        session.commit()
    return saved, skipped


async def fetch_and_store(
    limit: int | None,
    offset: int,
    update_existing: bool,
    batch_size: int = 100,
    fetch_all: bool = False,
    checkpoint_file: Path | None = None,
) -> tuple[int, int, int]:
    settings = get_settings(require_tokens=True)
    init_db()
    client = VKClient(settings)
    wall_count = await client.fetch_wall_count()
    total_to_inspect = resolve_total_to_inspect(wall_count, offset, limit, fetch_all)
    LOGGER.info(
        "VK wall count=%s. Starting import: offset=%s inspect_limit=%s batch_size=%s update_existing=%s.",
        wall_count,
        offset,
        total_to_inspect,
        batch_size,
        update_existing,
    )

    inspected = 0
    saved_total = 0
    skipped_total = 0
    current_offset = offset
    while inspected < total_to_inspect:
        count = min(batch_size, total_to_inspect - inspected)
        posts = await client.fetch_wall_posts(offset=current_offset, count=count)
        if not posts:
            break
        saved, skipped = await _store_posts(posts, update_existing=update_existing)
        inspected += len(posts)
        current_offset += len(posts)
        saved_total += saved
        skipped_total += skipped
        LOGGER.info(
            "Progress: inspected_posts=%s/%s saved_threads=%s/%s skipped_without_images=%s current_offset=%s.",
            inspected,
            total_to_inspect,
            saved_total,
            total_to_inspect,
            skipped_total,
            current_offset,
        )
        if checkpoint_file is not None:
            checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_file.write_text(
                json.dumps(
                    {
                        "offset": current_offset,
                        "inspected": inspected,
                        "saved_threads": saved_total,
                        "skipped_without_images": skipped_total,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        await asyncio.sleep(0.35)
    LOGGER.info(
        "Done. Inspected: %s. Saved or updated: %s. Skipped without usable images: %s.",
        inspected,
        saved_total,
        skipped_total,
    )
    return inspected, saved_total, skipped_total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch VK posts and store thread images.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of VK wall posts to inspect.")
    parser.add_argument("--offset", type=int, default=0, help="VK wall offset.")
    parser.add_argument("--all", action="store_true", help="Inspect all available VK wall posts from offset.")
    parser.add_argument("--batch-size", type=int, default=100, help="VK wall.get batch size. VK max is 100.")
    parser.add_argument("--update-existing", action="store_true", help="Update metadata for existing posts.")
    parser.add_argument("--checkpoint-file", type=Path, default=None, help="Write current VK offset after each batch.")
    parser.add_argument("--resume", action="store_true", help="Start from checkpoint-file offset when it exists.")
    parser.add_argument("--log-file", type=Path, default=None, help="Write fetch progress logs to this file.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    log_kwargs = {
        "level": logging.INFO,
        "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
    }
    if args.log_file is not None:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_kwargs.update({"filename": args.log_file, "encoding": "utf-8"})
    logging.basicConfig(**log_kwargs)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    offset = args.offset
    if args.resume and args.checkpoint_file is not None and args.checkpoint_file.exists():
        data = json.loads(args.checkpoint_file.read_text(encoding="utf-8"))
        offset = int(data.get("offset", offset))
        LOGGER.info("Resuming from checkpoint offset=%s.", offset)
    asyncio.run(
        fetch_and_store(
            args.limit,
            offset,
            args.update_existing,
            batch_size=max(1, min(args.batch_size, 100)),
            fetch_all=args.all,
            checkpoint_file=args.checkpoint_file,
        )
    )


if __name__ == "__main__":
    main()
