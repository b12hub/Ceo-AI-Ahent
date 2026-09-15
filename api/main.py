# api/main.py
"""
api/main.py

FastAPI application exposing:
  * POST /webhook              — Telegram sends updates here
  * POST /api/trigger-brief    — n8n cron trigger -> daily_brief() -> CEO's Telegram
  * POST /api/bottleneck-alerts — n8n (or anything else) -> forward alert to CEO's Telegram
  * GET  /dashboard            — Jinja2 dashboard (rendered here)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from aiogram.types import Update
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from agent.tools.registry import daily_brief, seed_company_documents
from api.deps import get_db, templates
from bot.config import INTERNAL_API_KEY, TELEGRAM_CE0_ID, WEBHOOK_SECRET, WEBHOOK_URL
from bot.dispatcher import bot, dp
from bot.utils import send_long_message
from models import Task, Decision, User

logger = logging.getLogger("api.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # --- startup ---
    # Ensure the company documents are seeded so RAG searches have baseline content.
    try:
        seed_company_documents()
    except Exception as exc:
        logger.exception("Failed to seed company documents on startup: %s", exc)

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


def verify_internal_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """
    Guard for the n8n-facing endpoints.
    """
    if INTERNAL_API_KEY is None:
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

    # feed_webhook_update processes the update through middleware and handlers
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


# ---------------------------------------------------------------------------
# Dashboard (Jinja2)
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_db)) -> HTMLResponse:
    """
    Renders the CEO dashboard. Provides complete Task and Decision objects.
    Template expects:
      - pending_tasks: list of {"task": Task, "assignee_name": str}
      - recent_decisions: list of {"decision": Decision, "logger_name": str}
    """
    # Pending tasks (status != 'completed')
    pending_tasks_raw = session.exec(
        select(Task).where(Task.status != "completed").order_by(Task.deadline.asc())
    ).all()

    pending_tasks = []
    for t in pending_tasks_raw:
        assignee_name = "—"
        if getattr(t, "assignee_id", None):
            assignee = session.get(User, t.assignee_id)
            if assignee:
                assignee_name = assignee.full_name
        pending_tasks.append({"task": t, "assignee_name": assignee_name})

    # Recent decisions (latest 10)
    recent_decisions_raw = session.exec(
        select(Decision).order_by(Decision.logged_at.desc()).limit(10)
    ).all()

    recent_decisions = []
    for d in recent_decisions_raw:
        logger_name = "—"
        if getattr(d, "logger_id", None):
            logger_user = session.get(User, d.logger_id)
            if logger_user:
                logger_name = logger_user.full_name
        recent_decisions.append({"decision": d, "logger_name": logger_name})

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"request": request, "pending_tasks": pending_tasks, "recent_decisions": recent_decisions}
    )