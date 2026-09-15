"""
api/main.py

FastAPI application exposing:
  * POST /webhook              — Telegram sends updates here
  * POST /api/trigger-brief    — n8n cron trigger -> daily_brief() -> CEO's Telegram
  * POST /api/bottleneck-alerts — n8n (or anything else) -> forward alert to CEO's Telegram
  * GET  /dashboard            — Jinja2 dashboard (registered via api/dashboard.py)

Run with: uvicorn api.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from aiogram.types import Update
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.tools.registry import daily_brief
from api.dashboard import router as dashboard_router
from bot.config import INTERNAL_API_KEY, TELEGRAM_CE0_ID, WEBHOOK_SECRET, WEBHOOK_URL
from bot.dispatcher import bot, dp
from bot.utils import send_long_message

logger = logging.getLogger("api.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # --- startup ---
    if WEBHOOK_URL:
        await bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
        )
        logger.info("Telegram webhook set to %s", WEBHOOK_URL)
    else:
        logger.warning("WEBHOOK_URL not set — skipping bot.set_webhook() on startup")

    yield

    # --- shutdown ---
    await bot.delete_webhook()
    await bot.session.close()
    logger.info("Bot webhook deleted and session closed")


app = FastAPI(title="CEO_AI_test_bot API", lifespan=lifespan)
app.include_router(dashboard_router)

# Serve compiled Tailwind / static assets if you add any under ./static
# (kept optional: mounting a missing directory raises at import time, so
# only enable this once ./static actually exists in your deployment).
# app.mount("/static", StaticFiles(directory="static"), name="static")


def verify_internal_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """
    Guard for the n8n-facing endpoints. Both endpoints can message the CEO
    directly on Telegram, so — unlike the webhook, which Telegram itself
    signs via WEBHOOK_SECRET — they need their own shared-secret check or
    anyone who finds the URL can spam the CEO's phone.
    """
    if INTERNAL_API_KEY is None:
        # No key configured: fail closed rather than silently running unauthenticated.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="INTERNAL_API_KEY is not configured on the server.",
        )
    if x_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")


# ---------------------------------------------------------------------------
# Telegram webhook
# ---------------------------------------------------------------------------

@app.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid secret token.")

    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": bot})

    # feed_webhook_update processes the update through CEOOnlyMiddleware and
    # the routers registered in bot/dispatcher.py, then returns — Telegram
    # just needs a fast 200 OK, it doesn't care about the handler's result.
    await dp.feed_webhook_update(bot, update)
    return {"ok": True}


# ---------------------------------------------------------------------------
# n8n integrations
# ---------------------------------------------------------------------------

@app.post("/api/trigger-brief")
async def trigger_brief(_: None = Depends(verify_internal_api_key)) -> dict[str, str]:
    """n8n calls this on a cron schedule; runs daily_brief() and pushes it to the CEO."""
    brief_text = await daily_brief()
    await send_long_message(bot, TELEGRAM_CE0_ID, brief_text)
    return {"status": "sent"}


class BottleneckAlertPayload(BaseModel):
    alert: str = Field(..., min_length=1, description="Alert body to forward to the CEO.")
    source: str | None = Field(default=None, description="Where the alert originated, e.g. 'n8n:pipeline-monitor'.")
    severity: str | None = Field(default=None, description="e.g. 'low' | 'medium' | 'high' | 'critical'.")


@app.post("/api/bottleneck-alerts")
async def bottleneck_alert(
    payload: BottleneckAlertPayload,
    _: None = Depends(verify_internal_api_key),
) -> dict[str, str]:
    """Immediately forward an operational alert to the CEO's Telegram chat."""
    header = "🚨 Bottleneck Alert"
    if payload.severity:
        header += f" [{payload.severity.upper()}]"
    if payload.source:
        header += f" — {payload.source}"

    text = f"{header}\n\n{payload.alert}"
    await send_long_message(bot, TELEGRAM_CE0_ID, text)
    return {"status": "sent"}
