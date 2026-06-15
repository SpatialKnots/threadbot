from datetime import datetime, timezone
import os

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Tag
from app.db.repositories import (
    ImageInput,
    PostInput,
    get_latest_posts,
    iter_images_without_ocr,
    search_post_results,
    search_posts,
    upsert_post,
)
from app.search.fts import ensure_search_text_column, rebuild_fts_index
from app.search.indexing import build_post_search_text, build_search_text


os.environ["THREADBOT_SEMANTIC_SEARCH"] = "0"


def make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_upsert_post_deduplicates_by_vk_post_id_and_updates_counters():
    session = make_session()
    data = PostInput(
        vk_post_id=10,
        vk_owner_id=-1,
        vk_url="https://vk.com/wall-1_10",
        text="first text",
        published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        likes_count=1,
        images=(ImageInput("https://example.com/a.jpg", "data/images/a.jpg", 100, 100),),
    )

    first = upsert_post(session, data)
    second = upsert_post(session, PostInput(**{**data.__dict__, "likes_count": 5, "text": "updated"}))
    session.commit()

    assert first.id == second.id
    assert second.likes_count == 5
    assert second.text == "updated"
    assert len(second.images) == 1


def test_upsert_post_preserves_multiple_images_for_one_post():
    session = make_session()

    post = upsert_post(
        session,
        PostInput(
            vk_post_id=11,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_11",
            text="multi image",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(
                ImageInput("https://example.com/a.jpg", "data/images/a.jpg", 100, 100),
                ImageInput("https://example.com/b.jpg", "data/images/b.jpg", 200, 200),
                ImageInput("https://example.com/c.jpg", "data/images/c.jpg", 300, 300),
            ),
        ),
    )
    session.commit()

    assert [image.local_path for image in post.images] == [
        "data/images/a.jpg",
        "data/images/b.jpg",
        "data/images/c.jpg",
    ]


def test_upsert_post_deduplicates_duplicate_image_urls_in_same_input():
    session = make_session()

    post = upsert_post(
        session,
        PostInput(
            vk_post_id=19,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_19",
            text="duplicate image input",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(
                ImageInput("https://example.com/a.jpg", "data/images/a.jpg", 100, 100),
                ImageInput("https://example.com/a.jpg", "data/images/a-copy.jpg", 100, 100),
            ),
        ),
    )
    session.commit()

    assert [image.vk_photo_url for image in post.images] == ["https://example.com/a.jpg"]


def test_search_posts_matches_text_and_orders_by_engagement():
    session = make_session()
    old = PostInput(
        vk_post_id=1,
        vk_owner_id=-1,
        vk_url="https://vk.com/wall-1_1",
        text="work joke",
        published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        likes_count=1,
        images=(ImageInput("https://example.com/1.jpg", "data/images/1.jpg"),),
    )
    popular = PostInput(
        vk_post_id=2,
        vk_owner_id=-1,
        vk_url="https://vk.com/wall-1_2",
        text="work joke",
        published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        likes_count=100,
        images=(ImageInput("https://example.com/2.jpg", "data/images/2.jpg"),),
    )
    upsert_post(session, old)
    upsert_post(session, popular)
    session.commit()

    results = search_posts(session, "work", limit=5)

    assert [post.vk_post_id for post in results] == [2, 1]


def test_search_posts_matches_cyrillic_ocr_tokens_case_insensitive():
    session = make_session()
    target = upsert_post(
        session,
        PostInput(
            vk_post_id=3,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_3",
            text="",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/3.jpg", "data/images/3.jpg"),),
        ),
    )
    target.ocr_text = "Батя играет в игры про сыновей"
    upsert_post(
        session,
        PostInput(
            vk_post_id=4,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_4",
            text="",
            published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/4.jpg", "data/images/4.jpg"),),
        ),
    ).ocr_text = "Совсем другой тред"
    session.commit()

    results = search_posts(session, "батя играет сыновей", limit=5)

    assert [post.vk_post_id for post in results] == [3]


def test_search_posts_normalizes_yo_letter():
    session = make_session()
    post = upsert_post(
        session,
        PostInput(
            vk_post_id=5,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_5",
            text="",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/5.jpg", "data/images/5.jpg"),),
        ),
    )
    post.ocr_text = "Ёжик несёт смешной тред"
    session.commit()

    results = search_posts(session, "ежик несет", limit=5)

    assert [post.vk_post_id for post in results] == [5]


def test_search_posts_requires_all_tokens_for_short_queries():
    session = make_session()
    first = upsert_post(
        session,
        PostInput(
            vk_post_id=6,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_6",
            text="",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/6.jpg", "data/images/6.jpg"),),
        ),
    )
    first.ocr_text = "батя играет"
    second = upsert_post(
        session,
        PostInput(
            vk_post_id=7,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_7",
            text="",
            published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/7.jpg", "data/images/7.jpg"),),
        ),
    )
    second.ocr_text = "батя"
    session.commit()

    results = search_posts(session, "батя играет", limit=5)

    assert [post.vk_post_id for post in results] == [6]


def test_search_posts_expands_parent_synonyms():
    session = make_session()
    target = upsert_post(
        session,
        PostInput(
            vk_post_id=8,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_8",
            text="",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/8.jpg", "data/images/8.jpg"),),
        ),
    )
    target.ocr_text = "Вспомнил пару историй про батю"
    session.commit()

    results = search_posts(session, "отец", limit=5)

    assert [post.vk_post_id for post in results] == [8]


def test_search_posts_expands_parent_synonyms_after_stopword():
    session = make_session()
    target = upsert_post(
        session,
        PostInput(
            vk_post_id=9,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_9",
            text="",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/9.jpg", "data/images/9.jpg"),),
        ),
    )
    target.ocr_text = "Вспомнил пару историй про батю"
    session.commit()

    results = search_posts(session, "про отца", limit=5)

    assert [post.vk_post_id for post in results] == [9]


def test_build_search_text_joins_post_ocr_tags_and_synonyms():
    session = make_session()
    post = upsert_post(
        session,
        PostInput(
            vk_post_id=12,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_12",
            text="Папа после работы",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/12.jpg", "data/images/12.jpg"),),
        ),
    )
    post.ocr_text = "Батя на кухне рассказывает тред"
    post.tags.append(Tag(name="семья"))
    session.commit()

    search_text = build_post_search_text(post)

    assert "папа" in search_text
    assert "батя" in search_text
    assert "кухне" in search_text
    assert "семья" in search_text
    assert "отец" in search_text


def test_build_search_text_adds_autotags():
    search_text = build_search_text(["Купил пиво после смены и пошел в общагу"])

    assert "алкоголь" in search_text
    assert "работа" in search_text
    assert "общага" in search_text


def test_build_search_text_normalizes_punctuation_and_short_tokens():
    search_text = build_search_text(["Ёжик!!! в 2 часа -- ок"])

    assert "ежик" in search_text
    assert "!!!" not in search_text
    assert " в " not in f" {search_text} "


def test_search_posts_uses_fts_index_when_available():
    session = make_session()
    target = upsert_post(
        session,
        PostInput(
            vk_post_id=13,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_13",
            text="",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            likes_count=5,
            images=(ImageInput("https://example.com/13.jpg", "data/images/13.jpg"),),
        ),
    )
    target.ocr_text = "Батя пришел домой после смены"
    target.tags.append(Tag(name="работа"))
    other = upsert_post(
        session,
        PostInput(
            vk_post_id=14,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_14",
            text="Странный мужик у подъезда",
            published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/14.jpg", "data/images/14.jpg"),),
        ),
    )
    session.commit()

    ensure_search_text_column(session)
    for post in (target, other):
        session.execute(
            text("UPDATE posts SET search_text = :search_text WHERE id = :post_id"),
            {"search_text": build_post_search_text(post), "post_id": post.id},
        )
    rebuild_fts_index(session)
    session.commit()

    results = search_posts(session, "папа после работы", limit=5)

    assert [post.vk_post_id for post in results] == [13]


def test_search_post_results_reports_score_and_source_for_fts():
    session = make_session()
    post = upsert_post(
        session,
        PostInput(
            vk_post_id=16,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_16",
            text="",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/16.jpg", "data/images/16.jpg"),),
        ),
    )
    post.ocr_text = "Батя после работы рассказывает истории"
    session.commit()

    ensure_search_text_column(session)
    session.execute(
        text("UPDATE posts SET search_text = :search_text WHERE id = :post_id"),
        {"search_text": build_post_search_text(post), "post_id": post.id},
    )
    rebuild_fts_index(session)
    session.commit()

    results = search_post_results(session, "папа после работы", limit=5)

    assert [(result.post.vk_post_id, result.source) for result in results] == [(16, "fts")]
    assert results[0].score > 0


def test_search_post_results_falls_back_when_fts_has_no_candidates():
    session = make_session()
    post = upsert_post(
        session,
        PostInput(
            vk_post_id=17,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_17",
            text="",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/17.jpg", "data/images/17.jpg"),),
        ),
    )
    post.ocr_text = "Пиво у подъезда"
    session.commit()

    ensure_search_text_column(session)
    rebuild_fts_index(session)
    session.commit()

    results = search_post_results(session, "пиво", limit=5)

    assert [(result.post.vk_post_id, result.source) for result in results] == [(17, "python")]


def test_search_posts_fts_uses_or_candidates_for_long_queries():
    session = make_session()
    post = upsert_post(
        session,
        PostInput(
            vk_post_id=18,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_18",
            text="",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/18.jpg", "data/images/18.jpg"),),
        ),
    )
    post.ocr_text = "Батя на кухне"
    session.commit()

    ensure_search_text_column(session)
    session.execute(
        text("UPDATE posts SET search_text = :search_text WHERE id = :post_id"),
        {"search_text": build_post_search_text(post), "post_id": post.id},
    )
    rebuild_fts_index(session)
    session.commit()

    results = search_post_results(session, "батя на кухне после работы", limit=5)

    assert [(result.post.vk_post_id, result.source) for result in results] == [(18, "fts")]


def test_search_posts_matches_tags_with_fallback_scoring():
    session = make_session()
    post = upsert_post(
        session,
        PostInput(
            vk_post_id=15,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_15",
            text="",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/15.jpg", "data/images/15.jpg"),),
        ),
    )
    post.tags.append(Tag(name="общага"))
    session.commit()

    results = search_posts(session, "общага", limit=5)

    assert [post.vk_post_id for post in results] == [15]


def test_get_latest_posts_requires_images():
    session = make_session()
    upsert_post(
        session,
        PostInput(
            vk_post_id=1,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_1",
            text="with image",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/1.jpg", "data/images/1.jpg"),),
        ),
    )
    upsert_post(
        session,
        PostInput(
            vk_post_id=2,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_2",
            text="without image",
            published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            images=(),
        ),
    )
    session.commit()

    assert [post.vk_post_id for post in get_latest_posts(session)] == [1]


def test_iter_images_without_ocr_skips_posts_with_ocr_unless_forced():
    session = make_session()
    without_ocr = upsert_post(
        session,
        PostInput(
            vk_post_id=1,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_1",
            text="",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/1.jpg", "data/images/1.jpg"),),
        ),
    )
    with_ocr = upsert_post(
        session,
        PostInput(
            vk_post_id=2,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_2",
            text="",
            published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/2.jpg", "data/images/2.jpg"),),
        ),
    )
    with_ocr.ocr_text = "recognized"
    session.commit()

    pending = list(iter_images_without_ocr(session, limit=100))
    forced = list(iter_images_without_ocr(session, limit=100, force=True))
    selected_post = list(iter_images_without_ocr(session, limit=100, post_id=without_ocr.id))

    assert [image.post_id for image in pending] == [without_ocr.id]
    assert [image.post_id for image in forced] == [without_ocr.id, with_ocr.id]
    assert [image.post_id for image in selected_post] == [without_ocr.id]
