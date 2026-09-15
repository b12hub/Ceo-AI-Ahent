"""
api/deps.py

Small shared pieces for the FastAPI layer: the Jinja2 templates instance
(one per process, per Jinja2Templates' own recommendation) and a
FastAPI-style dependency wrapping agent.db.get_session's contextmanager.
"""

from __future__ import annotations

from typing import Iterator

from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from agent.db import get_session

templates = Jinja2Templates(directory="templates")


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a Session, closes it when the request ends."""
    with get_session() as session:
        yield session
