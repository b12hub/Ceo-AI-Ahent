"""
agent/models_conversation.py

`models.py` (as provided) has no table for chat history, and the agent
loop needs one to reconstruct context across turns. This lives in its
own module rather than being folded into models.py so you can review it
separately before adding it to your Alembic migration.

Import this alongside models.py wherever SQLModel.metadata.create_all()
(or Alembic's autogenerate) is invoked, so the table actually gets created.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field


class ConversationMessage(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    role: str  # "user" | "assistant" | "tool"
    content: str
    tool_name: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)