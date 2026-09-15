"""
bot/utils.py

Small helpers shared between bot/handlers.py and api/main.py (the n8n
webhook endpoints send Telegram messages outside of any handler context,
so they need the same "get/create the CEO's User row" and "don't exceed
Telegram's 4096-char message limit" logic without importing aiogram
handler internals).
"""

from __future__ import annotations

from aiogram import Bot
from sqlmodel import Session, select
from typing import List, Optional
from aiogram.types import Message
from bot.config import TELEGRAM_CE0_ID
from models import User

TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def get_or_create_ceo_user(session: Session, full_name: str | None = None) -> User:
    """
    Resolve the CEO's internal `User` row by Telegram id, creating it on
    first contact if it doesn't exist yet. Task/Decision rows are keyed off
    `User.id` (a UUID), not the Telegram id directly, so every handler that
    calls into agent.loop.run_agent_turn or the tool registry needs this
    mapping first.
    """
    user = session.exec(select(User).where(User.telegram_id == TELEGRAM_CE0_ID)).first()
    if user is not None:
        return user

    user = User(
        telegram_id=TELEGRAM_CE0_ID,
        full_name=full_name or "CEO",
        role="CEO",
        is_ceo=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user





async def send_long_message(
        bot: Bot,
        chat_id: int,
        text: str,
        parse_mode: Optional[str] = "HTML"
) -> List[Message]:
    """Send `text` to `chat_id`, splitting on Telegram's 4096-char limit, and returning sent messages."""
    if not text:
        return []

    sent_messages = []
    for start in range(0, len(text), TELEGRAM_MAX_MESSAGE_LENGTH):
        chunk = text[start: start + TELEGRAM_MAX_MESSAGE_LENGTH]
        msg = await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode)
        sent_messages.append(msg)

    return sent_messages
