from sqlalchemy import create_engine, inspect, text

from app.db.session import init_db


def test_init_db_adds_original_url_to_existing_sqlite_posts_table():
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE posts (
                    id INTEGER PRIMARY KEY,
                    vk_post_id INTEGER NOT NULL,
                    vk_owner_id INTEGER NOT NULL,
                    vk_url TEXT NOT NULL,
                    text TEXT NOT NULL,
                    ocr_text TEXT NOT NULL,
                    likes_count INTEGER NOT NULL,
                    comments_count INTEGER NOT NULL,
                    reposts_count INTEGER NOT NULL,
                    views_count INTEGER NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )

    init_db(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("posts")}
    assert "original_url" in columns
