"""
agent/db.py

Single shared SQLModel engines (synchronous and asynchronous) for the agent package.
Provides get_session() and get_async_session() with expire_on_commit=False.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator

from sqlmodel import Session, create_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg2://ceo_admin:securepassword123@localhost:5432/ceo_agent_db"
)

# Synchronous engine
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)

_async_engine = None
_async_session_maker = None


def _init_async_engine():
    global _async_engine, _async_session_maker
    if _async_engine is None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        async_url = os.environ.get(
            "ASYNC_DATABASE_URL",
            DATABASE_URL.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace("postgresql://", "postgresql+asyncpg://")
        )
        _async_engine = create_async_engine(async_url, echo=False, pool_pre_ping=True)
        _async_session_maker = sessionmaker(
            bind=_async_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _async_engine, _async_session_maker


@contextmanager
def get_session() -> Iterator[Session]:
    """Synchronous session context manager."""
    with Session(engine, expire_on_commit=False) as session:
        yield session


@asynccontextmanager
async def get_async_session() -> AsyncIterator[Any]:
    """Asynchronous session context manager with fallback."""
    try:
        _, session_maker = _init_async_engine()
        async with session_maker() as session:
            yield session
    except ModuleNotFoundError:
        # Fallback if asyncpg driver is not installed in the environment
        with Session(engine, expire_on_commit=False) as session:
            yield session