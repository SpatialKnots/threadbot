from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.models import Image, Post
from app.db.session import get_session, make_engine
from app.ocr.recognize import OCRBackendUnavailable, OCRRecognitionError, recognize_image_text
from app.search.fts import rebuild_fts_index
from scripts.fetch_vk_posts import fetch_and_store
from scripts.rebuild_search_text import rebuild_search_text
from scripts.run_ocr import _join_ocr_chunks


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckResult:
    inspected: int
    saved: int
    skipped: int
    ocr_selected: int
    ocr_recognized: int
    ocr_empty: int
    ocr_failed: int
    search_rebuilt: bool


def _empty_ocr_post_ids() -> set[int]:
    with get_session() as session:
        return set(session.scalars(select(Post.id).where(func.length(Post.ocr_text) == 0)).all())


def _new_empty_ocr_post_ids(previous_empty_ids: set[int]) -> list[int]:
    with get_session() as session:
        ids = session.scalars(
            select(Post.id).where(func.length(Post.ocr_text) == 0, ~Post.id.in_(previous_empty_ids)).order_by(Post.id)
        ).all()
        return list(ids)


def _run_ocr_for_posts(post_ids: list[int], language: str = "rus+eng", psm: int = 6) -> tuple[int, int, int, int]:
    if not post_ids:
        return 0, 0, 0, 0

    selected_images = 0
    recognized_images = 0
    empty_images = 0
    failed_images = 0
    recognized_by_post: dict[int, list[str]] = defaultdict(list)

    with get_session() as session:
        images = list(
            session.scalars(
                select(Image)
                .join(Image.post)
                .where(Image.post_id.in_(post_ids))
                .order_by(Image.id)
            )
        )
        selected_images = len(images)
        posts_by_id = {post.id: post for post in session.scalars(select(Post).where(Post.id.in_(post_ids))).all()}
        for image in images:
            LOGGER.info("Startup check OCR image id=%s post_id=%s path=%s", image.id, image.post_id, image.local_path)
            try:
                text = recognize_image_text(image.local_path, language=language, psm=psm)
            except (FileNotFoundError, OCRBackendUnavailable, OCRRecognitionError) as exc:
                failed_images += 1
                LOGGER.error("Startup check OCR failed for image id=%s path=%s: %s", image.id, image.local_path, exc)
                continue
            if text:
                recognized_images += 1
                recognized_by_post[image.post_id].append(text)
            else:
                empty_images += 1

        for post_id, chunks in recognized_by_post.items():
            joined = _join_ocr_chunks(chunks)
            if joined and post_id in posts_by_id:
                posts_by_id[post_id].ocr_text = joined
        session.commit()

    return selected_images, recognized_images, empty_images, failed_images


async def check_for_new_threads(settings: Settings) -> CheckResult:
    previous_empty_ids = _empty_ocr_post_ids()
    inspected, saved, skipped = await fetch_and_store(
        limit=max(0, settings.startup_fetch_limit),
        offset=0,
        update_existing=False,
        batch_size=max(1, min(settings.startup_fetch_batch_size, 100)),
        fetch_all=False,
        checkpoint_file=None,
    )

    new_empty_post_ids = _new_empty_ocr_post_ids(previous_empty_ids)
    ocr_selected, ocr_recognized, ocr_empty, ocr_failed = _run_ocr_for_posts(new_empty_post_ids)

    search_rebuilt = False
    if saved > 0 or ocr_recognized > 0:
        rebuild_search_text(settings.database_url, batch_size=500)
        engine = make_engine(settings.database_url)
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as session:
            rebuild_fts_index(session)
            session.commit()
        search_rebuilt = True

    return CheckResult(
        inspected=inspected,
        saved=saved,
        skipped=skipped,
        ocr_selected=ocr_selected,
        ocr_recognized=ocr_recognized,
        ocr_empty=ocr_empty,
        ocr_failed=ocr_failed,
        search_rebuilt=search_rebuilt,
    )
