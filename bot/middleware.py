"""
bot/middleware.py

Restricts the entire bot to a single authorized user: the CEO (TELEGRAM_CE0_ID).
Extracts users across all Telegram update types and performs safe string/int checks.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update, User as TelegramUser

from bot.config import TELEGRAM_CE0_ID

logger = logging.getLogger("bot.middleware")

_USER_BEARING_FIELDS = (
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "callback_query",
    "inline_query",
    "chosen_inline_result",
    "shipping_query",
    "pre_checkout_query",
    "poll_answer",
    "my_chat_member",
    "chat_member",
    "chat_join_request",
)


def _extract_from_user(event: TelegramObject) -> TelegramUser | None:
    """Extracts TelegramUser object across all possible update types."""
    direct_user = getattr(event, "from_user", None)
    if direct_user is not None:
        return direct_user

    if isinstance(event, Update):
        for field_name in _USER_BEARING_FIELDS:
            inner = getattr(event, field_name, None)
            if inner is not None and getattr(inner, "from_user", None) is not None:
                return inner.from_user

    return None


class CEOOnlyMiddleware(BaseMiddleware):
    """Drops any update not originating from TELEGRAM_CE0_ID."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = _extract_from_user(event)

        incoming_id = str(user.id).strip() if user else ""
        expected_id = str(TELEGRAM_CE0_ID).strip()

        if not user or incoming_id != expected_id:
            logger.warning(
                "Unauthorized update dropped (received user_id=%s, expected=%s, username=%s)",
                incoming_id or "unknown",
                expected_id or "unset",
                getattr(user, "username", "unknown"),
            )
            return None

        return await handler(event, data)


# Alias class name for compatibility with dispatcher imports
CEOMiddleware = CEOOnlyMiddleware