"""
api/dashboard.py

GET /dashboard — a read-only Jinja2 page showing outstanding tasks and
recent decisions. Linked from the bot's /start and /help via a Telegram
WebApp button.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from api.deps import get_db, templates
from models import Decision, Task, User

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_db)) -> HTMLResponse:
    pending_tasks = session.exec(
        select(Task, User.full_name)
        .join(User, Task.assignee_id == User.id)
        .where(Task.status != "completed")
        .order_by(Task.deadline.asc())
    ).all()

    recent_decisions = session.exec(
        select(Decision, User.full_name)
        .join(User, Decision.logger_id == User.id)
        .order_by(Decision.logged_at.desc())
        .limit(10)
    ).all()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "pending_tasks": pending_tasks,       # list[tuple[Task, str]]
            "recent_decisions": recent_decisions, # list[tuple[Decision, str]]
        },
    )
