from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
from typing import Iterable, Optional

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Favorite, Image, Post, SearchEvent, SearchQuery, Tag
from app.search.fts import fetch_posts_by_ids, search_fts
from app.search.normalization import expand_query_tokens, normalize_search_text, tokenize_search_query
from app.search.semantic import SemanticSearchUnavailable, semantic_search
from app.vk.client import is_promotional_text


@dataclass(frozen=True)
class ImageInput:
    vk_photo_url: str
    local_path: str
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass(frozen=True)
class PostInput:
    vk_post_id: int
    vk_owner_id: int
    vk_url: str
    text: str
    published_at: Optional[datetime]
    likes_count: int = 0
    comments_count: int = 0
    reposts_count: int = 0
    views_count: int = 0
    images: tuple[ImageInput, ...] = ()


@dataclass(frozen=True)
class PostSearchResult:
    post: Post
    score: float
    source: str


_SIMILAR_STOPWORDS = {
    "anon",
    "anonymous",
    "jpg",
    "jpeg",
    "png",
    "gif",
    "webm",
    "kb",
    "mb",
    "kbpng",
    "kbjpg",
    "no",
    "sage",
    "аnonim",
    "аноним",
    "номер",
    "файл",
    "пнд",
    "втр",
    "срд",
    "чтв",
    "птн",
    "суб",
    "вск",
}


def normalize_vk_datetime(timestamp: int | None) -> Optional[datetime]:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def upsert_post(session: Session, data: PostInput, update_existing: bool = True) -> Post:
    post = session.scalar(select(Post).where(Post.vk_post_id == data.vk_post_id).options(selectinload(Post.images)))
    if post is None:
        post = Post(
            vk_post_id=data.vk_post_id,
            vk_owner_id=data.vk_owner_id,
            vk_url=data.vk_url,
            text=data.text or "",
            published_at=data.published_at,
            likes_count=data.likes_count,
            comments_count=data.comments_count,
            reposts_count=data.reposts_count,
            views_count=data.views_count,
        )
        session.add(post)
        session.flush()
    elif update_existing:
        post.vk_owner_id = data.vk_owner_id
        post.vk_url = data.vk_url
        post.text = data.text or ""
        post.published_at = data.published_at
        post.likes_count = data.likes_count
        post.comments_count = data.comments_count
        post.reposts_count = data.reposts_count
        post.views_count = data.views_count

    existing_urls = {image.vk_photo_url for image in post.images}
    for image_data in data.images:
        if image_data.vk_photo_url in existing_urls:
            continue
        existing_urls.add(image_data.vk_photo_url)
        post.images.append(
            Image(
                vk_photo_url=image_data.vk_photo_url,
                local_path=image_data.local_path,
                width=image_data.width,
                height=image_data.height,
            )
        )
    return post


def existing_vk_post_ids(session: Session, vk_post_ids: Iterable[int]) -> set[int]:
    ids = tuple(dict.fromkeys(vk_post_ids))
    if not ids:
        return set()
    return set(session.scalars(select(Post.vk_post_id).where(Post.vk_post_id.in_(ids))).all())


def add_search_query(session: Session, user_id: Optional[int], query: str) -> SearchQuery:
    row = SearchQuery(user_id=user_id, query=query)
    session.add(row)
    session.flush()
    return row


def get_search_query(session: Session, query_id: int, user_id: Optional[int]) -> SearchQuery | None:
    return session.scalar(
        select(SearchQuery).where(SearchQuery.id == query_id, SearchQuery.user_id == user_id)
    )


def add_search_event(
    session: Session,
    user_id: int | None,
    post_id: int,
    event_type: str,
    query: str | None = None,
) -> SearchEvent:
    event = SearchEvent(user_id=user_id, post_id=post_id, event_type=event_type, query=query)
    session.add(event)
    session.flush()
    return event


def _normalize_tag_names(tag_names: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(tag_name.strip() for tag_name in tag_names if tag_name.strip()))


def get_post_tags(session: Session, post_id: int) -> list[Tag]:
    post = session.scalar(select(Post).where(Post.id == post_id).options(selectinload(Post.tags)))
    if post is None:
        return []
    return sorted(post.tags, key=lambda tag: tag.name)


def add_tags_to_post(session: Session, post_id: int, tag_names: Iterable[str]) -> list[Tag]:
    post = session.scalar(select(Post).where(Post.id == post_id).options(selectinload(Post.tags)))
    if post is None:
        return []
    names = _normalize_tag_names(tag_names)
    if not names:
        return []

    existing_tags = {
        tag.name: tag
        for tag in session.scalars(select(Tag).where(Tag.name.in_(names))).all()
    }
    added: list[Tag] = []
    current_names = {tag.name for tag in post.tags}
    for name in names:
        tag = existing_tags.get(name)
        if tag is None:
            tag = Tag(name=name)
            session.add(tag)
            session.flush()
            existing_tags[name] = tag
        if name not in current_names:
            post.tags.append(tag)
            current_names.add(name)
            added.append(tag)
    session.flush()
    return added


def remove_tags_from_post(session: Session, post_id: int, tag_names: Iterable[str]) -> list[Tag]:
    post = session.scalar(select(Post).where(Post.id == post_id).options(selectinload(Post.tags)))
    if post is None:
        return []
    names = set(_normalize_tag_names(tag_names))
    if not names:
        return []

    removed: list[Tag] = []
    remaining: list[Tag] = []
    for tag in post.tags:
        if tag.name in names:
            removed.append(tag)
        else:
            remaining.append(tag)
    post.tags = remaining
    session.flush()
    return sorted(removed, key=lambda tag: tag.name)


def add_favorite(session: Session, user_id: int, post_id: int) -> None:
    if is_favorite(session, user_id, post_id):
        return
    session.add(Favorite(user_id=user_id, post_id=post_id))
    session.flush()


def remove_favorite(session: Session, user_id: int, post_id: int) -> None:
    session.execute(delete(Favorite).where(Favorite.user_id == user_id, Favorite.post_id == post_id))
    session.flush()


def is_favorite(session: Session, user_id: int, post_id: int) -> bool:
    return session.get(Favorite, {"user_id": user_id, "post_id": post_id}) is not None


def get_favorite_posts(session: Session, user_id: int, limit: int = 10, offset: int = 0) -> list[Post]:
    stmt = (
        select(Post)
        .join(Favorite, Favorite.post_id == Post.id)
        .where(Favorite.user_id == user_id)
        .order_by(Favorite.created_at.desc(), Post.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(session.scalars(_with_post_images(stmt)).all())


def _is_similar_query_token(token: str) -> bool:
    if len(token) < 4 or len(token) > 24:
        return False
    if token in _SIMILAR_STOPWORDS:
        return False
    if any(char.isdigit() for char in token):
        return False
    if re.fullmatch(r"[a-f]+", token) and len(token) >= 8:
        return False
    if re.search(r"(.)\1{4,}", token):
        return False
    return True


def _build_similar_query(post: Post, max_tokens: int = 8) -> str:
    return " ".join(_rank_similar_query_tokens(post)[:max_tokens])


def _rank_similar_query_tokens(post: Post) -> list[str]:
    token_scores: dict[str, float] = defaultdict(float)
    first_positions: dict[str, int] = {}
    position = 0

    for source_weight, text in (
        (90, " ".join(tag.name for tag in post.tags)),
        (45, post.text or ""),
        (12, post.ocr_text or ""),
    ):
        for token in tokenize_search_query(text):
            position += 1
            if not _is_similar_query_token(token):
                continue
            first_positions.setdefault(token, position)
            token_scores[token] += source_weight + min(len(token), 12)

    return sorted(
        token_scores,
        key=lambda token: (-token_scores[token], first_positions[token], token),
    )


def find_similar_posts(session: Session, post_id: int, limit: int = 5) -> list[Post]:
    post = session.scalar(_with_post_images(select(Post).where(Post.id == post_id)))
    if post is None:
        return []

    ranked_tokens = _rank_similar_query_tokens(post)
    if not ranked_tokens:
        return []

    token_counts = [8, 6, 4, 3, 2]
    for token_count in token_counts:
        query = " ".join(ranked_tokens[:token_count])
        if not query.strip():
            continue
        candidates = search_posts(session, query, limit=limit + 10, offset=0)
        similar = [candidate for candidate in candidates if candidate.id != post_id][:limit]
        if similar:
            return similar
    return []


def _with_post_images(stmt: Select[tuple[Post]]) -> Select[tuple[Post]]:
    return stmt.options(selectinload(Post.images), selectinload(Post.tags))


def _count_token(text: str, token: str) -> int:
    return text.count(token)


def _minimum_matches(token_groups: list[tuple[str, ...]]) -> int:
    if len(token_groups) <= 2:
        return len(token_groups)
    return max(2, (len(token_groups) + 1) // 2)


def _score_post_for_query(post: Post, normalized_query: str, token_groups: list[tuple[str, ...]]) -> float:
    text = normalize_search_text(post.text or "")
    ocr_text = normalize_search_text(post.ocr_text or "")
    tag_text = normalize_search_text(" ".join(tag.name for tag in post.tags))

    score = 0.0
    if normalized_query:
        if normalized_query in text:
            score += 240
        if normalized_query in ocr_text:
            score += 180
        if normalized_query in tag_text:
            score += 160

    matched_tokens = 0
    for group in token_groups:
        text_hits = sum(_count_token(text, token) for token in group)
        ocr_hits = sum(_count_token(ocr_text, token) for token in group)
        tag_hits = sum(_count_token(tag_text, token) for token in group)
        if text_hits or ocr_hits or tag_hits:
            matched_tokens += 1
        score += min(text_hits, 5) * 30
        score += min(ocr_hits, 5) * 18
        score += min(tag_hits, 3) * 35

    if token_groups and matched_tokens < _minimum_matches(token_groups):
        return 0.0
    if token_groups:
        score += (matched_tokens / len(token_groups)) * 100

    score += min(post.likes_count, 5000) * 0.001
    score += min(post.comments_count, 1000) * 0.002
    score += min(post.reposts_count, 1000) * 0.003
    return score


def _sort_search_results(results: list[PostSearchResult]) -> list[PostSearchResult]:
    results.sort(
        key=lambda item: (
            item.score,
            item.post.published_at or datetime.min.replace(tzinfo=timezone.utc),
            item.post.id,
        ),
        reverse=True,
    )
    return results


def _content_tokens(post: Post) -> set[str]:
    content = normalize_search_text(" ".join([post.text or "", post.ocr_text or ""]))
    return {token for token in content.split() if len(token) >= 3 and not token.isdigit()}


def _deduplicate_story_results(results: list[PostSearchResult]) -> list[PostSearchResult]:
    unique: list[PostSearchResult] = []
    fingerprints: list[set[str]] = []
    seen_ids: set[int] = set()
    for result in results:
        if result.post.id in seen_ids:
            continue
        tokens = _content_tokens(result.post)
        is_duplicate = False
        if len(tokens) >= 20:
            for fingerprint in fingerprints:
                if len(fingerprint) < 20:
                    continue
                overlap = len(tokens & fingerprint) / min(len(tokens), len(fingerprint))
                if overlap >= 0.85:
                    is_duplicate = True
                    break
        if is_duplicate:
            continue
        seen_ids.add(result.post.id)
        unique.append(result)
        fingerprints.append(tokens)
    return unique


def _is_displayable_post(post: Post) -> bool:
    return not is_promotional_text(post.text)


def _fallback_search_results(
    session: Session,
    normalized_query: str,
    token_groups: list[tuple[str, ...]],
) -> list[PostSearchResult]:
    stmt = select(Post).join(Post.images).distinct()
    posts = list(session.scalars(_with_post_images(stmt)).all())
    results = [
        PostSearchResult(post=post, score=score, source="python")
        for post in posts
        if _is_displayable_post(post) and (score := _score_post_for_query(post, normalized_query, token_groups)) > 0
    ]
    return _sort_search_results(results)


def _source_name(has_fts: bool, has_semantic: bool) -> str:
    if has_fts and has_semantic:
        return "fts+semantic"
    if has_semantic:
        return "semantic"
    if has_fts:
        return "fts"
    return "python"


def _semantic_enabled() -> bool:
    value = os.getenv("THREADBOT_SEMANTIC_SEARCH", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def search_post_results(session: Session, query: str, limit: int = 5, offset: int = 0) -> list[PostSearchResult]:
    normalized = normalize_search_text(query)
    if not normalized:
        return []
    tokens = tokenize_search_query(query)
    if not tokens:
        return []
    token_groups = expand_query_tokens(tokens)

    candidate_limit = max(50, offset + limit * 4)
    fts_candidates = search_fts(session, query, limit=candidate_limit)
    semantic_candidates = []
    if _semantic_enabled():
        try:
            semantic_candidates = semantic_search(query, top_k=candidate_limit)
        except SemanticSearchUnavailable:
            semantic_candidates = []

    if fts_candidates or semantic_candidates:
        fts_scores = {item.post_id: item.score for item in fts_candidates}
        semantic_scores = {item.post_id: item.score for item in semantic_candidates}
        candidate_ids = list(dict.fromkeys([*fts_scores.keys(), *semantic_scores.keys()]))
        candidate_posts = [
            post for post in fetch_posts_by_ids(session, candidate_ids) if post.images and _is_displayable_post(post)
        ]
        results: list[PostSearchResult] = []
        for post in candidate_posts:
            score = _score_post_for_query(post, normalized, token_groups)
            semantic_score = semantic_scores.get(post.id, 0.0)
            if score <= 0 and semantic_score <= 0:
                continue
            fts_rank_bonus = 100.0 / (1.0 + abs(fts_scores.get(post.id, 0.0))) if post.id in fts_scores else 0.0
            semantic_weight = 80.0 if score > 0 or post.id in fts_scores else 40.0
            semantic_bonus = max(semantic_score, 0.0) * semantic_weight
            source = _source_name(post.id in fts_scores, post.id in semantic_scores)
            results.append(PostSearchResult(post=post, score=score + fts_rank_bonus + semantic_bonus, source=source))
        if results:
            return _deduplicate_story_results(_sort_search_results(results))[offset : offset + limit]

    return _deduplicate_story_results(_fallback_search_results(session, normalized, token_groups))[offset : offset + limit]


def search_posts(session: Session, query: str, limit: int = 5, offset: int = 0) -> list[Post]:
    return [result.post for result in search_post_results(session, query, limit, offset)]


def get_random_post(session: Session) -> Optional[Post]:
    stmt = select(Post).join(Post.images).order_by(func.random()).limit(1)
    for _ in range(20):
        post = session.scalar(_with_post_images(stmt))
        if post is None or _is_displayable_post(post):
            return post
    stmt = select(Post).join(Post.images).distinct()
    posts = list(session.scalars(_with_post_images(stmt)).all())
    return next((post for post in posts if _is_displayable_post(post)), None)


def get_latest_posts(session: Session, limit: int = 5) -> list[Post]:
    stmt = (
        select(Post)
        .join(Post.images)
        .distinct()
        .order_by(Post.published_at.desc().nullslast(), Post.id.desc())
        .limit(max(limit * 5, limit))
    )
    posts = list(session.scalars(_with_post_images(stmt)).all())
    return [post for post in posts if _is_displayable_post(post)][:limit]


def iter_images_without_ocr(
    session: Session,
    limit: int = 100,
    post_id: int | None = None,
    force: bool = False,
    min_existing_ocr_length: int | None = None,
    max_existing_ocr_length: int | None = None,
) -> Iterable[Image]:
    stmt = select(Image).join(Image.post).options(selectinload(Image.post)).order_by(Image.id)
    if min_existing_ocr_length is not None:
        stmt = stmt.where(func.length(Post.ocr_text) >= min_existing_ocr_length)
    if max_existing_ocr_length is not None:
        stmt = stmt.where(func.length(Post.ocr_text) <= max_existing_ocr_length)
    elif not force:
        stmt = stmt.where(or_(Post.ocr_text == "", Post.ocr_text.is_(None)))
    if post_id is not None:
        stmt = stmt.where(Post.id == post_id)
    if limit > 0:
        stmt = stmt.limit(limit)
    return session.scalars(stmt).all()
