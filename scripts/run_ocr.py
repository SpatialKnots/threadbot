from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.repositories import iter_images_without_ocr
from app.db.session import get_session, init_db
from app.ocr.recognize import OCRBackendUnavailable, OCRRecognitionError, recognize_image_text


logger = logging.getLogger(__name__)


def _join_ocr_chunks(chunks: list[str]) -> str:
    return "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()


def _save_ocr_chunks(posts_by_id, recognized_by_post: dict[int, list[str]], force: bool) -> None:
    for post_id, chunks in recognized_by_post.items():
        joined = _join_ocr_chunks(chunks)
        if not joined:
            continue
        if force:
            posts_by_id[post_id].ocr_text = joined
        else:
            posts_by_id[post_id].ocr_text = _join_ocr_chunks([posts_by_id[post_id].ocr_text, joined])
    recognized_by_post.clear()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OCR for saved images.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum number of images to process. Use 0 for no limit.")
    parser.add_argument("--post-id", type=int, default=None, help="Process images attached to one local post id.")
    parser.add_argument("--force", action="store_true", help="Process images even when the post already has OCR text.")
    parser.add_argument(
        "--max-existing-ocr-length",
        type=int,
        default=None,
        help="Process images whose post OCR text is at most this many characters.",
    )
    parser.add_argument(
        "--min-existing-ocr-length",
        type=int,
        default=None,
        help="Process images whose post OCR text is at least this many characters.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Run OCR and log results without writing to the database.")
    parser.add_argument("--lang", default="rus+eng", help="Tesseract language list, for example rus+eng.")
    parser.add_argument("--psm", type=int, default=6, help="Tesseract page segmentation mode.")
    parser.add_argument("--commit-every", type=int, default=100, help="Commit recognized OCR chunks every N images.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("numexpr").setLevel(logging.WARNING)
    init_db()
    with get_session() as session:
        images = list(
            iter_images_without_ocr(
                session,
                args.limit,
                post_id=args.post_id,
                force=args.force,
                min_existing_ocr_length=args.min_existing_ocr_length,
                max_existing_ocr_length=args.max_existing_ocr_length,
            )
        )
        logger.info("Selected %d image(s) for OCR.", len(images))
        recognized_by_post: dict[int, list[str]] = defaultdict(list)
        recognized_images = 0
        empty_images = 0
        failed_images = 0

        for image in images:
            logger.info("OCR image id=%s post_id=%s path=%s", image.id, image.post_id, image.local_path)
            try:
                text = recognize_image_text(image.local_path, language=args.lang, psm=args.psm)
            except (FileNotFoundError, OCRBackendUnavailable, OCRRecognitionError) as exc:
                failed_images += 1
                logger.error("OCR failed for image id=%s path=%s: %s", image.id, image.local_path, exc)
                continue

            if text:
                recognized_images += 1
                recognized_by_post[image.post_id].append(text)
                logger.info("OCR extracted %d character(s) from image id=%s.", len(text), image.id)
            else:
                empty_images += 1
                logger.info("OCR extracted no text from image id=%s.", image.id)

            processed_images = recognized_images + empty_images + failed_images
            if not args.dry_run and args.commit_every > 0 and processed_images % args.commit_every == 0:
                posts_by_id = {selected_image.post_id: selected_image.post for selected_image in images}
                _save_ocr_chunks(posts_by_id, recognized_by_post, args.force)
                session.commit()
                logger.info("Committed OCR progress after %d processed image(s).", processed_images)

        if args.dry_run:
            session.rollback()
        else:
            posts_by_id = {image.post_id: image.post for image in images}
            _save_ocr_chunks(posts_by_id, recognized_by_post, args.force)
            session.commit()

        logger.info(
            "OCR summary: selected=%d recognized=%d empty=%d failed=%d dry_run=%s",
            len(images),
            recognized_images,
            empty_images,
            failed_images,
            args.dry_run,
        )


if __name__ == "__main__":
    main()
