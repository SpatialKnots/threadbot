from __future__ import annotations

import logging

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.session import make_engine
from app.search.fts import rebuild_fts_index
from scripts.fetch_vk_posts import fetch_and_store
from scripts.rebuild_search_text import rebuild_search_text


LOGGER = logging.getLogger(__name__)


async def sync_new_threads_on_startup(settings: Settings) -> tuple[int, int, int]:
    if not settings.startup_fetch_enabled:
        LOGGER.info("Startup VK sync is disabled.")
        return 0, 0, 0

    LOGGER.info(
        "Startup VK sync: inspecting latest %s wall post(s), batch_size=%s.",
        settings.startup_fetch_limit,
        settings.startup_fetch_batch_size,
    )
    try:
        inspected, saved, skipped = await fetch_and_store(
            limit=max(0, settings.startup_fetch_limit),
            offset=0,
            update_existing=False,
            batch_size=max(1, min(settings.startup_fetch_batch_size, 100)),
            fetch_all=False,
            checkpoint_file=None,
        )
    except (RuntimeError, OSError, httpx.HTTPError, SQLAlchemyError) as exc:
        LOGGER.exception("Startup VK sync failed; continuing with existing local database: %s", exc)
        return 0, 0, 0

    LOGGER.info("Startup VK sync done: inspected=%s saved=%s skipped=%s.", inspected, saved, skipped)
    if saved > 0 and settings.startup_rebuild_search:
        LOGGER.info("Rebuilding search_text and FTS after importing %s new thread(s).", saved)
        rebuild_search_text(settings.database_url, batch_size=500)
        engine = make_engine(settings.database_url)
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as session:
            rebuild_fts_index(session)
            session.commit()
        LOGGER.info("Search artifacts rebuilt after startup VK sync.")
    return inspected, saved, skipped
