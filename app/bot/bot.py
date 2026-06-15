from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher

from app.bot.handlers import router
from app.config import get_settings
from app.db.session import init_db


async def run_bot() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings(require_tokens=True)
    init_db()
    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)

