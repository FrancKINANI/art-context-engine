"""API dependencies: the store, and the retrieval backend the app was built with."""

from __future__ import annotations

from src.config import settings
from src.retrieval.http import get_backend, reset_backend
from src.store import Store, get_store


def store() -> Store:
    return get_store()


def set_mode(mode: str) -> None:
    """Tests switch to fixtures; the running app uses what config says."""
    settings.mode = mode
    reset_backend()


def backend():
    return get_backend()
