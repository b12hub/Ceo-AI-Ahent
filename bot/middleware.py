"""
bot/middleware.py

Authorization Middleware restricting bot interaction exclusively to users in ALLOWED_USERS.
Extracts Telegram user across message, callback query, and update types.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update, User as TelegramUser

from bot.config import ALLOWED_USERS, TELEGRAM_CE0_ID

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
    """Drops any update not originating from ALLOWED_USERS."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = _extract_from_user(event)

        if not user:
            logger.warning("Dropped update with missing user object.")
            return None

        user_id = user.id

        # Check against integer ALLOWED_USERS list or string TELEGRAM_CE0_ID fallback
        is_allowed = (
            user_id in ALLOWED_USERS
            or str(user_id).strip() == str(TELEGRAM_CE0_ID).strip()
        )

        if not is_allowed:
            logger.warning(
                "Unauthorized update dropped (user_id=%d, username=%s, allowed=%s)",
                user_id,
                getattr(user, "username", "unknown"),
                ALLOWED_USERS,
            )
            return None

        return await handler(event, data)


# Alias class names for compatibility
CEOMiddleware = CEOOnlyMiddleware
AuthMiddleware = CEOOnlyMiddleware