"""Persistence: answers are permalinks, so they have to outlive the request.

SQLite because the MVP needs durable documents and nothing else: no migrations
framework, no connection pool, no ORM. Every payload is the model's JSON, so the
store cannot drift from the schema — and when a model changes, old rows fail
loudly on read instead of silently returning half an object.

Pack notes:
* `sources` are stored per answer, because the rights determination at the time
  of publication is part of the record. Re-resolving a licence years later must
  not rewrite what a published document was allowed to show.
* Annotations are private (§3.8), which is enforced here: every row carries an
  owner and every read filters on it. "Private" that the API does not check is a
  promise, not a property.
* Accounts, one-time sign-in tokens and processed Stripe event ids live here too.
  WAL mode + a busy timeout keep concurrent reads from failing under uvicorn's
  thread pool; writes are serialized by the database, which is honest for the
  MVP's volume.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from src.config import DATA_DIR
from src.models import (
    Annotation,
    Answer,
    ContextPack,
    Feedback,
    Source,
    Work,
)

DB_PATH = DATA_DIR / "runtime" / "ace.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    parent_id TEXT,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS packs (
    answer_id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    answer_id TEXT NOT NULL,
    id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (answer_id, id)
);
CREATE TABLE IF NOT EXISTS works (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    answer_id TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS endorsements (
    answer_id TEXT NOT NULL,
    visitor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (answer_id, visitor)
);
CREATE TABLE IF NOT EXISTS annotations (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_answers_work ON answers(work_id);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free',
    email_verified INTEGER NOT NULL DEFAULT 0,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    subscription_status TEXT,
    current_period_end TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_tokens (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS stripe_events (
    id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    user_id TEXT NOT NULL,
    period TEXT NOT NULL,
    answers INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, period)
);
"""

#: Columns added after the first deploy. `_migrate` applies the ones a given
#: database is missing, so an existing `ace.sqlite3` keeps its rows.
_ADDED_COLUMNS = {
    "users": {"email_verified": "INTEGER NOT NULL DEFAULT 0"},
    "annotations": {"owner": "TEXT NOT NULL DEFAULT ''"},
}


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        for table, columns in _ADDED_COLUMNS.items():
            present = {
                row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            for name, ddl in columns.items():
                if name not in present:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        # Old annotations had no owner; they were local-only and cannot be
        # attributed now. Leaving them owned by '' keeps them unreachable rather
        # than handing them to the next visitor.
        self._conn.execute("UPDATE annotations SET owner='' WHERE owner IS NULL")
        # Indexed after the migration: on an existing database the `owner`
        # column only exists once the ALTER above has run.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_annotations_target ON annotations(target_id, owner)"
        )

    # ── answers ───────────────────────────────────────────────────────────

    def save(self, answer: Answer, pack: ContextPack, sources: dict[str, Source]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO answers (id, work_id, parent_id, created_at, payload)"
                " VALUES (?,?,?,?,?)",
                (answer.id, answer.work_id, answer.parent_id,
                 answer.generated_at.isoformat(), answer.model_dump_json()),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO packs (answer_id, work_id, payload) VALUES (?,?,?)",
                (answer.id, pack.work_id, pack.model_dump_json()),
            )
            for source in sources.values():
                self._conn.execute(
                    "INSERT OR REPLACE INTO sources (answer_id, id, payload) VALUES (?,?,?)",
                    (answer.id, source.id, source.model_dump_json()),
                )
            self._conn.commit()

    def answer(self, answer_id: str) -> Answer | None:
        row = self._conn.execute(
            "SELECT payload FROM answers WHERE id=?", (answer_id,)
        ).fetchone()
        return Answer.model_validate_json(row[0]) if row else None

    def pack(self, answer_id: str) -> ContextPack | None:
        row = self._conn.execute(
            "SELECT payload FROM packs WHERE answer_id=?", (answer_id,)
        ).fetchone()
        return ContextPack.model_validate_json(row[0]) if row else None

    def sources(self, answer_id: str) -> dict[str, Source]:
        rows = self._conn.execute(
            "SELECT id, payload FROM sources WHERE answer_id=?", (answer_id,)
        ).fetchall()
        return {sid: Source.model_validate_json(payload) for sid, payload in rows}

    def children(self, answer_id: str) -> list[Answer]:
        rows = self._conn.execute(
            "SELECT payload FROM answers WHERE parent_id=? ORDER BY created_at", (answer_id,)
        ).fetchall()
        return [Answer.model_validate_json(r[0]) for r in rows]

    def save_work(self, work: Work) -> Work:
        """A work resolved online, kept so its documents still render later.

        Without this, an answer would outlive the only copy of the work it is
        about, and the page would fall back to "Document" for ever.
        """
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO works (id, created_at, payload) VALUES (?,?,?)",
                (work.id, datetime.now().isoformat(timespec="seconds"),
                 work.model_dump_json()),
            )
            self._conn.commit()
        return work

    def work(self, work_id: str) -> Work | None:
        row = self._conn.execute(
            "SELECT payload FROM works WHERE id=?", (work_id,)
        ).fetchone()
        return Work.model_validate_json(row[0]) if row else None

    def works(self, limit: int = 500) -> list[Work]:
        rows = self._conn.execute(
            "SELECT payload FROM works ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [Work.model_validate_json(r[0]) for r in rows]

    def recent(self, limit: int = 12) -> list[Answer]:
        """Newest documents across every work.

        The index used to ask each registry work in turn; that stops working the
        moment a work is resolved online, and it is the wrong query anyway.
        """
        rows = self._conn.execute(
            "SELECT payload FROM answers ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [Answer.model_validate_json(r[0]) for r in rows]

    def feedback_count(self, answer_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE answer_id=?", (answer_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def by_work(self, work_id: str, limit: int = 20) -> list[Answer]:
        rows = self._conn.execute(
            "SELECT payload FROM answers WHERE work_id=? ORDER BY created_at DESC LIMIT ?",
            (work_id, limit),
        ).fetchall()
        return [Answer.model_validate_json(r[0]) for r in rows]

    # ── feedback / annotations ────────────────────────────────────────────

    def add_feedback(self, feedback: Feedback) -> Feedback:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO feedback (id, answer_id, payload) VALUES (?,?,?)",
                (feedback.id, feedback.answer_id, feedback.model_dump_json()),
            )
            self._conn.commit()
        return feedback

    # ── endorsements ──────────────────────────────────────────────────────
    #
    # A separate table from feedback on purpose. Feedback is a verdict on the
    # answer ("it missed"); an endorsement is a person putting their name to it
    # ("this is right, and I will say so"). They drive different decisions, and
    # the endorsement is the one that can become a reputation later.

    def endorse(self, answer_id: str, visitor: str) -> int:
        """One person, one endorsement. Repeating it is not twice the support."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO endorsements (answer_id, visitor, created_at) "
                "VALUES (?,?,?)",
                (answer_id, visitor, datetime.now().isoformat(timespec="seconds")),
            )
            self._conn.commit()
        return self.endorsement_count(answer_id)

    def endorsement_count(self, answer_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM endorsements WHERE answer_id=?", (answer_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def has_endorsed(self, answer_id: str, visitor: str | None) -> bool:
        if not visitor:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM endorsements WHERE answer_id=? AND visitor=?",
            (answer_id, visitor),
        ).fetchone()
        return row is not None

    def feedback_for(self, answer_id: str) -> list[Feedback]:
        rows = self._conn.execute(
            "SELECT payload FROM feedback WHERE answer_id=?", (answer_id,)
        ).fetchall()
        return [Feedback.model_validate_json(r[0]) for r in rows]

    # ── accounts / billing ───────────────────────────────────────────────

    def upsert_user(self, email: str, *, verified: bool = False) -> dict:
        email = email.strip().lower()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE email=?", (email,)
            ).fetchone()
            if row is None:
                # Opaque id: deriving it from the address let anyone predict
                # another customer's identifier.
                user_id = f"usr_{secrets.token_hex(12)}"
                self._conn.execute(
                    "INSERT INTO users (id, email, plan, email_verified, created_at)"
                    " VALUES (?,?,?,?,?)",
                    (user_id, email, "free", int(verified),
                     datetime.now(timezone.utc).isoformat()),
                )
            elif verified:
                self._conn.execute(
                    "UPDATE users SET email_verified=1 WHERE email=?", (email,)
                )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM users WHERE email=?", (email,)
            ).fetchone()
        return self._row_to_user(row)

    def user(self, user_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def user_by_email(self, email: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM users WHERE email=?", (email.strip().lower(),)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def user_by_customer(self, customer_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM users WHERE stripe_customer_id=?", (customer_id,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def update_subscription(
        self,
        user_id: str,
        *,
        plan: str | None = None,
        customer_id: str | None = None,
        subscription_id: str | None = None,
        status: str | None = None,
        period_end: str | None = None,
    ) -> None:
        updates = {
            "plan": plan,
            "stripe_customer_id": customer_id,
            "stripe_subscription_id": subscription_id,
            "subscription_status": status,
            "current_period_end": period_end,
        }
        fields = {k: v for k, v in updates.items() if v is not None}
        if not fields:
            return
        assignments = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE users SET {assignments} WHERE id=?",
                (*fields.values(), user_id),
            )
            self._conn.commit()

    # ── one-time sign-in tokens ───────────────────────────────────────────

    def create_login_token(self, token_hash: str, email: str, expires_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO login_tokens (token_hash, email, expires_at, used)"
                " VALUES (?,?,?,0)",
                (token_hash, email.strip().lower(), expires_at),
            )
            # Expired rows are worthless; pruning here keeps the table bounded
            # without a scheduler.
            self._conn.execute(
                "DELETE FROM login_tokens WHERE expires_at < ?",
                (datetime.now(timezone.utc).isoformat(),),
            )
            self._conn.commit()

    def consume_login_token(self, token_hash: str) -> str | None:
        """Atomically burn a token and return its email, or None."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT email FROM login_tokens"
                " WHERE token_hash=? AND used=0 AND expires_at >= ?",
                (token_hash, now),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE login_tokens SET used=1 WHERE token_hash=?", (token_hash,)
            )
            self._conn.commit()
        return row[0]

    # ── Stripe event idempotency ──────────────────────────────────────────

    def mark_event(self, event_id: str) -> bool:
        """True the first time an event id is seen, False on replay."""
        if not event_id:
            return True
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO stripe_events (id, received_at) VALUES (?,?)",
                    (event_id, datetime.now(timezone.utc).isoformat()),
                )
            except sqlite3.IntegrityError:
                return False
            self._conn.commit()
        return True

    def event_seen(self, event_id: str) -> bool:
        if not event_id:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM stripe_events WHERE id=?", (event_id,)
        ).fetchone()
        return row is not None

    def usage(self, user_id: str | None, period: str) -> int:
        row = self._conn.execute(
            "SELECT answers FROM usage WHERE user_id=? AND period=?",
            (user_id or "anon", period),
        ).fetchone()
        return int(row[0]) if row else 0

    def record_usage(self, user_id: str, period: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage (user_id, period, answers) VALUES (?,?,1)"
                " ON CONFLICT(user_id, period) DO UPDATE SET answers = answers + 1",
                (user_id, period),
            )
            self._conn.commit()

    @staticmethod
    def _row_to_user(row) -> dict:
        # By name, not position: a column added by `_migrate` is appended to the
        # table, so positional mapping silently shifts every field after it.
        return {
            key: row[key]
            for key in (
                "id", "email", "plan", "email_verified", "stripe_customer_id",
                "stripe_subscription_id", "subscription_status",
                "current_period_end", "created_at",
            )
        }

    def add_annotation(self, annotation: Annotation, owner: str) -> Annotation:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO annotations (id, target_type, target_id, owner, payload)"
                " VALUES (?,?,?,?,?)",
                (annotation.id, annotation.target_type, annotation.target_id, owner,
                 annotation.model_dump_json()),
            )
            self._conn.commit()
        return annotation

    def annotations_for(self, target_id: str, owner: str) -> list[Annotation]:
        if not owner:
            return []
        rows = self._conn.execute(
            "SELECT payload FROM annotations WHERE target_id=? AND owner=?",
            (target_id, owner),
        ).fetchall()
        return [Annotation.model_validate_json(r[0]) for r in rows]


_STORE: Store | None = None


def get_store(path: Path | None = None) -> Store:
    global _STORE
    if _STORE is None or path is not None:
        _STORE = Store(path)
    return _STORE
