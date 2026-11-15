"""Schema migration: an existing database keeps its rows and its field mapping.

The failure this prevents is quiet: `ALTER TABLE ... ADD COLUMN` appends, so any
code that maps rows positionally starts reading the wrong values on a migrated
database and returns a free plan for a paying customer (or the reverse).
"""

from __future__ import annotations

import sqlite3

from src.store import Store


def test_migration_preserves_user_fields(tmp_path):
    path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            plan TEXT NOT NULL DEFAULT 'free',
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            subscription_status TEXT,
            current_period_end TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE annotations (
            id TEXT PRIMARY KEY,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO users (id, email, plan, stripe_customer_id, created_at)"
        " VALUES ('usr_old', 'old@example.org', 'pro', 'cus_123', '2020-01-01T00:00:00')"
    )
    connection.commit()
    connection.close()

    store = Store(path)

    user = store.user("usr_old")
    assert user["email"] == "old@example.org"
    assert user["plan"] == "pro"
    assert user["stripe_customer_id"] == "cus_123"
    assert user["email_verified"] == 0

    verified = store.upsert_user("old@example.org", verified=True)
    assert verified["email_verified"] == 1
    assert verified["plan"] == "pro", "an existing plan must survive a sign-in"
    assert verified["id"] == "usr_old", "sign-in must not mint a second account"


def test_old_annotations_are_orphaned_not_exposed(tmp_path):
    path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE annotations (
            id TEXT PRIMARY KEY,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        INSERT INTO annotations (id, target_type, target_id, payload)
        VALUES ('ann_old', 'answer', 'ans_1',
                '{"id":"ann_old","target_type":"answer","target_id":"ans_1","text":"secret","created_at":"2020-01-01T00:00:00","updated_at":"2020-01-01T00:00:00"}');
        """
    )
    connection.commit()
    connection.close()

    store = Store(path)
    assert store.annotations_for("ans_1", "anon_whatever") == []
