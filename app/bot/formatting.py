from __future__ import annotations

from datetime import timezone

from app.db.models import Post


TELEGRAM_CAPTION_LIMIT = 1024
TELEGRAM_MESSAGE_LIMIT = 4096


def _text_fragment(post: Post, max_len: int = 420) -> str:
    text = (post.text or post.ocr_text or "").strip()
    if not text:
        return "No text saved."
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def format_post_caption(post: Post, index: int = 0, total: int = 1) -> str:
    if post.published_at is None:
        date = "unknown"
    else:
        date = post.published_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    original_2ch = (post.original_url or "").strip()
    links = f"VK source:\n{post.vk_url}"
    if original_2ch:
        links = f"Original 2ch:\n{original_2ch}\n\n{links}"

    caption = (
        f"Found: {index + 1}/{total}\n\n"
        f"Date: {date}\n"
        f"Likes: {post.likes_count}\n"
        f"Comments: {post.comments_count}\n"
        f"Reposts: {post.reposts_count}\n\n"
        f"Text:\n{_text_fragment(post)}\n\n"
        f"{links}"
    )
    if len(caption) <= TELEGRAM_CAPTION_LIMIT:
        return caption
    footer = f"\n\n{links}"
    keep = TELEGRAM_CAPTION_LIMIT - len(footer) - 3
    return caption[:keep].rstrip() + "..." + footer


def format_ocr_debug_messages(post: Post, max_len: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    header = "OCR diagnostic:\n"
    text = (post.ocr_text or "").strip()
    if not text:
        return [header + "No OCR text saved for this thread yet."]

    chunks: list[str] = []
    available = max_len - len(header)
    remaining = text
    while remaining:
        chunk = remaining[:available]
        if len(remaining) > available:
            split_at = max(chunk.rfind("\n"), chunk.rfind(" "))
            if split_at > available // 2:
                chunk = chunk[:split_at]
        chunks.append(header + chunk.strip())
        remaining = remaining[len(chunk) :].strip()
    return chunks
