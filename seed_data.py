# seed_data.py
import uuid
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from sqlmodel import create_engine, Session, SQLModel
from models import User, Task, Decision, Meeting

# Import your models so SQLModel knows about all database tables
from models import User, CompanyDocument  # Adjust import path if needed

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://ceo_admin:securepassword123@localhost:5433/ceo_agent_db"
)

engine = create_engine(DATABASE_URL)

# Drop existing tables to clear out old schema definitions
SQLModel.metadata.drop_all(engine)

# Create all tables fresh with the correct column types (including BIGINT)
SQLModel.metadata.create_all(engine)


def seed_db():
    with Session(engine) as session:
        # 1. Create Users (1 CEO, 4 Department Heads)
        ceo = User(full_name="Bobur Ergashboyev", role="CEO", is_ceo=True,
                   telegram_id=os.getenv("TELEGRAM_CE0_ID"))
        cto = User(full_name="Sherzod", role="CTO")
        cmo = User(full_name="Oybek", role="CMO")
        cfo = User(full_name="Orifjon", role="CFO")
        cso = User(full_name="Asliddin Sultonov", role="CSO")

        session.add_all([ceo, cto, cmo, cfo, cso])
        session.commit()

        # 2. Create 15 Past Decisions
        decisions = [
                        Decision(text="Moved all infrastructure to AWS.",
                                 context="To improve scalability ahead of Q4 rush.", logger_id=ceo.id,
                                 logged_at=datetime.utcnow() - timedelta(days=30)),
                        Decision(text="Approved hiring 3 new AI engineers.",
                                 context="Need to accelerate the LangGraph multi-agent rollout.", logger_id=ceo.id,
                                 logged_at=datetime.utcnow() - timedelta(days=15)),
                        Decision(text="Switched default LLM from OpenAI to Groq.",
                                 context="Cost reduction and faster inference for the Telegram bots.", logger_id=ceo.id,
                                 logged_at=datetime.utcnow() - timedelta(days=5)),
                        # ... (Loop or multiply these to reach 15)
                    ] * 5
        session.add_all(decisions)

        # 3. Create 20 Tasks (Some overdue, some pending)
        tasks = [
                    Task(description="Finalize server budget for Q1 2027.",
                         deadline=datetime.utcnow() + timedelta(days=2), assignee_id=cfo.id),
                    Task(description="Review candidate test tasks for the AI Dev role.",
                         deadline=datetime.utcnow() - timedelta(days=1), status="pending", assignee_id=cto.id),
                    Task(description="Draft the new Marketing brand voice document.",
                         deadline=datetime.utcnow() + timedelta(days=4), assignee_id=cmo.id),
                    Task(description="Audit the latest n8n webhook security logs.",
                         deadline=datetime.utcnow() + timedelta(days=1), assignee_id=cso.id),
                ] * 5
        session.add_all(tasks)

        # 4. Create 15 Meetings (Past and Future)
        meetings = [
                       Meeting(title="Weekly Executive Sync", scheduled_for=datetime.utcnow() + timedelta(hours=2),
                               summary="Reviewing the AI Agent deployment statuses."),
                       Meeting(title="1-on-1 with Security Head",
                               scheduled_for=datetime.utcnow() + timedelta(days=1, hours=4),
                               summary="Discussing incident response protocols."),
                       Meeting(title="Investor Update Call", scheduled_for=datetime.utcnow() - timedelta(days=2),
                               summary="Presented Q3 metrics; 15% growth MoM."),
                   ] * 5
        session.add_all(meetings)

        session.commit()
        print("✅ Database successfully seeded with 55 rows of realistic synthetic data!")


if __name__ == "__main__":
    seed_db()