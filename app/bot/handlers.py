from __future__ import annotations

from dataclasses import dataclass

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, Message

from app.bot.formatting import format_ocr_debug_messages, format_post_caption
from app.bot.keyboards import result_keyboard
from app.config import get_settings
from app.db.models import Post
from app.db.repositories import add_search_query, get_latest_posts, get_random_post, search_posts
from app.db.session import get_session


router = Router()


@dataclass
class SearchState:
    query_id: int
    results: list[int]


user_search_state: dict[int, SearchState] = {}


def _post_image_paths(post: Post) -> list[str]:
    return [image.local_path for image in sorted(post.images, key=lambda image: image.id)]


async def _send_post(message: Message, post: Post, index: int = 0, total: int = 1, query_id: int = 0) -> None:
    caption = format_post_caption(post, index=index, total=total)
    image_paths = _post_image_paths(post)
    keyboard = result_keyboard(query_id, index, total, post.vk_url)
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
        "Commands: /search, /random, /latest, /help."
    )


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Send text to search saved post text and OCR text. "
        "Use /random for a random thread and /latest for recent saved threads."
    )


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
