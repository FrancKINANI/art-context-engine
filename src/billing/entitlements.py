"""Accounts, quotas and entitlements.

An account is an email and a signed cookie — no passwords, no OAuth, no social
login. The MVP's social layer is deferred (§3.8) and so is its auth story; what
the product needs now is only "is this person on a paid plan, and how many
answers have they had this month".

**The email must be proven before a session is issued.** The first version
attached a session to whatever address was typed, so anyone who knew a paying
customer's email could sign in as them and inherit their plan. Sign-in is now a
one-time link: the token is random, stored only as a hash, valid for fifteen
minutes and burned on first use. ``ACE_REQUIRE_EMAIL_VERIFICATION=0`` exists for
local development only, and production refuses to boot with it on while Stripe
is enabled (``config.production_problems``).

Order of checks matters:

1. **billing disabled** (no Stripe key) → everything is free, no quota. A
   self-hosted install must be fully usable without a merchant account.
2. **paid plan** → unlimited.
3. **anonymous** → allowed a small taste (the free quota), then asked to sign up.
   Each browser has its own id, so one visitor cannot spend another's taste.
4. **free account** → quota per calendar month, reset at the month boundary.

Quota is checked before the pipeline runs and consumed after it succeeds — a
crash mid-assembly must not cost the reader an answer.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response

from src.api.security import ANON_COOKIE
from src.billing import gateway
from src.billing.plans import FREE, Plan, get_plan
from src.config import DEV_SESSION_SECRET, settings
from src.store import Store

ANON_QUOTA = int(os.environ.get("ACE_ANON_QUOTA", "2"))
COOKIE_NAME = "ace_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
LOGIN_TOKEN_TTL_SECONDS = 60 * 15


def _secret() -> bytes:
    return (settings.session_secret or DEV_SESSION_SECRET).encode()


# ═══════════════════════════════════════════════════════════════════════════
# SESSIONS
# ═══════════════════════════════════════════════════════════════════════════

def sign_session(user_id: str) -> str:
    payload = f"{user_id}.{int(time.time())}"
    mac = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    raw = f"{payload}.{mac}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def read_session(token: str | None) -> str | None:
    """Return the user id from a signed cookie, or None if it is invalid."""
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        user_id, issued, mac = raw.split(".")
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        return None
    expected = hmac.new(_secret(), f"{user_id}.{issued}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        issued_at = int(issued)
    except ValueError:
        return None
    if time.time() - issued_at > SESSION_TTL_SECONDS:
        return None
    return user_id


def set_session_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        sign_session(user_id),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def identity(request: Request, store: Store) -> str | None:
    """Who is asking: a signed-in user, or a browser-scoped anonymous id.

    Only a *signed* cookie can name a user, and only an ``anon_``-prefixed value
    can name a visitor. Without that rule, setting ``ace_anon=usr_...`` by hand
    would let a stranger read someone else's private annotations.
    """
    user_id = read_session(request.cookies.get(COOKIE_NAME))
    if user_id and store.user(user_id) is not None:
        return user_id
    anon = request.cookies.get(ANON_COOKIE)
    if anon and anon.startswith("anon_") and len(anon) <= 64:
        return anon
    # A brand-new browser: the middleware minted an id for this request so the
    # very first answer or annotation is charged to the right visitor.
    pending = getattr(request.state, "anon_id", None)
    if pending:
        return pending
    return None


# ═══════════════════════════════════════════════════════════════════════════
# MAGIC LINKS
# ═══════════════════════════════════════════════════════════════════════════

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_login_token(store: Store, email: str) -> str:
    """Store the hash and return the raw token for the link."""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(seconds=LOGIN_TOKEN_TTL_SECONDS)).isoformat()
    store.create_login_token(_token_hash(token), email, expires)
    return token


def consume_login_token(store: Store, token: str) -> str | None:
    if not token or len(token) > 128:
        return None
    return store.consume_login_token(_token_hash(token))


def login_link(token: str) -> str:
    return f"{settings.base_url.rstrip('/')}/auth/verify?token={token}"


# ═══════════════════════════════════════════════════════════════════════════
# ENTITLEMENT RESULT
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Entitlement:
    allowed: bool
    reason: str
    plan: Plan
    used: int = 0
    limit: int | None = None
    user_id: str | None = None

    @property
    def remaining(self) -> int | None:
        if self.limit is None:
            return None
        return max(0, self.limit - self.used)

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "plan": self.plan.id,
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
        }


def _period(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def check(user_id: str | None, store: Store) -> Entitlement:
    if not gateway.enabled():
        return Entitlement(True, "billing disabled: self-hosted install", FREE)

    if user_id and user_id.startswith("anon_"):
        used = store.usage(user_id, _period())
        return Entitlement(
            used < ANON_QUOTA, "" if used < ANON_QUOTA else "anonymous_trial_exhausted",
            FREE, used, ANON_QUOTA, user_id,
        )

    if user_id:
        user = store.user(user_id)
        if user is not None:
            plan = get_plan(user.get("plan", "free"))
            if plan.is_unlimited:
                return Entitlement(True, "paid plan", plan, 0, None, user_id)
            used = store.usage(user_id, _period())
            limit = plan.answers_per_month
            return Entitlement(
                used < limit, "" if used < limit else "quota_exhausted",
                plan, used, limit, user_id,
            )

    # No identity at all (cookies disabled, or a client calling the API
    # directly): fall back to a single shared bucket. It is shared on purpose —
    # there is nothing else to meter against — and rate limiting bounds it.
    used = store.usage(None, _period())
    return Entitlement(
        used < ANON_QUOTA, "" if used < ANON_QUOTA else "anonymous_trial_exhausted",
        FREE, used, ANON_QUOTA, None,
    )


def consume(user_id: str | None, store: Store) -> None:
    """Record one answer against the month. Called only after assembly."""
    if not gateway.enabled():
        return
    store.record_usage(user_id or "anon", _period())


def account(store: Store, user_id: str) -> dict | None:
    user = store.user(user_id)
    if user is None:
        return None
    plan = get_plan(user.get("plan", "free"))
    used = store.usage(user_id, _period()) if not plan.is_unlimited else 0
    return {
        "id": user["id"],
        "email": user["email"],
        "email_verified": bool(user.get("email_verified")),
        "plan": plan.id,
        "plan_name": plan.name,
        "used_this_month": used,
        "limit": plan.answers_per_month,
        "subscription_status": user.get("subscription_status"),
        "current_period_end": user.get("current_period_end"),
        "has_customer": bool(user.get("stripe_customer_id")),
    }
