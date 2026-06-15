from __future__ import annotations

import json
from collections.abc import Iterable

from app.db.models import Post
from app.search.autotags import extract_tags
from app.search.normalization import expand_query_tokens, normalize_search_text, tokenize_search_query


def build_post_search_text(post: Post) -> str:
    parts = [
        post.text or "",
        post.ocr_text or "",
        " ".join(tag.name for tag in post.tags),
    ]
    return build_search_text(parts)


def build_search_text(parts: Iterable[str]) -> str:
    normalized = normalize_search_text(" ".join(part for part in parts if part))
    tags = extract_tags(normalized)
    if tags:
        normalized = " ".join([normalized, *tags])
    tokens = tokenize_search_query(normalized)
    expanded: list[str] = []
    for group in expand_query_tokens(tokens):
        expanded.extend(group)
    return " ".join(dict.fromkeys(expanded))


def parse_tag_names(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if item]
