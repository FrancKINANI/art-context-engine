"""Shared test configuration.

Three things are pinned for every test:

* **fixture mode** — a test that reaches the internet is not a test.
* **a throwaway store** — tests must not write into `data/runtime/ace.sqlite3`,
  which is the local dev database, and must not depend on each other's rows.
* **no ambient billing secrets** — a Stripe key in the developer's environment
  must not change what a test measures.
"""

from __future__ import annotations

import pytest

from src.config import settings
from src.retrieval.http import reset_backend
import src.store as store_module


@pytest.fixture(autouse=True)
def fixture_mode(monkeypatch):
    monkeypatch.setattr(settings, "mode", "fixture")
    monkeypatch.setattr(settings, "require_email_verification", True)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_PRO", raising=False)
    reset_backend()
    yield
    reset_backend()


@pytest.fixture
def test_store(tmp_path, monkeypatch):
    """A Store with its own sqlite file, wired into every module that imported it."""
    test_store = store_module.Store(tmp_path / "test.sqlite3")
    monkeypatch.setattr(store_module, "_STORE", test_store)
    from src.api import deps

    monkeypatch.setattr(deps, "store", lambda: test_store)

    from src.api.routes import annotations, answers, billing, export, pages, works

    for module in (answers, works, export, annotations, pages, billing):
        if hasattr(module, "store"):
            monkeypatch.setattr(module, "store", lambda: test_store)
    return test_store


@pytest.fixture
def client_factory(test_store):
    """Clients that share one store but not one cookie jar (two visitors)."""
    from fastapi.testclient import TestClient

    from src.api.app import app

    clients: list[TestClient] = []

    def make() -> TestClient:
        instance = TestClient(app)
        clients.append(instance)
        return instance

    yield make

    for instance in clients:
        instance.close()


@pytest.fixture
def client(client_factory):
    return client_factory()
