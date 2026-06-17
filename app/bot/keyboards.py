from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


SEARCH_BUTTON = "🔎 Search"
RANDOM_BUTTON = "🎲 Random"
LATEST_BUTTON = "🕓 Latest"
HELP_BUTTON = "❔ Help"
FAVORITE_ADD_BUTTON = "⭐ В избранное"
FAVORITE_REMOVE_BUTTON = "★ Убрать из списка"
SIMILAR_BUTTON = "🔎 Похожие"


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=SEARCH_BUTTON), KeyboardButton(text=RANDOM_BUTTON)],
            [KeyboardButton(text=LATEST_BUTTON), KeyboardButton(text=HELP_BUTTON)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Send a search query",
    )


def welcome_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=SEARCH_BUTTON, callback_data="search_help"),
                InlineKeyboardButton(text=RANDOM_BUTTON, callback_data="random"),
            ],
            [
                InlineKeyboardButton(text=LATEST_BUTTON, callback_data="latest"),
                InlineKeyboardButton(text=HELP_BUTTON, callback_data="help"),
            ],
        ]
    )


def result_keyboard(
    query_id: int,
    index: int,
    total: int,
    vk_url: str,
    post_id: int | None = None,
    original_url: str = "",
    favorite_action: str = "add",
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if index > 0:
        nav.append(InlineKeyboardButton(text="Back", callback_data=f"result:{query_id}:{index - 1}"))
    if index + 1 < total:
        nav.append(InlineKeyboardButton(text="Next", callback_data=f"result:{query_id}:{index + 1}"))
    if nav:
        buttons.append(nav)
    action_row = [
        InlineKeyboardButton(text=RANDOM_BUTTON, callback_data="random"),
        InlineKeyboardButton(text="Open VK", url=vk_url),
    ]
    if original_url:
        action_row.append(InlineKeyboardButton(text="Open 2ch", url=original_url))
    buttons.append(action_row)
    if post_id is not None:
        favorite_text = FAVORITE_REMOVE_BUTTON if favorite_action == "remove" else FAVORITE_ADD_BUTTON
        favorite_callback = f"fav:remove:{post_id}" if favorite_action == "remove" else f"fav:add:{post_id}"
        buttons.append(
            [
                InlineKeyboardButton(text=favorite_text, callback_data=favorite_callback),
                InlineKeyboardButton(text=SIMILAR_BUTTON, callback_data=f"similar:{post_id}"),
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(text="Image-to-text", callback_data=f"ocr:{post_id}"),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)
