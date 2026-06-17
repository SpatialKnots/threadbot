from datetime import datetime, timezone
import os

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Post, Tag
from app.db.repositories import (
    ImageInput,
    PostInput,
    _build_similar_query,
    add_favorite,
    add_search_event,
    add_tags_to_post,
    find_similar_posts,
    get_latest_posts,
    get_favorite_posts,
    get_post_tags,
    get_search_query,
    is_favorite,
    iter_images_without_ocr,
    add_search_query,
    remove_favorite,
    remove_tags_from_post,
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


def test_search_posts_deduplicates_near_duplicate_ocr_stories():
    session = make_session()
    duplicate_text = (
        "Как я тебя понимаю. Жил один 6 лет и вынужден сейчас жить с родаками. "
        "Мамка храпит так что слышно через две закрытые двери. Батя от нее не отстает. "
        "Приходит с работы рано и падает спать. Потом врубает телик на всю квартиру. "
        "На кухне стоит батин суп и начинаются семейные разговоры про батю."
    )
    duplicate_a = upsert_post(
        session,
        PostInput(
            vk_post_id=20,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_20",
            text="",
            published_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            likes_count=10,
            images=(ImageInput("https://example.com/20.jpg", "data/images/20.jpg"),),
        ),
    )
    duplicate_a.ocr_text = duplicate_text
    duplicate_b = upsert_post(
        session,
        PostInput(
            vk_post_id=21,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_21",
            text="",
            published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            likes_count=1,
            images=(ImageInput("https://example.com/21.jpg", "data/images/21.jpg"),),
        ),
    )
    duplicate_b.ocr_text = duplicate_text.replace("понимаю", "п0нимаю").replace("закрытые", "закрытыe")
    unique = upsert_post(
        session,
        PostInput(
            vk_post_id=22,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_22",
            text="",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/22.jpg", "data/images/22.jpg"),),
        ),
    )
    unique.ocr_text = (
        "Другая история про батин суп. Отец варил странный обед, сосед смеялся, "
        "а потом вся семья обсуждала кастрюлю и рецепт."
    )
    session.commit()

    results = search_posts(session, "про батин суп", limit=5)

    result_ids = {post.vk_post_id for post in results}
    assert result_ids == {20, 22}
    assert 21 not in result_ids


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


def test_get_latest_posts_deduplicates_multi_image_posts():
    session = make_session()
    upsert_post(
        session,
        PostInput(
            vk_post_id=1,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_1",
            text="multi image",
            published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            images=(
                ImageInput("https://example.com/1.jpg", "data/images/1.jpg"),
                ImageInput("https://example.com/2.jpg", "data/images/2.jpg"),
            ),
        ),
    )
    upsert_post(
        session,
        PostInput(
            vk_post_id=2,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_2",
            text="single image",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/3.jpg", "data/images/3.jpg"),),
        ),
    )
    session.commit()

    assert [post.vk_post_id for post in get_latest_posts(session, limit=5)] == [1, 2]


def test_get_search_query_requires_same_user():
    session = make_session()
    query = add_search_query(session, user_id=10, query="needle")
    session.commit()

    assert get_search_query(session, query.id, user_id=10).query == "needle"
    assert get_search_query(session, query.id, user_id=11) is None


def test_add_search_event_records_disliked_event():
    session = make_session()
    post = upsert_post(
        session,
        PostInput(
            vk_post_id=29,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_29",
            text="feedback target",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/29.jpg", "data/images/29.jpg"),),
        ),
    )
    session.commit()

    event = add_search_event(session, user_id=10, post_id=post.id, event_type="disliked", query="needle")
    session.commit()

    assert event.id is not None
    assert event.user_id == 10
    assert event.post_id == post.id
    assert event.event_type == "disliked"
    assert event.query == "needle"


def test_add_tags_to_post_creates_tags_and_is_idempotent():
    session = make_session()
    post = upsert_post(
        session,
        PostInput(
            vk_post_id=40,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_40",
            text="tag target",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/40.jpg", "data/images/40.jpg"),),
        ),
    )
    session.commit()

    first = add_tags_to_post(session, post.id, ["батя", "техника", "батя"])
    second = add_tags_to_post(session, post.id, ["батя", "техника"])
    session.commit()

    assert [tag.name for tag in first] == ["батя", "техника"]
    assert second == []
    assert [tag.name for tag in get_post_tags(session, post.id)] == ["батя", "техника"]


def test_remove_tags_from_post_unlinks_only_requested_tags():
    session = make_session()
    post = upsert_post(
        session,
        PostInput(
            vk_post_id=41,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_41",
            text="tag target",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/41.jpg", "data/images/41.jpg"),),
        ),
    )
    session.commit()

    add_tags_to_post(session, post.id, ["батя", "техника"])
    removed = remove_tags_from_post(session, post.id, ["техника", "missing"])
    session.commit()

    assert [tag.name for tag in removed] == ["техника"]
    assert [tag.name for tag in get_post_tags(session, post.id)] == ["батя"]


def test_add_favorite_is_idempotent():
    session = make_session()
    post = upsert_post(
        session,
        PostInput(
            vk_post_id=30,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_30",
            text="favorite",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/30.jpg", "data/images/30.jpg"),),
        ),
    )
    session.commit()

    add_favorite(session, user_id=10, post_id=post.id)
    add_favorite(session, user_id=10, post_id=post.id)
    session.commit()

    assert is_favorite(session, user_id=10, post_id=post.id) is True
    assert [favorite.id for favorite in get_favorite_posts(session, user_id=10)] == [post.id]


def test_remove_favorite_is_idempotent():
    session = make_session()
    post = upsert_post(
        session,
        PostInput(
            vk_post_id=31,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_31",
            text="favorite",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/31.jpg", "data/images/31.jpg"),),
        ),
    )
    session.commit()

    add_favorite(session, user_id=10, post_id=post.id)
    remove_favorite(session, user_id=10, post_id=post.id)
    remove_favorite(session, user_id=10, post_id=post.id)
    session.commit()

    assert is_favorite(session, user_id=10, post_id=post.id) is False
    assert get_favorite_posts(session, user_id=10) == []


def test_get_favorite_posts_orders_by_created_at_desc():
    session = make_session()
    older = upsert_post(
        session,
        PostInput(
            vk_post_id=32,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_32",
            text="older favorite",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/32.jpg", "data/images/32.jpg"),),
        ),
    )
    newer = upsert_post(
        session,
        PostInput(
            vk_post_id=33,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_33",
            text="newer favorite",
            published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/33.jpg", "data/images/33.jpg"),),
        ),
    )
    session.commit()

    add_favorite(session, user_id=10, post_id=older.id)
    add_favorite(session, user_id=10, post_id=newer.id)
    session.commit()

    assert [post.id for post in get_favorite_posts(session, user_id=10)] == [newer.id, older.id]


def test_find_similar_posts_excludes_current_post():
    session = make_session()
    current = upsert_post(
        session,
        PostInput(
            vk_post_id=34,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_34",
            text="router kitchen family story",
            published_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/34.jpg", "data/images/34.jpg"),),
        ),
    )
    similar = upsert_post(
        session,
        PostInput(
            vk_post_id=35,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_35",
            text="router kitchen family joke",
            published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/35.jpg", "data/images/35.jpg"),),
        ),
    )
    unrelated = upsert_post(
        session,
        PostInput(
            vk_post_id=36,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_36",
            text="unrelated train station",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/36.jpg", "data/images/36.jpg"),),
        ),
    )
    network = Tag(name="network")
    current.tags.append(network)
    similar.tags.append(network)
    unrelated.tags.append(Tag(name="transport"))
    session.commit()

    results = find_similar_posts(session, current.id, limit=5)

    assert [post.id for post in results] == [similar.id]
    assert current.id not in [post.id for post in results]


def test_build_similar_query_filters_ocr_board_noise():
    post = Post(
        id=1,
        vk_post_id=1,
        vk_owner_id=-1,
        vk_url="https://vk.com/wall-1_1",
        text="",
        ocr_text=(
            "Anonymous 09/22/18 Sat No.48287782 765 KBPNG "
            "router kitchen family router kitchen family open door"
        ),
    )

    query = _build_similar_query(post)

    assert "anonymous" not in query
    assert "48287782" not in query
    assert "kbpng" not in query
    assert "router" in query
    assert "kitchen" in query
    assert "family" in query


def test_find_similar_posts_uses_short_signature_query():
    session = make_session()
    current = upsert_post(
        session,
        PostInput(
            vk_post_id=37,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_37",
            text="",
            published_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/37.jpg", "data/images/37.jpg"),),
        ),
    )
    current.ocr_text = (
        "Anonymous 09/22/18 Sat No.48287782 765 KBPNG "
        "door router kitchen family router kitchen family "
        "oneoffalpha oneoffbravo oneoffcharlie oneoffdelta oneoffecho oneofffoxtrot"
    )
    similar = upsert_post(
        session,
        PostInput(
            vk_post_id=38,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_38",
            text="door router kitchen family",
            published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/38.jpg", "data/images/38.jpg"),),
        ),
    )
    upsert_post(
        session,
        PostInput(
            vk_post_id=39,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_39",
            text="train station platform",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/39.jpg", "data/images/39.jpg"),),
        ),
    )
    session.commit()

    results = find_similar_posts(session, current.id, limit=5)

    assert [post.id for post in results] == [similar.id]


def test_search_and_latest_posts_skip_promotional_posts():
    session = make_session()
    upsert_post(
        session,
        PostInput(
            vk_post_id=385700,
            vk_owner_id=-121574455,
            vk_url="https://vk.com/wall-121574455_385700",
            text="[club27725025|PHOTO FILM] - атмосферный и интересный паблик c фотографиями!",
            published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/ad.jpg", "data/images/ad.jpg"),),
        ),
    )
    upsert_post(
        session,
        PostInput(
            vk_post_id=1,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_1",
            text="атмосферный тред с фотографиями",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/thread.jpg", "data/images/thread.jpg"),),
        ),
    )
    session.commit()

    assert [post.vk_post_id for post in search_posts(session, "атмосферный", limit=5)] == [1]
    assert [post.vk_post_id for post in get_latest_posts(session, limit=5)] == [1]


def test_search_and_latest_posts_skip_market_promotional_posts():
    session = make_session()
    upsert_post(
        session,
        PostInput(
            vk_post_id=384047,
            vk_owner_id=-121574455,
            vk_url="https://vk.com/wall-121574455_384047",
            text=(
                "Запускаем продажу streetwear шмоток\n\n"
                "С осенними скидками все товары по 1069р!\n\n"
                "Полный каталог товаров: https://vk.com/market-199985222\n\n"
                "Кол-во ограничено."
            ),
            published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/ad.jpg", "data/images/ad.jpg"),),
        ),
    )
    upsert_post(
        session,
        PostInput(
            vk_post_id=1,
            vk_owner_id=-1,
            vk_url="https://vk.com/wall-1_1",
            text="streetwear тред про одежду из старого обсуждения",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            images=(ImageInput("https://example.com/thread.jpg", "data/images/thread.jpg"),),
        ),
    )
    session.commit()

    assert [post.vk_post_id for post in search_posts(session, "streetwear", limit=5)] == [1]
    assert [post.vk_post_id for post in get_latest_posts(session, limit=5)] == [1]


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


def test_iter_images_without_ocr_can_select_short_existing_ocr_range():
    session = make_session()
    short_ocr = upsert_post(
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
    long_ocr = upsert_post(
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
    short_ocr.ocr_text = "short"
    long_ocr.ocr_text = "long enough"
    session.commit()

    selected = list(
        iter_images_without_ocr(
            session,
            limit=100,
            force=True,
            min_existing_ocr_length=1,
            max_existing_ocr_length=5,
        )
    )

    assert [image.post_id for image in selected] == [short_ocr.id]
