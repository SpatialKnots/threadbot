from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def result_keyboard(query_id: int, index: int, total: int, vk_url: str) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    nav: list[InlineKeyboardButton] = []
    if index > 0:
        nav.append(InlineKeyboardButton(text="Back", callback_data=f"result:{query_id}:{index - 1}"))
    if index + 1 < total:
        nav.append(InlineKeyboardButton(text="Next", callback_data=f"result:{query_id}:{index + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append(
        [
            InlineKeyboardButton(text="Random", callback_data="random"),
            InlineKeyboardButton(text="Open VK", url=vk_url),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)

