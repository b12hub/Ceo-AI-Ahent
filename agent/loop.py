"""
agent/loop.py

Ties together conversation history, the LLM client, and the tool
registry into a single turn-taking function.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlmodel import Session, select

from agent.llm import get_llm_response
from agent.prompt import SYSTEM_PROMPT
from agent.tools.registry import AVAILABLE_TOOLS, execute_tool
from agent.models_conversation import ConversationMessage

logger = logging.getLogger("agent.loop")

MAX_TOOL_ITERATIONS = 6  # hard stop against runaway tool-call loops
HISTORY_TURN_LIMIT = 20  # most recent messages pulled in as context


def _load_history(session: Session, user_uuid: uuid.UUID) -> list[dict[str, str]]:
    """
    Load recent user/assistant turns as plain OpenAI-style messages.
    Intermediate tool round-trips from prior turns are intentionally not
    replayed — only the final user message and final assistant answer of
    each past turn matter for context; replaying stale tool_call ids would
    require reconstructing an exact provider-specific shape that's brittle
    across a Groq/Google fallback.
    """
    rows = session.exec(
        select(ConversationMessage)
        .where(ConversationMessage.user_id == user_uuid)
        .where(ConversationMessage.role.in_(["user", "assistant"]))
        .order_by(ConversationMessage.created_at.desc())
        .limit(HISTORY_TURN_LIMIT)
    ).all()
    rows.reverse()  # chronological order
    return [{"role": row.role, "content": row.content} for row in rows]


def _persist(session: Session, user_uuid: uuid.UUID, role: str, content: str, tool_name: str | None = None) -> None:
    session.add(
        ConversationMessage(
            user_id=user_uuid,
            role=role,
            content=content,
            tool_name=tool_name,
        )
    )
    session.commit()


async def run_agent_turn(user_id: str, prompt: str, session: Session) -> str:
    """
    Execute one full conversational turn for `user_id`:

      1. Load recent history.
      2. Build [SYSTEM_PROMPT, ...history, user_message].
      3. Call the LLM with AVAILABLE_TOOLS.
      4. If tool calls come back, execute them via the registry dispatcher,
         append results, and loop until a final natural-language answer
         is produced (or MAX_TOOL_ITERATIONS is hit).
      5. Persist the user prompt and final assistant answer.

    Returns the final assistant response text.
    """
    user_uuid = uuid.UUID(str(user_id))

    history = _load_history(session, user_uuid)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": prompt},
    ]

    # Persist the user's message immediately so it's not lost if something
    # downstream fails mid-loop.
    _persist(session, user_uuid, "user", prompt)

    final_text: str | None = None

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = get_llm_response(messages, tools=AVAILABLE_TOOLS)

        if not response.has_tool_calls:
            final_text = response.content or ""
            break

        # Record the assistant's tool-call turn in the running message list
        # so the next LLM call has full context of what it asked for.
        messages.append(
            {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in response.tool_calls
                ],
            }
        )

        for tc in response.tool_calls:
            logger.info("Executing tool '%s' with args %s", tc.name, tc.arguments)
            result = await execute_tool(tc.name, tc.arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": result,
                }
            )
    else:
        # Loop exhausted without a final answer — surface this rather than
        # silently returning nothing, per the "never fabricate" rule.
        final_text = (
            "I wasn't able to reach a final answer within the allotted tool-call "
            "budget. Please rephrase or narrow the request."
        )
        logger.warning("MAX_TOOL_ITERATIONS (%d) reached for user %s", MAX_TOOL_ITERATIONS, user_id)

    _persist(session, user_uuid, "assistant", final_text or "")
    return final_text or ""