from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, Message

from app.bot.formatting import format_ocr_debug_messages, format_post_caption
from app.bot.keyboards import result_keyboard
from app.check_updates import CheckResult, check_for_new_threads
from app.config import get_settings
from app.db.models import Post
from app.db.repositories import add_search_query, get_latest_posts, get_random_post, search_posts
from app.db.session import get_session
from app.twoch.originals import extract_2ch_post_numbers, find_original_from_text


router = Router()
logger = logging.getLogger(__name__)
check_lock = asyncio.Lock()
LAZY_ORIGINAL_LOOKUP_TIMEOUT_SECONDS = 4.0


@dataclass
class SearchState:
    query_id: int
    results: list[int]


user_search_state: dict[int, SearchState] = {}


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


async def _send_post(message: Message, post: Post, index: int = 0, total: int = 1, query_id: int = 0) -> None:
    await _ensure_original_url(post)
    caption = format_post_caption(post, index=index, total=total)
    image_paths = _post_image_paths(post)
    keyboard = result_keyboard(query_id, index, total, post.vk_url, original_url=post.original_url)
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
    for ocr_message in format_ocr_debug_messages(post):
        await message.answer(ocr_message)


@router.message(Command("start"))
async def start(message: Message) -> None:
    await message.answer(
        "Hi. Send any search query, and I will look for saved VK thread images.\n"
        "Commands: /search, /random, /latest, /check, /help."
    )


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Send text to search saved post text and OCR text. "
        "Use /random for a random thread, /latest for recent saved threads, and /check to import new threads."
    )


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

    state = user_search_state.get(callback.from_user.id)
    if state is None or state.query_id != query_id:
        await callback.answer("Search state expired. Send the query again.")
        return
    if index < 0 or index >= len(state.results):
        await callback.answer("No such result.")
        return

    with get_session() as session:
        post = session.get(Post, state.results[index])
        if post is None:
            await callback.answer("Result is no longer available.")
            return
        await _send_post(callback.message, post, index=index, total=len(state.results), query_id=query_id)
    await callback.answer()
