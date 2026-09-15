# bot/handlers.py
"""
bot/handlers.py

/start, /help, /about, /reset, file upload and free-text handlers, and Human-in-the-Loop CallbackQuery handlers.

Robust PDF handling: downloads to BytesIO and performs synchronous PDF parsing inside asyncio.to_thread()
to avoid blocking the event loop. Logs full tracebacks on parse errors and surfaces exception text to the
user for easier debugging.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
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
from sqlmodel import delete, select, Session

from agent.db import get_session
from agent.loop import run_agent_turn
from agent.models_bot_messages import BotSentMessage
from agent.models_conversation import ConversationMessage
from agent.pending_actions import pop_pending
from agent.tools import registry
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
    """Build InlineKeyboardMarkup with Confirm and Cancel buttons for HITL guardrails.

    Uzbek labels and callback_data use the new `confirm_action:` / `cancel_action:` prefixes.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"confirm_action:{action_id}"),
                InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"cancel_action:{action_id}"),
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
        f"• /about — Bot haqida qisqacha ma'lumot\n"
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
        f"• /about — Bot haqida qisqacha ma'lumot\n"
        f"• /reset — Muloqot xotirasi va chat tarixini o'chirish"
    )
    await _send_tracked(message, text, reply_markup=_dashboard_keyboard())


@router.message(Command("about"))
async def cmd_about(message: Message) -> None:
    text = (
        f"📘 <b>Bot haqida</b>\n"
        f"────────────────────────\n"
        f"Men sizning Bosh Shtab Boshlig'i yordamchingizman — kompaniya topshiriqlari, qarorlar, uchrashuvlar va hujjatlar bo'yicha yordam beraman.\n\n"
        f"<b>Mavjud Imkoniyatlar:</b>\n"
        f"• 📊 <b>Kunlik Brifing</b> (/brief yoki 'Kunlik brifingni ber') — Bugungi uchrashuvlar va bajarilmagan topshiriqlar xulosasi.\n"
        f"• 📝 <b>Topshiriq biriktirish</b> — Xodimlarga muddatli vazifalar belgilash (tasdiqlash bilan).\n"
        f"• 📌 <b>Qarorlarni ro'yxatga olish va qidirish</b> — Muhim strategik qarorlarni saqlash va qidirish.\n"
        f"• 📄 <b>Hujjatlarni tahlil qilish va RAG</b> — Yuklangan PDF/fayllarni umumlashtirish va kompaniya strategiyasidan javob topish.\n"
        f"• 🖥 <b>CEO Boshqaruv Paneli</b> — Mini-App orqali topshiriqlar va qarorlar monitoringi.\n"
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


@router.callback_query(F.data.startswith("confirm_action:"))
async def handle_action_confirm(callback: CallbackQuery) -> None:
    """Execute a pending write action after CEO confirmation."""
    pending_id = callback.data.split(":", 1)[1] if callback.data else ""
    await callback.answer("Amal tasdiqlandi. Ijro etilmoqda...", show_alert=False)

    pending = pop_pending(pending_id)
    if pending is None:
        if callback.message and isinstance(callback.message, Message):
            await callback.message.edit_text("❌ <b>Xato:</b> Saqlangan amal topilmadi yoki allaqachon bajarilgan.")
        return

    name = pending.get("name")
    args = pending.get("arguments", {})

    # Call the actual implementation (these functions commit internally)
    try:
        if name == "assign_task":
            result = await registry.assign_task(**args)
        elif name == "log_decision":
            result = await registry.log_decision(**args)
        else:
            result = f"Noma'lum amal: {name}"
    except Exception as exc:
        logger.exception("Failed to execute pending action %s: %s", pending_id, exc)
        result = f"Xatolik: Amalni bajarishda xatolik yuz berdi: {exc}"

    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text(f"✅ <b>Amal bajarildi:</b>\n{sanitize_for_telegram(result)}", parse_mode="HTML")


@router.callback_query(F.data.startswith("cancel_action:"))
async def handle_action_cancel(callback: CallbackQuery) -> None:
    """Cancel a pending write action when CEO rejects it."""
    pending_id = callback.data.split(":", 1)[1] if callback.data else ""
    # Remove from store if present
    _ = pop_pending(pending_id)
    await callback.answer("Amal bekor qilindi.", show_alert=False)
    if callback.message and isinstance(callback.message, Message):
        await callback.message.edit_text("❌ <b>Amal bekor qilindi.</b>\nBu amal bazaga kiritilmadi.")


# --- Document and free-text handler with robust PDF extraction ---
async def _extract_pdf_text_bytes(data: bytes) -> str:
    """Extract text from PDF bytes using pdfplumber or PyPDF2 inside a thread."""
    def _sync_extract():
        # Try pdfplumber first (better layout), then fall back to PyPDF2
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                pages = [p.extract_text() or "" for p in pdf.pages]
                return "\n".join(pages)
        except Exception:
            # Fallback PyPDF2
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(data))
            texts = []
            for page in reader.pages:
                try:
                    texts.append(page.extract_text() or "")
                except Exception:
                    texts.append("")
            return "\n".join(texts)

    return await asyncio.to_thread(_sync_extract)


@router.message()
async def handle_message(message: Message) -> None:
    """Main entrypoint: handles documents and free-text messages, forwards to agent loop."""
    # 1) File upload handling: if a document is attached, try to extract text and route to summarization
    if getattr(message, "document", None):
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        buf = io.BytesIO()
        try:
            # Download file into memory (aiogram Bot/File download supports BytesIO)
            # Use message.bot to avoid circular imports
            await message.bot.download(message.document, destination=buf)
            data = buf.getvalue()
            # Extract PDF text inside a thread to avoid blocking
            try:
                extracted = await _extract_pdf_text_bytes(data)
            except Exception as e:
                logger.error(f"PDF Parsing Error: {e}", exc_info=True)
                await _send_tracked(message, f"⚠️ <b>Hujjatni qayta ishlashda xatolik yuz berdi:</b> {str(e)}")
                return

            if not extracted.strip():
                await _send_tracked(message, "⚠️ <b>Hujjatdan matn olinmadi.</b> Iltimos, matnli fayl yoki PDF yuboring.")
                return

            # Limit size passed into model for safety
            text_for_model = extracted[:30000]
            prompt = (
                "Iltimos, quyidagi hujjat matnini executive xulosa qilib bering (qat'iy o'zbek tilida, qisqa va professional):\n\n"
                + text_for_model
            )

            with get_session() as session:
                user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)
                raw_response = await run_agent_turn(str(user.id), prompt, session)

            response = sanitize_for_telegram(raw_response)
            sent_messages = await send_long_message(message.bot, message.chat.id, response, parse_mode="HTML")
            with get_session() as session:
                user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)
                for sent in sent_messages or []:
                    _track_sent_message(user.id, sent.chat.id, sent.message_id)
            return
        except Exception as exc:
            logger.error(f"Failed to process uploaded document: {exc}", exc_info=True)
            await _send_tracked(message, f"⚠️ <b>Hujjatni qayta ishlashda xatolik yuz berdi:</b> {str(exc)}")
            return

    # 2) Normal free-text message path
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

    # If the agent returned a pending-action marker, render confirmation keyboard
    if isinstance(raw_response, str) and raw_response.startswith("[PENDING_ACTION:"):
        m = re.match(r"^\[PENDING_ACTION:([0-9a-fA-F]+)\]\n(.*)$", raw_response, re.S)
        if m:
            pending_id, preview = m.group(1), m.group(2)
            keyboard = build_confirmation_keyboard(pending_id)
            await _send_tracked(message, preview, reply_markup=keyboard)
            return

    response = sanitize_for_telegram(raw_response)

    sent_messages = await send_long_message(
        message.bot, message.chat.id, response, parse_mode="HTML"
    )

    with get_session() as session:
        user = get_or_create_ceo_user(session, full_name=message.from_user.full_name)
        for sent in sent_messages or []:
            _track_sent_message(user.id, sent.chat.id, sent.message_id)