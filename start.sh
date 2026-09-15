#!/bin/bash
set -e

# Run database migrations
alembic upgrade head

# Start Uvicorn app
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}