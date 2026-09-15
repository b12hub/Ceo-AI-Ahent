"""
bot/handlers.py

/start, /help, /reset, and the main free-text handler that routes the
CEO's messages into agent.loop.run_agent_turn.
"""

from __future__ import annotations

import asyncio
import logging
import os

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from sqlmodel import delete

from agent.db import get_session
from agent.loop import run_agent_turn
from agent.models_conversation import ConversationMessage
from bot.config import DASHBOARD_BASE_URL
from bot.utils import get_or_create_ceo_user, send_long_message

logger = logging.getLogger("bot.handlers")

router = Router(name="ceo-bot")

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import TELEGRAM_BOT_TOKEN
from bot.handlers import router as ceo_router
from bot.middleware import CEOOnlyMiddleware

# Initialize the bot instance
bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

# Initialize the dispatcher
dp = Dispatcher()

# Attach the outer middleware to catch all incoming updates
dp.update.outer_middleware(CEOOnlyMiddleware())

# Register the handlers
dp.include_router(ceo_router)

def _dashboard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Open CEO Dashboard",
                    web_app=WebAppInfo(url=f"{DASHBOARD_BASE_URL.rstrip('/')}/dashboard"),
                )
            ]
        ]
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    with get_session() as session:
        user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)

    await message.answer(
        f"Welcome back, {user.full_name}. I'm your Chief of Staff — ask me about "
        "tasks, decisions, meetings, or company documents, or ask for your daily brief.\n\n"
        "Commands:\n"
        "/help — list commands\n"
        "/reset — clear our conversation history and start fresh",
        reply_markup=_dashboard_keyboard(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "I'm your Chief of Staff assistant. I can:\n"
        "• Give you a daily brief of tasks, meetings, and decisions\n"
        "• Log decisions and assign tasks\n"
        "• Search past decisions and company documents\n\n"
        "Commands:\n"
        "/start — welcome message and dashboard link\n"
        "/help — this message\n"
        "/reset — clear our conversation history and start fresh\n\n"
        "Or just type a message — e.g. \"what's on my plate today?\"",
        reply_markup=_dashboard_keyboard(),
    )


@router.message(Command("reset"))
async def cmd_reset(message: Message) -> None:
    with get_session() as session:
        user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)
        session.exec(delete(ConversationMessage).where(ConversationMessage.user_id == user.id))
        session.commit()

    await message.answer("Conversation history cleared. Starting fresh.")


@router.message(F.text)
async def handle_message(message: Message) -> None:
    """Main entrypoint: forwards free-text messages to the agent loop."""
    prompt = message.text or ""
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        with get_session() as session:
            user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)
            # run_agent_turn is fully synchronous (sync SQLModel session +
            # sync Groq/Google clients) — offload it so it never blocks
            # the aiogram event loop.
            response = await asyncio.to_thread(run_agent_turn, str(user.id), prompt, session)
    except Exception:  # noqa: BLE001 - never let an unhandled tool/LLM error kill the bot process
        logger.exception("run_agent_turn failed for prompt: %r", prompt)
        await message.answer(
            "Something went wrong processing that request. Please try again, "
            "or /reset if the issue persists."
        )
        return

    await send_long_message(message.bot, message.chat.id, response)
