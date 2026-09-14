# agent/tools/registry.py
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlmodel import Session, select
from models import User, Task, Decision, Meeting, CompanyDocument
from agent.llm import completion_with_backoff


def daily_brief(session: Session) -> Dict[str, Any]:
    """Generates executive summary of pending tasks, meetings, and recent decisions."""
    tasks = session.exec(select(Task).where(Task.status == "pending")).all()

    now = datetime.now(timezone.utc)
    meetings = session.exec(select(Meeting).where(Meeting.scheduled_for >= now)).all()

    decisions = session.exec(select(Decision).order_by(Decision.logged_at.desc()).limit(5)).all()

    return {
        "pending_tasks_count": len(tasks),
        "tasks": [{"id": str(t.id), "description": t.description, "deadline": str(t.deadline)} for t in tasks],
        "upcoming_meetings": [{"title": m.title, "time": str(m.scheduled_for)} for m in meetings],
        "recent_decisions": [{"text": d.text, "logged_at": str(d.logged_at)} for d in decisions]
    }


def log_decision(session: Session, text: str, context: str, logger_id: str) -> Dict[str, Any]:
    """Logs a new strategic decision into the database."""
    decision = Decision(
        text=text,
        context=context,
        logger_id=uuid.UUID(logger_id),
        logged_at=datetime.now(timezone.utc)
    )
    session.add(decision)
    session.commit()
    session.refresh(decision)
    return {"status": "success", "decision_id": str(decision.id), "logged_at": str(decision.logged_at)}


def search_decisions(session: Session, query: str) -> List[Dict[str, Any]]:
    """Searches decisions containing the given query string."""
    decisions = session.exec(
        select(Decision).where(Decision.text.contains(query) | Decision.context.contains(query))).all()
    return [{"id": str(d.id), "text": d.text, "context": d.context, "logged_at": str(d.logged_at)} for d in decisions]


def assign_task(session: Session, description: str, deadline_str: str, assignee_id: str) -> Dict[str, Any]:
    """Creates and assigns a task to a user."""
    deadline = datetime.fromisoformat(deadline_str)
    task = Task(
        description=description,
        deadline=deadline,
        assignee_id=uuid.UUID(assignee_id),
        status="pending"
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return {"status": "created", "task_id": str(task.id), "assignee_id": str(task.assignee_id)}


def summarize_document(session: Session, doc_id: str) -> str:
    """Retrieves a document by ID and generates a summary via LLM call."""
    doc = session.get(CompanyDocument, uuid.UUID(doc_id))
    if not doc:
        return f"Document with ID {doc_id} not found."

    messages = [
        {"role": "system", "content": "Summarize the following document concisely for the CEO."},
        {"role": "user", "content": f"Title: {doc.title}\nContent: {doc.content_chunk}"}
    ]
    response = completion_with_backoff(messages)
    return response["message"].content if hasattr(response["message"], "content") else response["message"]["content"]


def search_company_docs(session: Session, query_vector: List[float], limit: int = 3) -> List[Dict[str, Any]]:
    """Performs cosine distance vector search over stored document chunks."""
    statement = select(CompanyDocument).order_by(CompanyDocument.embedding.l2_distance(query_vector)).limit(limit)
    results = session.exec(statement).all()
    return [{"id": str(d.id), "title": d.title, "content_chunk": d.content_chunk} for d in results]


# OpenAI-compatible function tool definitions for Groq API
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "daily_brief",
            "description": "Fetch upcoming meetings, pending tasks, and recent decisions for executive briefing."
        }
    },
    {
        "type": "function",
        "function": {
            "name": "log_decision",
            "description": "Log an executive decision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The decision statement."},
                    "context": {"type": "string", "description": "Background context for the decision."},
                    "logger_id": {"type": "string", "description": "UUID of the user logging this."}
                },
                "required": ["text", "context", "logger_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_decisions",
            "description": "Search past decisions by keyword.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "assign_task",
            "description": "Assign a task to a team member.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "deadline_str": {"type": "string", "description": "ISO formatted date string"},
                    "assignee_id": {"type": "string", "description": "UUID of assignee"}
                },
                "required": ["description", "deadline_str", "assignee_id"]
            }
        }
    }
]


class ChiefOfStaffAgent:
    pass