"""
agent/db.py

Single shared SQLModel engine for the whole agent package. Import
`get_session()` from here rather than constructing engines ad hoc, so
connection pooling stays consistent across tools.py, loop.py, and the
Telegram bot entrypoint.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, create_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg2://ceo_admin:securepassword123@localhost:5433/ceo_agent_db"
)

# pool_pre_ping avoids "server closed the connection unexpectedly" after idle
# periods, which matters for a bot process that may sit quiet for hours.
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)


@contextmanager
def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session