from __future__ import annotations

from app.config import get_settings
from app.db.repositories import search_posts as repository_search_posts
from app.db.session import get_session


async def search_posts(query: str, limit: int | None = None, offset: int = 0):
    settings = get_settings(require_tokens=False)
    with get_session() as session:
        return repository_search_posts(session, query, limit or settings.results_per_page, offset)

