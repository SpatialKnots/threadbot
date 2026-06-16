from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base


def make_engine(database_url: str | None = None) -> Engine:
    settings = get_settings(require_tokens=False)
    url = database_url or settings.database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, connect_args=connect_args)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def init_db(target_engine: Engine | None = None) -> None:
    active_engine = target_engine or engine
    Base.metadata.create_all(bind=active_engine)
    if active_engine.dialect.name == "sqlite":
        with active_engine.begin() as connection:
            columns = {column["name"] for column in inspect(connection).get_columns("posts")}
            if "original_url" not in columns:
                connection.execute(text("ALTER TABLE posts ADD COLUMN original_url TEXT NOT NULL DEFAULT ''"))


def get_session() -> Session:
    return SessionLocal()
