from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiogram import Bot

from app.config import get_settings
from app.vk.client import VKClient


async def check_telegram() -> None:
    settings = get_settings(require_tokens=True)
    bot = Bot(settings.telegram_bot_token)
    try:
        me = await bot.get_me()
    finally:
        await bot.session.close()
    print(f"telegram ok: @{me.username} ({me.id})")


async def check_vk() -> None:
    settings = get_settings(require_tokens=True)
    client = VKClient(settings)
    posts = await client.fetch_wall_posts(offset=0, count=1)
    print(f"vk ok: received {len(posts)} post(s) from {settings.vk_group_domain}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Check Telegram and VK tokens without printing secrets.")
    parser.add_argument("--telegram", action="store_true", help="Check Telegram Bot API token.")
    parser.add_argument("--vk", action="store_true", help="Check VK API token.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    if not args.telegram and not args.vk:
        args.telegram = True
        args.vk = True
    if args.telegram:
        await check_telegram()
    if args.vk:
        await check_vk()


if __name__ == "__main__":
    asyncio.run(main())

