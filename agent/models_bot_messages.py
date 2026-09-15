"""
bot/models_bot_messages.py

Database table for tracking outbound Telegram message IDs.
This allows the /reset command to bulk-delete the bot's previous
responses from the Telegram UI.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger
from sqlmodel import SQLModel, Field, Column

class BotSentMessage(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)

    # Telegram specific tracking
    chat_id: int = Field(sa_column=Column(BigInteger()))
    message_id: int

    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)