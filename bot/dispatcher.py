"""
bot/dispatcher.py

Instantiates the Aiogram Bot and Dispatcher, attaches the CEOOnlyMiddleware,
and registers the main handlers router from bot.handlers.
"""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import TELEGRAM_BOT_TOKEN
from bot.handlers import router as ceo_router
from bot.middleware import CEOOnlyMiddleware

logger = logging.getLogger("bot.dispatcher")

# Initialize the bot instance
bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

# Initialize the dispatcher
dp = Dispatcher()

# Attach the outer middleware to catch all incoming updates
dp.update.outer_middleware(CEOOnlyMiddleware())

# Register the handlers router
dp.include_router(ceo_router)
