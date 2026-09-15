"""
agent/pending_actions.py

Simple in-memory store for pending human-confirmation actions.
"""

from __future__ import annotations

import threading
from typing import Dict, Any, Optional

_store: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def add_pending(pending_id: str, payload: Dict[str, Any]) -> None:
    with _lock:
        _store[pending_id] = payload


def get_pending(pending_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        return _store.get(pending_id)


def pop_pending(pending_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        return _store.pop(pending_id, None)
