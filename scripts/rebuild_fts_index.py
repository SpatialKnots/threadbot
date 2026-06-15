from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import make_engine
from app.search.fts import rebuild_fts_index
from scripts.rebuild_search_text import assert_no_active_writer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild SQLite FTS5 index for an explicit database.")
    parser.add_argument("--database-url", required=True, help="Target database URL, for example sqlite:///./copy.db")
    parser.add_argument("--allow-active-writer", action="store_true", help="Bypass SQLite journal safety check.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    assert_no_active_writer(args.database_url, args.allow_active_writer)
    engine = make_engine(args.database_url)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        rebuild_fts_index(session)
        session.commit()
    print("Rebuilt post_search_fts.")


if __name__ == "__main__":
    main()
