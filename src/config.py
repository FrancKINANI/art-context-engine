"""Runtime configuration.

The MVP runs in two modes and the difference is only where bytes come from:

* ``fixture`` — recorded HTTP responses on disk. Deterministic, offline, used by
  tests and evaluation (§10.1: "re-recording fixtures is a deliberate act").
* ``live`` — the real endpoints.
* ``auto`` — fixtures first, live on miss. Convenient locally; never for the
  evaluation suite, which must stay offline (§10.1) and pins ``fixture``.

Adapters never know which one is active; they take a ``Backend``.

Production is a third axis, not a mode: ``ACE_ENV=production`` turns the checks
below from advice into a startup failure. A misconfigured payment install must
refuse to boot rather than take money with a forgeable session cookie.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "http"
WORKS_DIR = DATA_DIR / "works"
SEED_DIR = DATA_DIR / "seed"
GOLDEN_DIR = DATA_DIR / "golden"

#: The value that must never reach production. Kept as a constant so the check
#: is one comparison, not a regex that drifts.
DEV_SESSION_SECRET = "dev-insecure-secret-change-me"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    env: str = field(default_factory=lambda: os.environ.get("ACE_ENV", "development"))
    mode: str = field(default_factory=lambda: os.environ.get("ACE_HTTP", "auto"))
    user_agent: str = "art-context-engine/0.3 (research prototype; +mailto:julius@example.org)"
    timeout_seconds: float = 20.0
    record_fixtures: bool = field(default_factory=lambda: _env_bool("ACE_RECORD"))
    reader_jurisdiction: str = field(default_factory=lambda: os.environ.get("ACE_JURISDICTION", "US"))
    max_sources_per_adapter: int = 3
    # LLM is optional and never required for the deterministic path (§4.7).
    llm_enabled: bool = field(default_factory=lambda: _env_bool("ACE_LLM"))
    llm_base_url: str = field(default_factory=lambda: os.environ.get("ACE_LLM_BASE_URL", ""))

    # ── deployment ────────────────────────────────────────────────────────
    base_url: str = field(default_factory=lambda: os.environ.get("ACE_BASE_URL", "http://localhost:8000"))
    allowed_hosts: list[str] = field(
        default_factory=lambda: [
            h.strip()
            for h in os.environ.get("ACE_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
            if h.strip()
        ]
    )
    trust_proxy: bool = field(default_factory=lambda: _env_bool("ACE_TRUST_PROXY"))
    require_email_verification: bool = field(
        default_factory=lambda: _env_bool("ACE_REQUIRE_EMAIL_VERIFICATION", True)
    )
    session_secret: str | None = field(default_factory=lambda: os.environ.get("ACE_SESSION_SECRET"))
    max_body_bytes: int = field(default_factory=lambda: int(os.environ.get("ACE_MAX_BODY_BYTES", "262144")))
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.environ.get("ACE_RATE_LIMIT", "20")))
    sign_in_limit_per_minute: int = field(
        default_factory=lambda: int(os.environ.get("ACE_SIGNIN_RATE_LIMIT", "5"))
    )

    # ── mail (magic links) ────────────────────────────────────────────────
    smtp_host: str | None = field(default_factory=lambda: os.environ.get("ACE_SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(os.environ.get("ACE_SMTP_PORT", "587")))
    smtp_user: str | None = field(default_factory=lambda: os.environ.get("ACE_SMTP_USER"))
    smtp_password: str | None = field(default_factory=lambda: os.environ.get("ACE_SMTP_PASSWORD"))
    mail_from: str = field(default_factory=lambda: os.environ.get("ACE_MAIL_FROM", "no-reply@localhost"))

    @property
    def is_production(self) -> bool:
        return self.env.strip().lower() == "production"

    @property
    def use_fixtures(self) -> bool:
        return self.mode in ("fixture", "auto", "record")

    @property
    def allow_live(self) -> bool:
        return self.mode in ("live", "auto", "record")

    @property
    def cookie_secure(self) -> bool:
        return self.base_url.startswith("https")

    @property
    def mail_configured(self) -> bool:
        return bool(self.smtp_host)


settings = Settings()


def production_problems() -> list[str]:
    """Everything that makes this install unfit to take customers' money.

    Returned as a list rather than raised one at a time: a deploy should learn
    all of its problems in one boot, not five restarts.
    """
    problems: list[str] = []
    if not settings.base_url.startswith("https://"):
        problems.append("ACE_BASE_URL must be https:// in production")

    secret = settings.session_secret
    if not secret or secret == DEV_SESSION_SECRET:
        problems.append("ACE_SESSION_SECRET must be set to a random value (>= 32 chars)")
    elif len(secret) < 32:
        problems.append("ACE_SESSION_SECRET is shorter than 32 characters")

    if "*" in settings.allowed_hosts:
        problems.append("ACE_ALLOWED_HOSTS must name the real hosts, not '*'")

    from src.billing import gateway
    from src.billing.plans import paid_plans_configured

    if gateway.enabled():
        if not gateway.webhook_secret():
            problems.append("STRIPE_WEBHOOK_SECRET is required when Stripe is enabled")
        if not paid_plans_configured():
            problems.append("STRIPE_PRICE_PRO is required when Stripe is enabled")
        if not settings.require_email_verification:
            problems.append(
                "ACE_REQUIRE_EMAIL_VERIFICATION=0 lets anyone sign in as a paying "
                "customer by typing their address; it is refused while Stripe is enabled"
            )
        if settings.require_email_verification and not settings.mail_configured:
            problems.append(
                "ACE_SMTP_HOST is required: without email verification any visitor "
                "could sign in as a paying customer by typing their address"
            )
    return problems
