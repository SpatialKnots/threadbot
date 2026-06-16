from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Iterable, Optional

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Image, Post, SearchQuery, Tag
from app.search.fts import fetch_posts_by_ids, search_fts
from app.search.normalization import expand_query_tokens, normalize_search_text, tokenize_search_query
from app.search.semantic import SemanticSearchUnavailable, semantic_search


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
        if (score := _score_post_for_query(post, normalized_query, token_groups)) > 0
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
        candidate_posts = [post for post in fetch_posts_by_ids(session, candidate_ids) if post.images]
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
            return _sort_search_results(results)[offset : offset + limit]

    return _fallback_search_results(session, normalized, token_groups)[offset : offset + limit]


def search_posts(session: Session, query: str, limit: int = 5, offset: int = 0) -> list[Post]:
    return [result.post for result in search_post_results(session, query, limit, offset)]


def get_random_post(session: Session) -> Optional[Post]:
    stmt = select(Post).join(Post.images).order_by(func.random()).limit(1)
    return session.scalar(_with_post_images(stmt))


def get_latest_posts(session: Session, limit: int = 5) -> list[Post]:
    stmt = select(Post).join(Post.images).order_by(Post.published_at.desc().nullslast(), Post.id.desc()).limit(limit)
    return list(session.scalars(_with_post_images(stmt)).all())


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
