"""
bot/handlers.py

/start, /help, /reset, and main text handler routing into agent.loop.run_agent_turn.
Includes HTTPS validation for Telegram Web Apps, executive response formatting,
and message-ID tracking so /reset can bulk-delete visible chat history.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from sqlmodel import delete, select

from agent.db import get_session
from agent.loop import run_agent_turn
from agent.models_bot_messages import BotSentMessage
from agent.models_conversation import ConversationMessage
from bot.config import DASHBOARD_BASE_URL
from bot.formatting import sanitize_for_telegram
from bot.utils import get_or_create_ceo_user, send_long_message

logger = logging.getLogger("bot.handlers")

router = Router(name="ceo-bot")


def _dashboard_keyboard() -> Optional[InlineKeyboardMarkup]:
    """Build inline keyboard for CEO Dashboard."""
    url = DASHBOARD_BASE_URL.rstrip("/")
    is_valid_https = (
        url.startswith("https://")
        and "localhost" not in url

        and "127.0.0.1" not in url
    )

    if is_valid_https:
        button = InlineKeyboardButton(
            text="📊 Open CEO Dashboard",
            web_app=WebAppInfo(url=f"{url}/dashboard"),
        )
    else:
        button = InlineKeyboardButton(
            text="🔗 Open Web Dashboard",
            url=f"{url}/dashboard"
            if url.startswith("http")
            else "http://localhost:8000/dashboard",
        )

    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def _track_sent_message(user_id, chat_id: int, message_id: int) -> None:
    """Record one outbound message ID so /reset can delete it later."""
    with get_session() as session:
        session.add(
            BotSentMessage(user_id=user_id, chat_id=chat_id, message_id=message_id)
        )
        session.commit()


async def _send_tracked(message: Message, text: str, **kwargs) -> Message:
    """Wrapper around message.answer() that also persists the sent message's ID."""
    kwargs.setdefault("parse_mode", "HTML")
    sent = await message.answer(text, **kwargs)

    with get_session() as session:
        user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)
        _track_sent_message(user.id, sent.chat.id, sent.message_id)

    return sent


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    with get_session() as session:
        user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)

    text = (
        f"👑 <b>Chief of Staff Online</b>\n"
        f"────────────────────────\n"
        f"Welcome back, <b>{user.full_name}</b>.\n\n"
        f"I am initialized and synchronized with your workspace records. "
        f"Ask me anything regarding active tasks, executive decisions, upcoming meetings, or documents.\n\n"
        f"⚡ <b>Quick Commands:</b>\n"
        f"• /help — Overview of capabilities\n"
        f"• /reset — Clear active context & start fresh\n\n"
        f"💡 <i>Try asking: \"Give me a daily brief\" or \"What's on my plate today?\"</i>"
    )
    await _send_tracked(message, text, reply_markup=_dashboard_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        f"📑 <b>Executive Assistant Capabilities</b>\n"
        f"────────────────────────\n"
        f"• <b>Daily Operations:</b> Briefings on tasks, meetings, and recent decisions.\n"
        f"• <b>Task Management:</b> Assign tasks and track deadlines across teams.\n"
        f"• <b>Decision Logging:</b> Record key corporate decisions with full context.\n"
        f"• <b>Knowledge Base:</b> Semantic search across company documentation.\n\n"
        f"⚙️ <b>System Commands:</b>\n"
        f"• /start — Display welcome card & dashboard link\n"
        f"• /reset — Flush conversation context and visible chat history"
    )
    await _send_tracked(message, text, reply_markup=_dashboard_keyboard())


@router.message(Command("reset"))
async def cmd_reset(message: Message) -> None:
    with get_session() as session:
        user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)

        # 1. Fetch all tracked bot messages
        tracked = session.exec(
            select(BotSentMessage).where(BotSentMessage.user_id == user.id)
        ).all()

        # 2. Clear conversation memory
        session.exec(
            delete(ConversationMessage).where(ConversationMessage.user_id == user.id)
        )
        session.commit()

    # 3. Bulk delete visible UI chat history (messages older than 48h will fail silently)
    deleted, failed = 0, 0
    for row in tracked:
        try:
            await message.bot.delete_message(chat_id=row.chat_id, message_id=row.message_id)
            deleted += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # Stay under Telegram's rate limits

    # 4. Clear the tracked messages from DB
    with get_session() as session:
        user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)
        session.exec(delete(BotSentMessage).where(BotSentMessage.user_id == user.id))
        session.commit()

    # 5. Delete the user's /reset command
    try:
        await message.delete()
    except Exception:
        pass

    text = (
        "🧹 <b>Context Reset Complete</b>\n"
        "────────────────────────\n"
        "Conversation memory and visible chat history cleared. Starting a clean session."
    )
    await _send_tracked(message, text)


@router.message(F.text)
async def handle_message(message: Message) -> None:
    """Main entrypoint: forwards free-text messages to the agent loop."""
    prompt = message.text or ""
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        with get_session() as session:
            user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)
            raw_response = await asyncio.to_thread(
                run_agent_turn, str(user.id), prompt, session
            )
    except Exception:
        logger.exception("run_agent_turn failed for prompt: %r", prompt)
        await _send_tracked(
            message,
            "⚠️ <b>Execution Error</b>\n"
            "An issue occurred processing that request. Please try again or run /reset.",
        )
        return

    response = sanitize_for_telegram(raw_response)

    # Ensure `send_long_message` returns the list of Sent Message objects
    sent_messages = await send_long_message(
        message.bot, message.chat.id, response, parse_mode="HTML"
    )

    with get_session() as session:
        user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)
        for sent in sent_messages or []:
            _track_sent_message(user.id, sent.chat.id, sent.message_id)