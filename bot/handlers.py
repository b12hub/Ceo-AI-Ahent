"""
bot/handlers.py

/start, /help, /reset, free-text handlers, and Human-in-the-Loop CallbackQuery handlers.
Includes HTTPS validation for Telegram Web Apps, executive response formatting,
message-ID tracking, TelegramBadRequest handling, and Uzbek localization.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
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
    """Build inline keyboard for CEO Dashboard in Uzbek."""
    url = DASHBOARD_BASE_URL.rstrip("/")
    is_valid_https = (
        url.startswith("https://")
        and "localhost" not in url
        and "127.0.0.1" not in url
    )

    if is_valid_https:
        button = InlineKeyboardButton(
            text="📊 CEO Boshqaruv Panelini Ochish",
            web_app=WebAppInfo(url=f"{url}/dashboard"),
        )
    else:
        button = InlineKeyboardButton(
            text="🔗 Veb Boshqaruv Paneli",
            url=f"{url}/dashboard"
            if url.startswith("http")
            else "http://localhost:8000/dashboard",
        )

    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def build_confirmation_keyboard(action_id: str) -> InlineKeyboardMarkup:
    """Build InlineKeyboardMarkup with Confirm and Cancel buttons for HITL guardrails."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Confirm", callback_data=f"confirm:{action_id}"),
                InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel:{action_id}"),
            ]
        ]
    )


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
        f"👑 <b>Bosh Shtab Boshlig'i Tizimda</b>\n"
        f"────────────────────────\n"
        f"Xush kelibsiz, <b>{user.full_name}</b>.\n\n"
        f"Men sizning Bosh Shtab Boshlig'i yordamchingizman. "
        f"Kompaniya topshiriqlari, strategik qarorlar, uchrashuvlar va hujjatlar bo'yicha savollaringizga javob berishga tayyorman.\n\n"
        f"⚡ <b>Tezkor Buyruqlar:</b>\n"
        f"• /help — Tizim imkoniyatlari sharhi\n"
        f"• /reset — Muloqot tarixini tozalash va yangidan boshlash\n\n"
        f"💡 <i>Sinab ko'ring: \"Bugungi kunlik brifingni ber\" yoki \"Mening zimmamda qanday topshiriqlar bor?\"</i>"
    )
    await _send_tracked(message, text, reply_markup=_dashboard_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        f"📑 <b>Bosh Shtab Boshlig'ining Imkoniyatlari</b>\n"
        f"────────────────────────\n"
        f"• <b>Kunlik Operatsiyalar:</b> Topshiriqlar, uchrashuvlar va qarorlar brifingi.\n"
        f"• <b>Topshiriqlar Boshqaruvi:</b> Topshiriqlarni biriktirish va muddatlarini kuzatish.\n"
        f"• <b>Qarorlarni Qayd Etish:</b> Kompaniya strategik qarorlarini to'liq konteksti bilan saqlash.\n"
        f"• <b>Bilimlar Bazasi:</b> Kompaniya hujjatlari bo'yicha semantik qidiruv.\n\n"
        f"⚙️ <b>Tizim Buyruqlari:</b>\n"
        f"• /start — Xush kelibsiz xabari va panel havolasi\n"
        f"• /reset — Muloqot xotirasi va chat tarixini o'chirish"
    )
    await _send_tracked(message, text, reply_markup=_dashboard_keyboard())


@router.message(Command("reset"))
async def cmd_reset(message: Message) -> None:
    """Clear entire conversation memory from DB and bulk-delete tracked messages in Telegram UI."""
    with get_session() as session:
        user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)

        # 1. Extract primitive (chat_id, message_id) tuples while session is active
        rows = session.exec(
            select(BotSentMessage.chat_id, BotSentMessage.message_id)
            .where(BotSentMessage.user_id == user.id)
        ).all()
        to_delete: list[tuple[int, int]] = [(r[0], r[1]) for r in rows]

        # 2. Clear conversation memory from database
        session.exec(
            delete(ConversationMessage).where(ConversationMessage.user_id == user.id)
        )
        session.commit()

    # 3. Bulk delete visible Telegram chat history using native primitive ints
    deleted, failed = 0, 0
    for chat_id, message_id in to_delete:
        try:
            await message.bot.delete_message(chat_id=chat_id, message_id=message_id)
            deleted += 1
        except TelegramBadRequest as exc:
            logger.debug("Could not delete message %s: %s", message_id, exc)
            failed += 1
        except Exception as exc:
            logger.debug("Unexpected error deleting message %s: %s", message_id, exc)
            failed += 1
        await asyncio.sleep(0.03)

    # 4. Clear the tracked messages table in database
    with get_session() as session:
        user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)
        session.exec(delete(BotSentMessage).where(BotSentMessage.user_id == user.id))
        session.commit()

    # 5. Delete the triggering /reset command message
    try:
        await message.delete()
    except Exception:
        pass

    text = (
        "🧹 <b>Suhbat Tarixi Tozalandi</b>\n"
        "────────────────────────\n"
        "Barcha muloqot xotirasi va xabarlar tarixi o'chirildi. Yangi seans boshlandi."
    )
    await _send_tracked(message, text)


@router.callback_query(F.data.startswith("confirm:"))
async def handle_action_confirm(callback: CallbackQuery) -> None:
    """Handle Human-in-the-Loop confirmation callback."""
    action_data = callback.data.split(":", 1)[1] if callback.data else ""
    await callback.answer("Amal tasdiqlandi va saqlandi.", show_alert=False)
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"✅ <b>Amal Tasdiqlandi va Bajarildi:</b>\n<code>{action_data}</code>",
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("cancel:"))
async def handle_action_cancel(callback: CallbackQuery) -> None:
    """Handle Human-in-the-Loop cancellation callback."""
    action_data = callback.data.split(":", 1)[1] if callback.data else ""
    await callback.answer("Amal bekor qilindi.", show_alert=False)
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"❌ <b>Amal Bekor Qilindi:</b>\n<code>{action_data}</code>",
            parse_mode="HTML"
        )


@router.message(F.text)
async def handle_message(message: Message) -> None:
    """Main entrypoint: forwards free-text messages to the agent loop."""
    prompt = message.text or ""
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        with get_session() as session:
            user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)
            raw_response = await run_agent_turn(str(user.id), prompt, session)
    except Exception:
        logger.exception("run_agent_turn failed for prompt: %r", prompt)
        await _send_tracked(
            message,
            "⚠️ <b>Bajarishda Xatolik</b>\n"
            "So'rovni qayta ishlashda xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring yoki /reset buyrug'ini yuboring.",
        )
        return

    response = sanitize_for_telegram(raw_response)

    sent_messages = await send_long_message(
        message.bot, message.chat.id, response, parse_mode="HTML"
    )

    with get_session() as session:
        user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)
        for sent in sent_messages or []:
            _track_sent_message(user.id, sent.chat.id, sent.message_id)