from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import make_engine
from app.search.fts import has_search_text_column
from app.search.semantic import (
    IDS_PATH,
    INDEX_PATH,
    SemanticSearchUnavailable,
    encode_passages,
    load_model,
    save_index,
)
from scripts.rebuild_search_text import assert_no_active_writer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build FAISS semantic index from posts.search_text.")
    parser.add_argument("--database-url", required=True, help="Source database URL, for example sqlite:///./threads.db")
    parser.add_argument("--index-path", default=str(INDEX_PATH))
    parser.add_argument("--ids-path", default=str(IDS_PATH))
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--allow-active-writer", action="store_true", help="Bypass SQLite journal safety check.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    assert_no_active_writer(args.database_url, args.allow_active_writer)
    engine = make_engine(args.database_url)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        if not has_search_text_column(session):
            raise RuntimeError("posts.search_text does not exist. Run rebuild_search_text first.")
        rows = session.execute(
            text(
                """
                SELECT id, search_text
                FROM posts
                WHERE search_text IS NOT NULL AND search_text != ''
                ORDER BY id
                """
            )
        ).mappings().all()
    post_ids = [int(row["id"]) for row in rows]
    texts = [str(row["search_text"]) for row in rows]
    if not texts:
        raise RuntimeError("No non-empty search_text rows found.")
    try:
        model = load_model(local_files_only=False)
        embeddings = encode_passages(model, texts, batch_size=max(1, args.batch_size))
        save_index(embeddings, post_ids, Path(args.index_path), Path(args.ids_path))
    except SemanticSearchUnavailable as exc:
        raise RuntimeError(str(exc)) from exc
    print(f"Built FAISS index for {len(post_ids)} posts.")


if __name__ == "__main__":
    main()
