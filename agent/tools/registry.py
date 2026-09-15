"""
agent/tools/registry.py

The 6 mandatory Chief-of-Staff tools, each backed by SQLModel / PostgreSQL query logic.
Supports async execution with `get_async_session()` and fallback to `get_session()`.

Registered Tools:
  1. daily_brief()
  2. log_decision(text, context, logger_id)
  3. search_decisions(query)
  4. assign_task(description, assignee_id, deadline)
  5. summarize_document(file_path_or_bytes)
  6. search_company_docs(query, limit)
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Optional, Union

from dateutil import parser as dateparser
from sqlmodel import select

from agent.db import get_session, get_async_session
from models import CompanyDocument, Decision, Meeting, Task, User

logger = logging.getLogger("agent.tools")

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "models/text-embedding-004")
EMBEDDING_DIM = 768  # must match CompanyDocument.embedding's Vector(768)


def _embed_text(text: str) -> list[float]:
    """Generate a 768-dim embedding via Google GenAI."""
    from google import genai

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY / GEMINI_API_KEY is not set (required for embeddings)")

    client = genai.Client(api_key=api_key)
    result = client.models.embed_content(model=EMBEDDING_MODEL, contents=text)
    vector = result.embeddings[0].values
    if len(vector) != EMBEDDING_DIM:
        logger.warning(
            "Embedding dimension mismatch: got %d, expected %d.",
            len(vector), EMBEDDING_DIM
        )
    return vector


def _parse_uuid(value: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"'{field_name}' must be a valid UUID, got: {value!r}") from exc


def _extract_text_from_input(file_path_or_bytes: Union[str, bytes]) -> str:
    """Extract text from file path string or raw bytes (TXT, PDF)."""
    if isinstance(file_path_or_bytes, bytes):
        try:
            return file_path_or_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(file_path_or_bytes))
                return "\n".join(page.extract_text() or "" for page in reader.pages)
            except Exception as exc:
                logger.warning("Could not parse PDF bytes: %s", exc)
                return file_path_or_bytes.decode("latin1", errors="ignore")[:2000]
    elif isinstance(file_path_or_bytes, str):
        if os.path.exists(file_path_or_bytes):
            if file_path_or_bytes.lower().endswith(".pdf"):
                try:
                    import pypdf
                    reader = pypdf.PdfReader(file_path_or_bytes)
                    return "\n".join(page.extract_text() or "" for page in reader.pages)
                except Exception as exc:
                    logger.warning("Could not parse PDF file %s: %s", file_path_or_bytes, exc)
            with open(file_path_or_bytes, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        return file_path_or_bytes
    return str(file_path_or_bytes)


# ---------------------------------------------------------------------------
# 1. daily_brief
# ---------------------------------------------------------------------------

async def daily_brief() -> str:
    """
    Fetch a executive summary of pending tasks, today's upcoming meetings, and recent decisions.
    
    Returns:
        Formatted summary string in Uzbek HTML format.
    """
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

        lines: list[str] = ["<b>📊 Kunlik Brifing</b>", ""]

        lines.append("<b>📋 Topshiriqlar (kutilmoqda):</b>")
        if not overdue_and_pending:
            lines.append("• Bajarilmagan topshiriqlar mavjud emas.")
        else:
            for t in overdue_and_pending:
                flag = " ⚠️ MUDDATI O'TGAN" if t.deadline < now else ""
                lines.append(f"• [{t.id}] {t.description} — muddati: {t.deadline.isoformat()}{flag}")

        lines.append("")
        lines.append("<b>📅 Kutilayotgan uchrashuvlar:</b>")
        if not upcoming_meetings:
            lines.append("• Rejalashtirilgan uchrashuvlar mavjud emas.")
        else:
            for m in upcoming_meetings:
                lines.append(f"• [{m.id}] {m.title} — {m.scheduled_for.isoformat()}")

        lines.append("")
        lines.append("<b>📝 So'nggi qarorlar:</b>")
        if not recent_decisions:
            lines.append("• Hozircha qarorlar kiritilmagan.")
        else:
            for d in recent_decisions:
                lines.append(f"• [{d.id}] {d.text} (kiritilgan vaqti: {d.logged_at.isoformat()})")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. log_decision
# ---------------------------------------------------------------------------

async def log_decision(text: str, context: str, logger_id: str = "") -> str:
    """
    Log an executive decision into the decisions table.
    
    Args:
        text: Concise statement of the decision made.
        context: Background rationale and context for the decision.
        logger_id: Optional UUID string of the logging user.
    """
    with get_session() as session:
        user = None
        if logger_id:
            try:
                logger_uuid = _parse_uuid(logger_id, "logger_id")
                user = session.get(User, logger_uuid)
            except ValueError:
                pass
        
        if user is None:
            user = session.exec(select(User).where(User.is_ceo == True)).first()

        logger_uuid = user.id if user else uuid.uuid4()
        user_name = user.full_name if user else "CEO"

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
            f"Qaror muvaffaqiyatli saqlandi. id={decision.id}, "
            f"vaqti={decision.logged_at.isoformat()}, kim tomonidan={user_name}."
        )


# ---------------------------------------------------------------------------
# 3. search_decisions
# ---------------------------------------------------------------------------

async def search_decisions(query: str) -> str:
    """
    Search past decisions using SQL ILIKE substring search over text and context.
    
    Args:
        query: Keyword or phrase to search for.
    """
    like_pattern = f"%{query}%"

    with get_session() as session:
        results = session.exec(
            select(Decision)
            .where(Decision.text.ilike(like_pattern) | Decision.context.ilike(like_pattern))
            .order_by(Decision.logged_at.desc())
            .limit(10)
        ).all()

        if not results:
            return f"'{query}' so'rovi bo'yicha hech qanday qaror topilmadi."

        lines = [f"'{query}' so'rovi bo'yicha {len(results)} ta qaror topildi:", ""]
        for d in results:
            lines.append(f"• [{d.id}] {d.logged_at.isoformat()} — {d.text}\n  Kontekst: {d.context}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. assign_task
# ---------------------------------------------------------------------------

async def assign_task(person: str, text: str, deadline: str) -> str:
    """
    Create and assign a new task to a team member with a specified deadline.
    
    Args:
        person: Name or UUID of the assignee.
        text: Description of the task to be completed.
        deadline: Deadline date/time string.
    """
    try:
        parsed_deadline = dateparser.parse(deadline)
    except (dateparser.ParserError, ValueError, OverflowError) as exc:
        return f"Xatolik: '{deadline}' muddati tushunilmadi: {exc}. Topshiriq yaratilmadi."

    if parsed_deadline is None:
        return f"Xatolik: '{deadline}' muddati tushunilmadi. Topshiriq yaratilmadi."

    with get_session() as session:
        assignee = None
        try:
            assignee_uuid = _parse_uuid(person, "person")
            assignee = session.get(User, assignee_uuid)
        except ValueError:
            assignee = session.exec(select(User).where(User.full_name.ilike(f"%{person}%"))).first()

        if assignee is None:
            assignee = session.exec(select(User).where(User.is_ceo == True)).first()

        if assignee is None:
            return f"Xatolik: '{person}' ismli foydalanuvchi topilmadi. Topshiriq yaratilmadi."

        task = Task(
            description=text,
            deadline=parsed_deadline,
            status="pending",
            assignee_id=assignee.id,
            created_at=datetime.utcnow(),
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        return (
            f"Topshiriq yaratildi. id={task.id}, biriktirildi={assignee.full_name}, "
            f"muddati={task.deadline.isoformat()}, holati={task.status}."
        )


# ---------------------------------------------------------------------------
# 5. summarize_document
# ---------------------------------------------------------------------------

async def summarize_document(file_path_or_bytes: Union[str, bytes]) -> str:
    """
    Parse a document (PDF or text file / bytes) and extract content for summary.
    
    Args:
        file_path_or_bytes: Path to file or raw document bytes.
    """
    extracted_text = _extract_text_from_input(file_path_or_bytes)
    if not extracted_text.strip():
        return "Hujjatdan matn ajratib olinmadi."

    preview = extracted_text[:400] + ("..." if len(extracted_text) > 400 else "")
    return (
        f"Hujjat matni (burchagi):\n{extracted_text[:3000]}\n\n"
        f"(Oldindan ko'rish: {preview})\n\n"
        "Model uchun yo'riqnoma: Yuqoridagi hujjat matnini strictly o'zbek tilida londa xulosa qilib bering."
    )


# ---------------------------------------------------------------------------
# 6. search_company_docs
# ---------------------------------------------------------------------------

async def search_company_docs(query: str, limit: int = 5) -> str:
    """
    Perform semantic RAG vector similarity search over CompanyDocument embeddings.
    
    Args:
        query: Natural language query string.
        limit: Max number of document chunks to retrieve.
    """
    try:
        query_vector = _embed_text(query)
    except Exception as exc:  # noqa: BLE001
        return f"Embedding yaratishda xatolik: '{query}': {exc}"

    with get_session() as session:
        results = session.exec(
            select(
                CompanyDocument,
                CompanyDocument.embedding.cosine_distance(query_vector).label("distance"),
            )
            .order_by(CompanyDocument.embedding.cosine_distance(query_vector))
            .limit(limit)
        ).all()

        if not results:
            return f"'{query}' so'rovi bo'yicha kompaniya hujjatlari topilmadi."

        lines = [f"'{query}' so'rovi bo'yicha topilgan {len(results)} ta hujjat parchalari:", ""]
        for doc, distance in results:
            lines.append(f"• [{doc.id}] {doc.title} (masofa={distance:.4f})\n  {doc.content_chunk[:300]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# OpenAI-style tool function definitions
# ---------------------------------------------------------------------------

AVAILABLE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "daily_brief",
            "description": "Get executive summary of pending tasks, meetings, and recent decisions.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_decision",
            "description": "Log an executive decision with rationale and context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The decision text."},
                    "context": {"type": "string", "description": "Background context for decision."},
                    "logger_id": {"type": "string", "description": "Optional UUID string of logger."},
                },
                "required": ["text", "context"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_decisions",
            "description": "Search logged decisions by keyword before answering past decision queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword to search for."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_task",
            "description": "Create and assign a task to a team member.",
            "parameters": {
                "type": "object",
                "properties": {
                    "person": {"type": "string", "description": "Name or UUID of assignee."},
                    "text": {"type": "string", "description": "Task description."},
                    "deadline": {"type": "string", "description": "Deadline string."},
                },
                "required": ["person", "text", "deadline"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_document",
            "description": "Summarize a company document or uploaded PDF/file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path_or_bytes": {"type": "string", "description": "File path or content string."},
                },
                "required": ["file_path_or_bytes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_company_docs",
            "description": "Perform RAG vector search across company strategy and policy documents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string."},
                    "limit": {"type": "integer", "description": "Max results (default 5).", "default": 5},
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


async def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Dispatch a tool call by name, handling both async coroutines and sync functions."""
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return f"Xatolik: Noma'lum vosita '{name}'."
    try:
        if asyncio.iscoroutinefunction(fn):
            return await fn(**arguments)
        return fn(**arguments)
    except TypeError as exc:
        return f"Xatolik: Vosita noaniq argumentlar oldi '{name}': {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Tool '%s' raised an exception", name)
        return f"Vosita ijrosida xatolik '{name}': {exc}"