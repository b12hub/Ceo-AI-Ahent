"""
agent/tools/registry.py

The 6 mandatory Chief-of-Staff tools, each backed by SQLModel queries
against the existing Postgres schema (models.py). Every tool:

  * opens its own `Session(engine)` via `agent.db.get_session()`
  * returns a plain string (what the LLM sees as the tool result)
  * is registered in `AVAILABLE_TOOLS` (OpenAI-style function schema,
    consumed directly by Groq and translated for Google GenAI in
    agent/llm.py) and in `TOOL_DISPATCH` (name -> callable)
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Optional

from dateutil import parser as dateparser
from sqlmodel import select

from agent.db import get_session
from models import CompanyDocument, Decision, Meeting, Task, User

logger = logging.getLogger("agent.tools")

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "models/text-embedding-004")
EMBEDDING_DIM = 768  # must match CompanyDocument.embedding's Vector(768)


# ---------------------------------------------------------------------------
# Embedding helper (used by search_company_docs)
# ---------------------------------------------------------------------------

def _embed_text(text: str) -> list[float]:
    """
    Generate a 768-dim embedding via Google GenAI, matching the dimension
    CompanyDocument.embedding was declared with. Kept separate from
    agent/llm.py because embeddings are a distinct API surface from chat
    completions and have no Groq equivalent.
    """
    from google import genai

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY / GEMINI_API_KEY is not set (required for embeddings)")

    client = genai.Client(api_key=api_key)
    result = client.models.embed_content(model=EMBEDDING_MODEL, contents=text)
    vector = result.embeddings[0].values
    if len(vector) != EMBEDDING_DIM:
        logger.warning(
            "Embedding dimension mismatch: got %d, expected %d. "
            "Check EMBEDDING_MODEL vs. the Vector(%d) column definition.",
            len(vector), EMBEDDING_DIM, EMBEDDING_DIM,
        )
    return vector


def _parse_uuid(value: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"'{field_name}' must be a valid UUID, got: {value!r}") from exc


# ---------------------------------------------------------------------------
# 1. daily_brief
# ---------------------------------------------------------------------------

def daily_brief() -> str:
    """Pending/overdue tasks, upcoming meetings, and recent decisions in one summary."""
    now = datetime.utcnow()
    with get_session() as session:
        overdue_and_pending = session.exec(
            select(Task)
            .where(Task.status == "pending")
            .order_by(Task.deadline.asc())
        ).all()

        upcoming_meetings = session.exec(
            select(Meeting)
            .where(Meeting.scheduled_for >= now)
            .order_by(Meeting.scheduled_for.asc())
            .limit(5)
        ).all()

        recent_decisions = session.exec(
            select(Decision).order_by(Decision.logged_at.desc()).limit(5)
        ).all()

        lines: list[str] = ["## Daily Brief", ""]

        lines.append("### Tasks (pending)")
        if not overdue_and_pending:
            lines.append("- None outstanding.")
        else:
            for t in overdue_and_pending:
                flag = " ⚠️ OVERDUE" if t.deadline < now else ""
                lines.append(f"- [{t.id}] {t.description} — due {t.deadline.isoformat()}{flag}")

        lines.append("")
        lines.append("### Upcoming meetings")
        if not upcoming_meetings:
            lines.append("- None scheduled.")
        else:
            for m in upcoming_meetings:
                lines.append(f"- [{m.id}] {m.title} — {m.scheduled_for.isoformat()}")

        lines.append("")
        lines.append("### Recent decisions")
        if not recent_decisions:
            lines.append("- None logged yet.")
        else:
            for d in recent_decisions:
                lines.append(f"- [{d.id}] {d.text} (logged {d.logged_at.isoformat()})")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. log_decision
# ---------------------------------------------------------------------------

def log_decision(text: str, context: str, logger_id: str) -> str:
    """Insert a new Decision record."""
    logger_uuid = _parse_uuid(logger_id, "logger_id")

    with get_session() as session:
        user = session.get(User, logger_uuid)
        if user is None:
            return f"Error: no user found with id {logger_id}. Decision was NOT logged."

        decision = Decision(
            text=text,
            context=context,
            logger_id=logger_uuid,
            logged_at=datetime.utcnow(),
        )
        session.add(decision)
        session.commit()
        session.refresh(decision)

        return (
            f"Decision logged successfully. id={decision.id}, "
            f"logged_at={decision.logged_at.isoformat()}, by={user.full_name}."
        )


# ---------------------------------------------------------------------------
# 3. search_decisions
# ---------------------------------------------------------------------------

def search_decisions(query: str) -> str:
    """Case-insensitive substring search across Decision.text and Decision.context."""
    like_pattern = f"%{query}%"

    with get_session() as session:
        results = session.exec(
            select(Decision)
            .where(Decision.text.ilike(like_pattern) | Decision.context.ilike(like_pattern))
            .order_by(Decision.logged_at.desc())
            .limit(10)
        ).all()

        if not results:
            return f"No decisions found matching '{query}'."

        lines = [f"Found {len(results)} decision(s) matching '{query}':", ""]
        for d in results:
            lines.append(f"- [{d.id}] {d.logged_at.isoformat()} — {d.text}\n  Context: {d.context}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. assign_task
# ---------------------------------------------------------------------------

def assign_task(description: str, assignee_id: str, deadline: str) -> str:
    """Create a new pending Task bound to assignee_id, with deadline parsed from natural text."""
    assignee_uuid = _parse_uuid(assignee_id, "assignee_id")

    try:
        parsed_deadline = dateparser.parse(deadline)
    except (dateparser.ParserError, ValueError, OverflowError) as exc:
        return f"Error: could not parse deadline '{deadline}': {exc}. Task was NOT created."

    if parsed_deadline is None:
        return f"Error: could not parse deadline '{deadline}'. Task was NOT created."

    with get_session() as session:
        assignee = session.get(User, assignee_uuid)
        if assignee is None:
            return f"Error: no user found with id {assignee_id}. Task was NOT created."

        task = Task(
            description=description,
            deadline=parsed_deadline,
            status="pending",
            assignee_id=assignee_uuid,
            created_at=datetime.utcnow(),
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        return (
            f"Task created. id={task.id}, assignee={assignee.full_name}, "
            f"deadline={task.deadline.isoformat()}, status={task.status}."
        )


# ---------------------------------------------------------------------------
# 5. summarize_document
# ---------------------------------------------------------------------------

def summarize_document(document_id: str) -> str:
    """
    Fetch a CompanyDocument's stored chunk. The "summary" here is a light,
    deterministic reduction (first N chars) — the outer agent loop is
    responsible for turning this raw chunk into a natural-language summary
    via the LLM, since this tool must never itself fabricate content.
    """
    doc_uuid = _parse_uuid(document_id, "document_id")

    with get_session() as session:
        doc = session.get(CompanyDocument, doc_uuid)
        if doc is None:
            return f"Error: no CompanyDocument found with id {document_id}."

        preview = doc.content_chunk[:400] + ("..." if len(doc.content_chunk) > 400 else "")
        return (
            f"Document '{doc.title}' (id={doc.id}).\n\n"
            f"Full chunk content:\n{doc.content_chunk}\n\n"
            f"(Preview for reference: {preview})\n\n"
            "Instruction to model: summarize the above content_chunk faithfully — "
            "do not add facts not present in it."
        )


# ---------------------------------------------------------------------------
# 6. search_company_docs
# ---------------------------------------------------------------------------

def search_company_docs(query: str, limit: int = 5) -> str:
    """Embed `query` and run a pgvector cosine-distance similarity search over CompanyDocument."""
    try:
        query_vector = _embed_text(query)
    except Exception as exc:  # noqa: BLE001
        return f"Error generating embedding for query '{query}': {exc}"

    with get_session() as session:
        # `.cosine_distance()` is provided by pgvector.sqlalchemy's Vector
        # comparator; lower distance = more similar. Falls back to the
        # `<->` (L2) operator via `.max_inner_product()`/`.l2_distance()`
        # if your pgvector version doesn't expose cosine_distance.
        results = session.exec(
            select(
                CompanyDocument,
                CompanyDocument.embedding.cosine_distance(query_vector).label("distance"),
            )
            .order_by(CompanyDocument.embedding.cosine_distance(query_vector))
            .limit(limit)
        ).all()

        if not results:
            return f"No company documents found matching '{query}'."

        lines = [f"Top {len(results)} document chunk(s) matching '{query}':", ""]
        for doc, distance in results:
            lines.append(f"- [{doc.id}] {doc.title} (distance={distance:.4f})\n  {doc.content_chunk[:300]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format — translated for Google
# GenAI inside agent/llm.py)
# ---------------------------------------------------------------------------

AVAILABLE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "daily_brief",
            "description": (
                "Get a structured summary of pending/overdue tasks, upcoming meetings, "
                "and recently logged decisions. Use this whenever the CEO asks for a "
                "status update, brief, or 'what's on my plate'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_decision",
            "description": "Log a new executive decision with its rationale/context. Use when the CEO makes or confirms a decision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The decision itself, stated concisely."},
                    "context": {"type": "string", "description": "Why the decision was made / relevant background."},
                    "logger_id": {"type": "string", "description": "UUID of the User logging the decision."},
                },
                "required": ["text", "context", "logger_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_decisions",
            "description": "Search previously logged decisions by keyword, before answering questions about past decisions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword or phrase to search for."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_task",
            "description": "Create and assign a new task to a team member with a deadline. Use when the CEO delegates work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "What needs to be done."},
                    "assignee_id": {"type": "string", "description": "UUID of the assignee User."},
                    "deadline": {"type": "string", "description": "Deadline in natural language or ISO format, e.g. 'next Friday 5pm' or '2026-09-20T17:00:00'."},
                },
                "required": ["description", "assignee_id", "deadline"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_document",
            "description": "Fetch a specific company document chunk by its ID before summarizing or quoting it. Never summarize a document from memory — always call this first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID of the CompanyDocument."},
                },
                "required": ["document_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_company_docs",
            "description": "Semantic search across company documents (policies, contracts, memos) using vector similarity. Use before answering any question that references company documents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language search query."},
                    "limit": {"type": "integer", "description": "Max number of results (default 5).", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
]

TOOL_DISPATCH: dict[str, Any] = {
    "daily_brief": daily_brief,
    "log_decision": log_decision,
    "search_decisions": search_decisions,
    "assign_task": assign_task,
    "summarize_document": summarize_document,
    "search_company_docs": search_company_docs,
}


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Dispatch a tool call by name, returning a string result (or an error string)."""
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return f"Error: unknown tool '{name}'."
    try:
        return fn(**arguments)
    except TypeError as exc:
        return f"Error: bad arguments for tool '{name}': {exc}"
    except Exception as exc:  # noqa: BLE001 - tool errors must surface to the model, not crash the loop
        logger.exception("Tool '%s' raised an unhandled exception", name)
        return f"Error executing tool '{name}': {exc}"