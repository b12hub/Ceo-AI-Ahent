import os
from pathlib import Path
from dotenv import load_dotenv

# Explicitly target .env file at project root
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Fallback to check both variable spellings (0 vs O)
raw_id = os.getenv("TELEGRAM_CE0_ID") or os.getenv("TELEGRAM_CEO_ID") or ""
TELEGRAM_CE0_ID: str = str(raw_id).strip().strip('"').strip("'")

# Parse ALLOWED_USERS list of integer Telegram IDs
raw_allowed = os.getenv("ALLOWED_USERS", "").strip()
allowed_ids = [int(i.strip()) for i in raw_allowed.split(",") if i.strip().isdigit()]
if not allowed_ids and TELEGRAM_CE0_ID.isdigit():
    allowed_ids = [int(TELEGRAM_CE0_ID)]

ALLOWED_USERS: list[int] = allowed_ids

INTERNAL_API_KEY: str = os.getenv("INTERNAL_API_KEY", "").strip()
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "").strip()
WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "").strip()
DASHBOARD_BASE_URL: str = os.getenv("DASHBOARD_BASE_URL", "http://localhost:8000").strip().rstrip('/')