import uuid
from sqlmodel import SQLModel, Session, select
from agent.db import get_session, engine
from agent.loop import run_agent_turn
from models import User


def setup_test_user():
    """Ensure database tables exist and create a test user if needed."""
    SQLModel.metadata.create_all(engine)
    with get_session() as session:
        user = session.exec(select(User).limit(1)).first()
        if not user:
            user = User(
                email="hnot779@gmail.com",
                full_name="CEO",
                role="ceo",
                hashed_password="fake"
            )
            session.add(user)
            session.commit()
            session.refresh(user)
        return user.id


if __name__ == "__main__":
    print("Setting up test database...")
    user_id = setup_test_user()

    print(f"\n--- Testing Agent Loop for User {user_id} ---\n")

    with Session(engine) as session:
        prompt = "What is on my plate today? Give me a daily brief."
        print(f"User: {prompt}")

        response = run_agent_turn(str(user_id), prompt, session)

        print(f"\nChief of Staff:\n{response}")