import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger
from sqlmodel import SQLModel, Field, Column
from pgvector.sqlalchemy import Vector

class User(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    telegram_id: Optional[int] = Field(default=None, sa_type=BigInteger, unique=True, index=True)
    full_name: str
    role: str = Field(default="user")
    is_ceo: bool = Field(default=False)
    email: Optional[str] = Field(default=None, unique=True, index=True)
    hashed_password: Optional[str] = Field(default=None)

class Task(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    description: str
    deadline: datetime
    status: str = Field(default="pending")
    assignee_id: uuid.UUID = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Decision(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    text: str
    context: str
    logged_at: datetime = Field(default_factory=datetime.utcnow)
    logger_id: uuid.UUID = Field(foreign_key="user.id")

class Meeting(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str
    scheduled_for: datetime
    summary: Optional[str] = None

class CompanyDocument(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str
    content_chunk: str
    embedding: list[float] = Field(sa_column=Column(Vector(768)))