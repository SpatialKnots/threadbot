from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import selectinload, sessionmaker
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.models import Post
from app.db.session import make_engine
from app.search.fts import ensure_search_text_column
from app.search.indexing import build_post_search_text


def sqlite_path_from_url(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    return Path(database_url[len(prefix) :]).resolve()


def assert_no_active_writer(database_url: str, allow_active_writer: bool) -> None:
    sqlite_path = sqlite_path_from_url(database_url)
    if sqlite_path is None or allow_active_writer:
        return
    journal_path = Path(str(sqlite_path) + "-journal")
    if journal_path.exists():
        raise RuntimeError(
            f"{journal_path} exists. The database may be in use; rerun only after import finishes "
            "or pass --allow-active-writer for an intentional maintenance window."
        )


def rebuild_search_text(database_url: str, batch_size: int) -> int:
    engine = make_engine(database_url)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    total = 0
    last_id = 0
    with Session() as session:
        ensure_search_text_column(session)
        while True:
            posts = list(
                session.scalars(
                    select(Post)
                    .where(Post.id > last_id)
                    .order_by(Post.id)
                    .limit(batch_size)
                    .options(selectinload(Post.tags))
                )
            )
            if not posts:
                break
            for post in posts:
                session.execute(
                    text("UPDATE posts SET search_text = :search_text WHERE id = :post_id"),
                    {"search_text": build_post_search_text(post), "post_id": post.id},
                )
                last_id = post.id
                total += 1
            session.commit()
    return total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build materialized posts.search_text for an explicit database.")
    parser.add_argument("--database-url", required=True, help="Target database URL, for example sqlite:///./copy.db")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--allow-active-writer", action="store_true", help="Bypass SQLite journal safety check.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    assert_no_active_writer(args.database_url, args.allow_active_writer)
    total = rebuild_search_text(args.database_url, max(1, args.batch_size))
    print(f"Updated search_text for {total} posts.")


if __name__ == "__main__":
    main()
