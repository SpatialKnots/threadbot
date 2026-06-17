from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineQuery,
    InlineQueryResultArticle,
    InputMediaPhoto,
    InputTextMessageContent,
    Message,
)

from app.bot.formatting import format_ocr_debug_messages, format_post_caption
from app.bot.keyboards import (
    HELP_BUTTON,
    LATEST_BUTTON,
    RANDOM_BUTTON,
    SEARCH_BUTTON,
    main_reply_keyboard,
    result_keyboard,
    welcome_inline_keyboard,
)
from app.check_updates import CheckResult, check_for_new_threads
from app.config import get_settings
from app.db.models import Post
from app.db.repositories import (
    add_favorite,
    add_search_event,
    add_search_query,
    add_tags_to_post,
    find_similar_posts,
    get_favorite_posts,
    get_latest_posts,
    get_post_tags,
    get_random_post,
    get_search_query,
    remove_favorite,
    remove_tags_from_post,
    search_posts,
)
from app.db.session import get_session
from app.twoch.originals import extract_2ch_post_numbers, find_original_from_text


router = Router()
logger = logging.getLogger(__name__)
check_lock = asyncio.Lock()
LAZY_ORIGINAL_LOOKUP_TIMEOUT_SECONDS = 4.0
FAVORITES_QUERY_ID = 0
SIMILAR_QUERY_ID = -1


@dataclass
class SearchState:
    query_id: int
    results: list[int]


user_search_state: dict[int, SearchState] = {}
WELCOME_TEXT = (
    "Thread Search Bot\n\n"
    "Send any phrase to search saved VK thread images by post text and OCR text.\n\n"
    "Available commands:\n"
    "/search query - search explicitly\n"
    "/random - random saved thread\n"
    "/latest - latest saved threads\n"
    "/favorites - saved favorite threads\n"
    "/check - import new VK posts\n"
    "/help - short help"
)
SEARCH_HELP_TEXT = "Send a search phrase, or use /search followed by a query."
HELP_TEXT = (
    "Send text to search saved post text and OCR text. "
    "Use /random for a random thread, /latest for recent saved threads, /favorites for saved threads, "
    "and /check to import new threads."
)


def _format_check_result(result: CheckResult) -> str:
    return (
        "Check finished.\n"
        f"Inspected VK posts: {result.inspected}\n"
        f"New threads saved: {result.saved}\n"
        f"Skipped: {result.skipped}\n"
        f"OCR images: {result.ocr_selected}\n"
        f"OCR recognized: {result.ocr_recognized}\n"
        f"OCR empty: {result.ocr_empty}\n"
        f"OCR failed: {result.ocr_failed}\n"
        f"2ch originals checked: {result.originals_checked}\n"
        f"2ch originals found: {result.originals_found}\n"
        f"Search rebuilt: {'yes' if result.search_rebuilt else 'no'}"
    )


def _compact_inline_text(text: str, max_length: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 1].rstrip() + "…"


def _inline_result_title(post: Post) -> str:
    title = _compact_inline_text(post.text or post.ocr_text or f"Thread {post.id}", 64)
    return title or f"Thread {post.id}"


def _inline_result_description(post: Post) -> str:
    description = _compact_inline_text(post.ocr_text or post.text or post.vk_url, 160)
    return description or post.vk_url


def _inline_result_message(post: Post) -> str:
    parts = [format_post_caption(post), post.vk_url]
    if post.original_url:
        parts.append(post.original_url)
    return "\n\n".join(part for part in parts if part)


def is_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    settings = get_settings(require_tokens=False)
    return user_id in settings.admin_ids


def _post_image_paths(post: Post) -> list[str]:
    return [image.local_path for image in sorted(post.images, key=lambda image: image.id)]


async def _ensure_original_url(post: Post) -> str:
    if post.original_url or not extract_2ch_post_numbers(post.ocr_text or ""):
        return post.original_url or ""
    try:
        original = await asyncio.wait_for(
            find_original_from_text(post.ocr_text or "", number_limit=2),
            timeout=LAZY_ORIGINAL_LOOKUP_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.info("Lazy 2ch original lookup timed out for post_id=%s.", post.id)
        return ""
    if original is None:
        return ""
    with get_session() as session:
        stored_post = session.get(Post, post.id)
        if stored_post is not None and not stored_post.original_url:
            stored_post.original_url = original.url
            session.commit()
    post.original_url = original.url
    return original.url


async def _send_post(
    message: Message,
    post: Post,
    index: int = 0,
    total: int = 1,
    query_id: int = 0,
    favorite_action: str = "add",
) -> None:
    caption = format_post_caption(post, index=index, total=total)
    image_paths = _post_image_paths(post)
    keyboard = result_keyboard(
        query_id,
        index,
        total,
        post.vk_url,
        post_id=post.id,
        original_url=post.original_url,
        favorite_action=favorite_action,
    )
    if len(image_paths) == 1:
        await message.answer_photo(FSInputFile(image_paths[0]), caption=caption, reply_markup=keyboard)
    elif image_paths:
        media = [
            InputMediaPhoto(media=FSInputFile(image_path), caption=caption if media_index == 0 else None)
            for media_index, image_path in enumerate(image_paths[:10])
        ]
        await message.answer_media_group(media=media)
        await message.answer("Actions", reply_markup=keyboard)
    else:
        await message.answer(caption, reply_markup=keyboard)


async def _send_welcome(message: Message) -> None:
    await message.answer(WELCOME_TEXT, reply_markup=main_reply_keyboard())
    await message.answer("Choose an action:", reply_markup=welcome_inline_keyboard())


@router.inline_query()
async def inline_query(query: InlineQuery) -> None:
    settings = get_settings(require_tokens=False)
    if not settings.enable_inline:
        await query.answer([], cache_time=5, is_personal=True)
        return
    search_query = query.query.strip()
    if not search_query:
        await query.answer([], cache_time=5, is_personal=True)
        return

    with get_session() as session:
        posts = search_posts(session, search_query, limit=min(settings.results_per_page, 10), offset=0)

    results = [
        InlineQueryResultArticle(
            id=str(post.id),
            title=_inline_result_title(post),
            description=_inline_result_description(post),
            input_message_content=InputTextMessageContent(message_text=_inline_result_message(post)),
        )
        for post in posts
    ]
    await query.answer(results, cache_time=30, is_personal=True)


@router.message(Command("start"))
async def start(message: Message) -> None:
    await _send_welcome(message)


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("check"))
async def check_command(message: Message) -> None:
    if check_lock.locked():
        await message.answer("Check is already running. Wait for the current run to finish.")
        return

    await message.answer("Checking VK for new threads. This can take a few minutes if OCR is needed.")
    settings = get_settings(require_tokens=True)
    async with check_lock:
        try:
            result = await check_for_new_threads(settings)
        except Exception:
            logger.exception("Manual /check failed.")
            await message.answer("Check failed. See application logs for details.")
            return
    await message.answer(_format_check_result(result))


@router.message(Command("random"))
async def random_command(message: Message) -> None:
    with get_session() as session:
        post = get_random_post(session)
        if post is None:
            await message.answer("No saved posts with images yet.")
            return
        await _send_post(message, post)


@router.message(Command("latest"))
async def latest_command(message: Message) -> None:
    settings = get_settings(require_tokens=False)
    with get_session() as session:
        posts = get_latest_posts(session, settings.results_per_page)
        if not posts:
            await message.answer("No saved posts with images yet.")
            return
        await _send_post(message, posts[0], index=0, total=len(posts))


@router.message(Command("favorites"))
async def favorites_command(message: Message) -> None:
    settings = get_settings(require_tokens=False)
    if not settings.enable_favorites:
        await message.answer("Favorites are disabled.")
        return
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        await message.answer("Favorites require a Telegram user.")
        return
    with get_session() as session:
        posts = get_favorite_posts(session, user_id, settings.results_per_page)
        if not posts:
            await message.answer("У тебя пока нет избранных тредов")
            return
        user_search_state[user_id] = SearchState(query_id=FAVORITES_QUERY_ID, results=[post.id for post in posts])
        await _send_post(message, posts[0], index=0, total=len(posts), favorite_action="remove")


def _parse_tag_command(text: str, command: str) -> tuple[int | None, list[str]]:
    payload = text.removeprefix(command).strip()
    parts = payload.split()
    if not parts:
        return None, []
    try:
        post_id = int(parts[0])
    except ValueError:
        return None, []
    return post_id, parts[1:]


@router.message(Command("tag"))
async def tag_command(message: Message) -> None:
    settings = get_settings(require_tokens=False)
    if not settings.enable_tag_commands:
        await message.answer("Tag commands are disabled.")
        return
    if not is_admin(message.from_user.id if message.from_user else None):
        await message.answer("Admin only.")
        return
    post_id, tag_names = _parse_tag_command(message.text or "", "/tag")
    if post_id is None or not tag_names:
        await message.answer("Usage: /tag {post_id} {tag1} {tag2} ...")
        return
    with get_session() as session:
        post = session.get(Post, post_id)
        if post is None:
            await message.answer("Thread not found")
            return
        added = add_tags_to_post(session, post_id, tag_names)
        session.commit()
    if added:
        await message.answer("Added tags: " + ", ".join(tag.name for tag in added))
    else:
        await message.answer("No new tags added.")


@router.message(Command("untag"))
async def untag_command(message: Message) -> None:
    settings = get_settings(require_tokens=False)
    if not settings.enable_tag_commands:
        await message.answer("Tag commands are disabled.")
        return
    if not is_admin(message.from_user.id if message.from_user else None):
        await message.answer("Admin only.")
        return
    post_id, tag_names = _parse_tag_command(message.text or "", "/untag")
    if post_id is None or not tag_names:
        await message.answer("Usage: /untag {post_id} {tag1} {tag2} ...")
        return
    with get_session() as session:
        post = session.get(Post, post_id)
        if post is None:
            await message.answer("Thread not found")
            return
        removed = remove_tags_from_post(session, post_id, tag_names)
        session.commit()
    if removed:
        await message.answer("Removed tags: " + ", ".join(tag.name for tag in removed))
    else:
        await message.answer("No tags removed.")


@router.message(Command("tags"))
async def tags_command(message: Message) -> None:
    settings = get_settings(require_tokens=False)
    if not settings.enable_tag_commands:
        await message.answer("Tag commands are disabled.")
        return
    post_id, _ = _parse_tag_command(message.text or "", "/tags")
    if post_id is None:
        await message.answer("Usage: /tags {post_id}")
        return
    with get_session() as session:
        post = session.get(Post, post_id)
        if post is None:
            await message.answer("Thread not found")
            return
        tags = get_post_tags(session, post_id)
    if tags:
        await message.answer("Tags: " + ", ".join(tag.name for tag in tags))
    else:
        await message.answer("No tags.")


@router.message(Command("search"))
async def search_command(message: Message) -> None:
    query = ""
    if message.text:
        query = message.text.removeprefix("/search").strip()
    if not query:
        await message.answer("Send /search followed by a query, or just send query text.")
        return
    await handle_text_search(message, query)


@router.message()
async def text_message(message: Message) -> None:
    if not message.text:
        return
    text = message.text.strip()
    if text.upper() == "START":
        await _send_welcome(message)
        return
    if text == SEARCH_BUTTON:
        await message.answer(SEARCH_HELP_TEXT)
        return
    if text == RANDOM_BUTTON:
        await random_command(message)
        return
    if text == LATEST_BUTTON:
        await latest_command(message)
        return
    if text == HELP_BUTTON:
        await help_command(message)
        return
    await handle_text_search(message, message.text)


async def handle_text_search(message: Message, query: str) -> None:
    settings = get_settings(require_tokens=False)
    with get_session() as session:
        query_row = add_search_query(session, message.from_user.id if message.from_user else None, query)
        posts = search_posts(session, query, settings.results_per_page, 0)
        session.commit()
        if not posts:
            await message.answer("Nothing found.")
            return
        user_id = message.from_user.id if message.from_user else 0
        user_search_state[user_id] = SearchState(query_id=query_row.id, results=[post.id for post in posts])
        await _send_post(message, posts[0], index=0, total=len(posts), query_id=query_row.id)


@router.callback_query(lambda callback: callback.data == "random")
async def random_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    await random_command(callback.message)
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "latest")
async def latest_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    await latest_command(callback.message)
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "help")
async def help_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    await callback.message.answer(HELP_TEXT)
    await callback.answer()


@router.callback_query(lambda callback: callback.data == "search_help")
async def search_help_callback(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    await callback.message.answer(SEARCH_HELP_TEXT)
    await callback.answer()


@router.callback_query(lambda callback: bool(callback.data and callback.data.startswith("ocr:")))
async def image_to_text_callback(callback: CallbackQuery) -> None:
    if callback.message is None or callback.data is None:
        return
    parts = callback.data.split(":")
    if len(parts) != 2:
        await callback.answer("Bad callback data.")
        return
    try:
        post_id = int(parts[1])
    except ValueError:
        await callback.answer("Bad callback data.")
        return

    with get_session() as session:
        post = session.get(Post, post_id)
        if post is None:
            await callback.answer("Post is no longer available.")
            return
        for ocr_message in format_ocr_debug_messages(post):
            await callback.message.answer(ocr_message)
    await callback.answer()


@router.callback_query(lambda callback: bool(callback.data and callback.data.startswith("fav:")))
async def favorite_callback(callback: CallbackQuery) -> None:
    settings = get_settings(require_tokens=False)
    if not settings.enable_favorites:
        await callback.answer("Favorites are disabled.")
        return
    if callback.from_user is None or callback.data is None:
        await callback.answer("Bad callback data.")
        return
    parts = callback.data.split(":")
    if len(parts) != 3 or parts[1] not in {"add", "remove"}:
        await callback.answer("Bad callback data.")
        return
    try:
        post_id = int(parts[2])
    except ValueError:
        await callback.answer("Bad callback data.")
        return

    with get_session() as session:
        post = session.get(Post, post_id)
        if post is None:
            await callback.answer("Thread not found")
            return
        if parts[1] == "remove":
            remove_favorite(session, callback.from_user.id, post_id)
            answer_text = "Удалено из избранного"
        else:
            add_favorite(session, callback.from_user.id, post_id)
            if settings.enable_feedback:
                add_search_event(session, callback.from_user.id, post_id, "favorite_added")
            answer_text = "Добавлено в избранное"
        session.commit()
    await callback.answer(answer_text)


@router.callback_query(lambda callback: bool(callback.data and callback.data.startswith("similar:")))
async def similar_callback(callback: CallbackQuery) -> None:
    settings = get_settings(require_tokens=False)
    if not settings.enable_similar:
        await callback.answer("Similar threads are disabled.")
        return
    if callback.message is None or callback.from_user is None or callback.data is None:
        await callback.answer("Bad callback data.")
        return
    parts = callback.data.split(":")
    if len(parts) != 2:
        await callback.answer("Bad callback data.")
        return
    try:
        post_id = int(parts[1])
    except ValueError:
        await callback.answer("Bad callback data.")
        return

    with get_session() as session:
        posts = find_similar_posts(session, post_id, settings.results_per_page)
        if not posts:
            await callback.answer("Похожие треды не найдены")
            return
        if settings.enable_feedback:
            add_search_event(session, callback.from_user.id, post_id, "similar_clicked")
        session.commit()
        user_search_state[callback.from_user.id] = SearchState(
            query_id=SIMILAR_QUERY_ID,
            results=[post.id for post in posts],
        )
        await _send_post(callback.message, posts[0], index=0, total=len(posts), query_id=SIMILAR_QUERY_ID)
    await callback.answer()


@router.callback_query(lambda callback: bool(callback.data and callback.data.startswith("feedback:bad:")))
async def feedback_bad_callback(callback: CallbackQuery) -> None:
    settings = get_settings(require_tokens=False)
    if not settings.enable_feedback:
        await callback.answer("Feedback is disabled.")
        return
    if callback.from_user is None or callback.data is None:
        await callback.answer("Bad callback data.")
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Bad callback data.")
        return
    try:
        post_id = int(parts[2])
    except ValueError:
        await callback.answer("Bad callback data.")
        return

    with get_session() as session:
        post = session.get(Post, post_id)
        if post is None:
            await callback.answer("Thread not found")
            return
        add_search_event(session, callback.from_user.id, post_id, "disliked")
        session.commit()
    await callback.answer("Понял, буду показывать меньше похожего")


@router.callback_query(lambda callback: bool(callback.data and callback.data.startswith("result:")))
async def result_callback(callback: CallbackQuery) -> None:
    if callback.message is None or callback.from_user is None or callback.data is None:
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Bad callback data.")
        return
    try:
        query_id = int(parts[1])
        index = int(parts[2])
    except ValueError:
        await callback.answer("Bad callback data.")
        return

    with get_session() as session:
        state = user_search_state.get(callback.from_user.id)
        if state is None or state.query_id != query_id:
            if query_id <= 0:
                await callback.answer("Search state expired. Send the command again.")
                return
            settings = get_settings(require_tokens=False)
            query_row = get_search_query(session, query_id, callback.from_user.id)
            if query_row is None:
                await callback.answer("Search state expired. Send the query again.")
                return
            posts = search_posts(session, query_row.query, settings.results_per_page, 0)
            state = SearchState(query_id=query_id, results=[post.id for post in posts])
            user_search_state[callback.from_user.id] = state

        if index < 0 or index >= len(state.results):
            await callback.answer("No such result.")
            return

        post = session.get(Post, state.results[index])
        if post is None:
            await callback.answer("Result is no longer available.")
            return
        favorite_action = "remove" if query_id == FAVORITES_QUERY_ID else "add"
        await _send_post(
            callback.message,
            post,
            index=index,
            total=len(state.results),
            query_id=query_id,
            favorite_action=favorite_action,
        )
    await callback.answer()
