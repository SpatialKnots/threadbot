from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import OperationalError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import Post
from app.search.normalization import expand_query_tokens, normalize_search_text, tokenize_search_query


FTS_TABLE = "post_search_fts"
SEARCH_TEXT_COLUMN = "search_text"


@dataclass(frozen=True)
class FTSCandidate:
    post_id: int
    score: float


def is_sqlite_session(session: Session) -> bool:
    return session.bind is not None and session.bind.dialect.name == "sqlite"


def has_search_text_column(session: Session) -> bool:
    if not is_sqlite_session(session):
        return False
    rows = session.execute(text("PRAGMA table_info(posts)")).mappings().all()
    return any(row["name"] == SEARCH_TEXT_COLUMN for row in rows)


def has_fts_index(session: Session) -> bool:
    if not is_sqlite_session(session):
        return False
    row = session.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name"),
        {"name": FTS_TABLE},
    ).first()
    return row is not None


def ensure_search_text_column(session: Session) -> bool:
    if not is_sqlite_session(session):
        raise RuntimeError("search_text migration is currently implemented only for SQLite")
    if has_search_text_column(session):
        return False
    session.execute(text("ALTER TABLE posts ADD COLUMN search_text TEXT NOT NULL DEFAULT ''"))
    return True


def rebuild_fts_index(session: Session) -> None:
    if not is_sqlite_session(session):
        raise RuntimeError("FTS index rebuild is currently implemented only for SQLite")
    if not has_search_text_column(session):
        raise RuntimeError("posts.search_text does not exist. Run rebuild_search_text first.")
    session.execute(text(f"DROP TABLE IF EXISTS {FTS_TABLE}"))
    session.execute(text(f"CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5(search_text, content='')"))
    session.execute(
        text(
            f"""
            INSERT INTO {FTS_TABLE}(rowid, search_text)
            SELECT id, search_text
            FROM posts
            WHERE search_text IS NOT NULL AND search_text != ''
            """
        )
    )


def build_fts_query(query: str, operator: str = "AND") -> str:
    tokens = tokenize_search_query(query)
    terms: list[str] = []
    for group in expand_query_tokens(tokens):
        variants = [normalize_search_text(token) for token in group]
        variants = [token for token in dict.fromkeys(variants) if token]
        if not variants:
            continue
        if len(variants) == 1:
            terms.append(_quote_fts_term(variants[0]))
        else:
            terms.append("(" + " OR ".join(_quote_fts_term(token) for token in variants) + ")")
    joiner = f" {operator} "
    return joiner.join(terms)


def _quote_fts_term(term: str) -> str:
    escaped = term.replace('"', '""')
    return f'"{escaped}"'


def search_fts(session: Session, query: str, limit: int, offset: int = 0) -> list[FTSCandidate]:
    if not has_fts_index(session):
        return []
    exact_candidates = _search_fts_query(session, build_fts_query(query, "AND"), limit, offset)
    if exact_candidates:
        return exact_candidates
    return _search_fts_query(session, build_fts_query(query, "OR"), limit, offset)


def _search_fts_query(session: Session, fts_query: str, limit: int, offset: int) -> list[FTSCandidate]:
    if not fts_query:
        return []
    try:
        rows = session.execute(
            text(
                f"""
                SELECT rowid AS post_id, bm25({FTS_TABLE}) AS score
                FROM {FTS_TABLE}
                WHERE {FTS_TABLE} MATCH :query
                ORDER BY score
                LIMIT :limit OFFSET :offset
                """
            ),
            {"query": fts_query, "limit": limit, "offset": offset},
        ).mappings()
    except OperationalError:
        return []
    return [FTSCandidate(post_id=int(row["post_id"]), score=float(row["score"])) for row in rows]


def fetch_posts_by_ids(session: Session, post_ids: list[int]) -> list[Post]:
    if not post_ids:
        return []
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    posts = session.scalars(
        select(Post).where(Post.id.in_(post_ids)).options(selectinload(Post.images), selectinload(Post.tags))
    ).all()
    by_id = {post.id: post for post in posts}
    return [by_id[post_id] for post_id in post_ids if post_id in by_id]
