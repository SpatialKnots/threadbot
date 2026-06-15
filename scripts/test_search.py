from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.db.repositories import search_post_results
from app.db.session import make_engine


DEFAULT_QUERIES = [
    "батя",
    "батя на кухне",
    "папа после работы",
    "общага",
    "пиво",
    "странный мужик",
]


def snippet(text: str, max_len: int = 140) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 3] + "..."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print diagnostic search results without Telegram.")
    parser.add_argument("--database-url", default=None, help="Database URL. Defaults to configured DATABASE_URL.")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("queries", nargs="*", help="Queries to test. Defaults to a small Russian smoke set.")
    return parser.parse_args()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    database_url = args.database_url or get_settings(require_tokens=False).database_url
    engine = make_engine(database_url)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    queries = args.queries or DEFAULT_QUERIES
    with Session() as session:
        for query in queries:
            print(f"QUERY: {query}")
            results = search_post_results(session, query, limit=max(1, args.limit))
            if not results:
                print("  no results")
                print()
                continue
            for index, result in enumerate(results, start=1):
                post = result.post
                tags = ", ".join(tag.name for tag in post.tags) or "-"
                text = snippet(post.text or post.ocr_text or "")
                print(
                    f"{index}. post_id={post.id} vk_post_id={post.vk_post_id} "
                    f"score={result.score:.3f} source={result.source}"
                )
                print(f"   tags: {tags}")
                print(f"   text: {text}")
            print()


if __name__ == "__main__":
    main()
