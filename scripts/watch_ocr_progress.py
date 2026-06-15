from __future__ import annotations

import argparse
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SELECTED_RE = re.compile(r"Selected (?P<count>\d+) image\(s\) for OCR\.")
COMMITTED_RE = re.compile(r"Committed OCR progress after (?P<count>\d+) processed image\(s\)\.")
CURRENT_RE = re.compile(r"OCR image id=(?P<image_id>\d+) post_id=(?P<post_id>\d+) path=(?P<path>.+)")
EXTRACTED_RE = re.compile(r"OCR extracted (?P<count>\d+) character\(s\) from image id=(?P<image_id>\d+)\.")
EMPTY_RE = re.compile(r"OCR extracted no text from image id=(?P<image_id>\d+)\.")
FAILED_RE = re.compile(r"OCR failed for image id=(?P<image_id>\d+) path=(?P<path>.+): (?P<error>.+)")
SUMMARY_RE = re.compile(
    r"OCR summary: selected=(?P<selected>\d+) recognized=(?P<recognized>\d+) "
    r"empty=(?P<empty>\d+) failed=(?P<failed>\d+) dry_run=(?P<dry_run>\S+)"
)


@dataclass(frozen=True)
class OcrLogProgress:
    selected: int | None = None
    current_image_id: int | None = None
    current_post_id: int | None = None
    current_path: str | None = None
    started_images: int = 0
    recognized_images: int = 0
    empty_images: int = 0
    failed_images: int = 0
    committed_images: int = 0
    summary_seen: bool = False
    last_failure: str | None = None

    @property
    def finished_images(self) -> int:
        return self.recognized_images + self.empty_images + self.failed_images


@dataclass(frozen=True)
class DbProgress:
    posts: int = 0
    images: int = 0
    ocr_posts: int = 0


def parse_ocr_log(log_path: Path) -> OcrLogProgress:
    selected: int | None = None
    current_image_id: int | None = None
    current_post_id: int | None = None
    current_path: str | None = None
    started_images = 0
    recognized_images = 0
    empty_images = 0
    failed_images = 0
    committed_images = 0
    summary_seen = False
    last_failure: str | None = None

    if not log_path.exists():
        return OcrLogProgress()

    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if match := SELECTED_RE.search(line):
                selected = int(match.group("count"))
            elif match := CURRENT_RE.search(line):
                started_images += 1
                current_image_id = int(match.group("image_id"))
                current_post_id = int(match.group("post_id"))
                current_path = match.group("path")
            elif EXTRACTED_RE.search(line):
                recognized_images += 1
            elif EMPTY_RE.search(line):
                empty_images += 1
            elif match := FAILED_RE.search(line):
                failed_images += 1
                last_failure = f"image_id={match.group('image_id')} {match.group('error')}"
            elif match := COMMITTED_RE.search(line):
                committed_images = int(match.group("count"))
            elif match := SUMMARY_RE.search(line):
                selected = int(match.group("selected"))
                recognized_images = int(match.group("recognized"))
                empty_images = int(match.group("empty"))
                failed_images = int(match.group("failed"))
                summary_seen = True

    return OcrLogProgress(
        selected=selected,
        current_image_id=current_image_id,
        current_post_id=current_post_id,
        current_path=current_path,
        started_images=started_images,
        recognized_images=recognized_images,
        empty_images=empty_images,
        failed_images=failed_images,
        committed_images=committed_images,
        summary_seen=summary_seen,
        last_failure=last_failure,
    )


def read_db_progress(database_path: Path) -> DbProgress:
    if not database_path.exists():
        return DbProgress()
    with sqlite3.connect(database_path) as connection:
        cursor = connection.cursor()
        posts = cursor.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        images = cursor.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        ocr_posts = cursor.execute("SELECT COUNT(*) FROM posts WHERE length(ocr_text) > 0").fetchone()[0]
    return DbProgress(posts=posts, images=images, ocr_posts=ocr_posts)


def _percent(value: int, total: int | None) -> str:
    if not total:
        return "n/a"
    return f"{(value / total) * 100:.2f}%"


def _file_state(path: Path) -> str:
    if not path.exists():
        return "missing"
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    age_seconds = max(0, int(time.time() - stat.st_mtime))
    return f"{stat.st_size} bytes, modified {modified}, age {age_seconds}s"


def render_status(progress: OcrLogProgress, db: DbProgress, log_path: Path, database_path: Path) -> str:
    status = "finished" if progress.summary_seen else "running or pending"
    lines = [
        f"time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"status: {status}",
        f"log: {log_path} ({_file_state(log_path)})",
        f"database: {database_path}",
        "",
        "log progress:",
        f"  selected_images: {progress.selected if progress.selected is not None else 'unknown'}",
        f"  started_images: {progress.started_images} ({_percent(progress.started_images, progress.selected)})",
        f"  finished_images: {progress.finished_images} ({_percent(progress.finished_images, progress.selected)})",
        f"  committed_images: {progress.committed_images} ({_percent(progress.committed_images, progress.selected)})",
        f"  recognized_images: {progress.recognized_images}",
        f"  empty_images: {progress.empty_images}",
        f"  failed_images: {progress.failed_images}",
        "",
        "database progress:",
        f"  posts: {db.posts}",
        f"  images: {db.images}",
        f"  posts_with_ocr: {db.ocr_posts} ({_percent(db.ocr_posts, db.posts)})",
    ]
    if progress.current_image_id is not None:
        lines.extend(
            [
                "",
                "current:",
                f"  image_id: {progress.current_image_id}",
                f"  post_id: {progress.current_post_id}",
                f"  path: {progress.current_path}",
            ]
        )
    if progress.last_failure:
        lines.extend(["", "last_failure:", f"  {progress.last_failure}"])
    return "\n".join(lines)


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch OCR progress from the OCR log and SQLite database.")
    parser.add_argument("--log", type=Path, default=Path("ocr_full_stderr.log"))
    parser.add_argument("--database", type=Path, default=Path("threads.db"))
    parser.add_argument("--watch", action="store_true", help="Refresh continuously.")
    parser.add_argument("--interval", type=float, default=10.0, help="Refresh interval in seconds for --watch.")
    parser.add_argument("--no-clear", action="store_true", help="Do not clear the terminal between refreshes.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    while True:
        progress = parse_ocr_log(args.log)
        db = read_db_progress(args.database)
        if args.watch and not args.no_clear:
            clear_screen()
        print(render_status(progress, db, args.log, args.database))
        if not args.watch:
            return
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    main()
