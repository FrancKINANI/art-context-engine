"""Security and production-hardening tests.

Each test names the failure it prevents. If one of these goes red, the install is
not safe to expose to paying customers.
"""

from __future__ import annotations

import base64
import json

import pytest

from src.api import reader, security
from src.api.routes import billing
from src.billing import entitlements, gateway, mailer
from src.config import DEV_SESSION_SECRET, settings


QUESTION = {
    "question": "Why does the second movement sound so plain?",
    "work_id": "beethoven-op111",
}


@pytest.fixture
def mailbox(monkeypatch):
    """Capture outgoing sign-in links instead of talking to an SMTP server."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(mailer, "send_sign_in_link", lambda email, link: sent.append((email, link)))
    return sent


def _token_from(link: str) -> str:
    return link.split("token=", 1)[1]


# ═══════════════════════════════════════════════════════════════════════════
# HEADERS AND CONTENT
# ═══════════════════════════════════════════════════════════════════════════

def test_security_headers_on_every_page(client):
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp, "inline code must not be allowed by the CSP"


def test_pages_have_no_inline_style_or_script(client):
    """The CSP forbids inline code; emitted HTML must therefore not rely on it."""
    for path in ("/", "/pricing", "/account"):
        html = client.get(path).text
        assert "<style" not in html, path
        assert "<script>" not in html, path
        assert "/static/app.css" in html
        assert "/static/app.js" in html


def test_static_assets_are_allowlisted(client):
    assert client.get("/static/app.css").status_code == 200
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "deepen" in js.text
    assert client.get("/static/../src/config.py").status_code == 404
    assert client.get("/static/secret.txt").status_code == 404


def test_only_http_urls_become_links():
    assert reader.safe_url("https://example.org/a") == "https://example.org/a"
    assert reader.safe_url("javascript:alert(1)") == ""
    assert reader.safe_url("data:text/html,<script>") == ""
    assert reader.safe_url(None) == ""


def test_oversized_body_is_rejected_before_the_pipeline(client):
    body = json.dumps({"question": "x" * (settings.max_body_bytes + 10), "work_id": "beethoven-op111"})
    response = client.post("/api/v1/answers", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 413


# ═══════════════════════════════════════════════════════════════════════════
# SESSIONS AND ACCOUNTS
# ═══════════════════════════════════════════════════════════════════════════

def test_forged_session_cookie_is_rejected(client):
    forged = base64.urlsafe_b64encode(b"usr_victim.1700000000.deadbeefdeadbeef").decode()
    client.cookies.set(entitlements.COOKIE_NAME, forged)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_session_signed_with_another_secret_is_rejected(client, monkeypatch):
    token = entitlements.sign_session("usr_someone")
    monkeypatch.setattr(settings, "session_secret", "a-completely-different-secret-value!!")
    client.cookies.set(entitlements.COOKIE_NAME, token)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_sign_in_does_not_issue_a_session_until_the_link_is_used(client, mailbox):
    response = client.post("/api/v1/auth/sign-in", json={"email": "reader@example.org"})
    assert response.status_code == 202
    assert response.json()["status"] == "link_sent"
    assert entitlements.COOKIE_NAME not in response.cookies
    assert client.get("/api/v1/auth/me").status_code == 401
    assert mailbox, "the link must actually be mailed"

    token = _token_from(mailbox[0][1])
    signed_in = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert signed_in.status_code == 303
    assert client.get("/api/v1/auth/me").json()["email"] == "reader@example.org"


def test_sign_in_fails_closed_without_a_mailer(client):
    """A paid install with no SMTP cannot verify an address, so it must not sign in."""
    response = client.post("/api/v1/auth/sign-in", json={"email": "reader@example.org"})
    assert response.status_code == 503


def test_sign_in_is_uniform_for_unknown_addresses(client, mailbox):
    """No account enumeration: the work email and a stranger's look the same."""
    first = client.post("/api/v1/auth/sign-in", json={"email": "paying@example.org"})
    second = client.post("/api/v1/auth/sign-in", json={"email": "nobody@example.org"})
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()


def test_magic_link_signs_in_and_burns_once(client, test_store):
    token = entitlements.create_login_token(test_store, "reader@example.org")

    signed_in = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert signed_in.status_code == 303
    assert entitlements.COOKIE_NAME in signed_in.cookies

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "reader@example.org"
    assert me.json()["email_verified"] is True

    replay = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert replay.status_code == 400


def test_expired_token_is_refused(client, test_store):
    test_store.create_login_token("deadbeef", "reader@example.org", "2000-01-01T00:00:00+00:00")
    assert test_store.consume_login_token("deadbeef") is None


def test_user_ids_are_opaque(test_store):
    user = test_store.upsert_user("someone@example.org")
    assert user["id"].startswith("usr_")
    assert "someone" not in user["id"], "an id derived from the address is predictable"


def test_sign_out_clears_the_session(client, test_store):
    token = entitlements.create_login_token(test_store, "reader@example.org")
    client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.post("/api/v1/auth/sign-out").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_html_sign_in_sets_the_cookie_in_development(client, monkeypatch):
    """The form path must issue a usable session, not a cookie on a dropped response."""
    monkeypatch.setattr(settings, "require_email_verification", False)
    response = client.post(
        "/account/sign-in", data={"email": "reader@example.org"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert entitlements.COOKIE_NAME in response.cookies
    assert client.get("/api/v1/auth/me").json()["email"] == "reader@example.org"


# ═══════════════════════════════════════════════════════════════════════════
# PRIVACY
# ═══════════════════════════════════════════════════════════════════════════

def test_annotations_are_private_to_their_owner(client_factory):
    alice, bob = client_factory(), client_factory()
    created = alice.post(
        "/api/v1/annotations",
        json={"target_type": "answer", "target_id": "ans_1", "text": "private note"},
    )
    assert created.status_code == 200

    assert bob.get("/api/v1/annotations", params={"target_id": "ans_1"}).json() == []
    mine = alice.get("/api/v1/annotations", params={"target_id": "ans_1"}).json()
    assert [a["text"] for a in mine] == ["private note"]


def test_anonymous_cannot_impersonate_a_user_for_annotations(client, test_store):
    from src.models import Annotation

    user = test_store.upsert_user("victim@example.org", verified=True)
    test_store.add_annotation(
        Annotation(target_type="answer", target_id="ans_secret", text="victim note"),
        owner=user["id"],
    )
    client.cookies.set(security.ANON_COOKIE, user["id"])  # not an anon_ id
    response = client.get("/api/v1/annotations", params={"target_id": "ans_secret"})
    assert response.status_code == 401
    assert "victim note" not in response.text


def test_each_visitor_gets_their_own_anonymous_identity(client_factory):
    one, two = client_factory(), client_factory()
    one.get("/")
    two.get("/")
    first = one.cookies.get(security.ANON_COOKIE)
    second = two.cookies.get(security.ANON_COOKIE)
    assert first and second and first != second


def test_one_visitor_cannot_spend_anothers_quota(client_factory, monkeypatch):
    from src.api.routes import answers as answers_route

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setattr(entitlements, "ANON_QUOTA", 1)

    first, second = client_factory(), client_factory()
    assert first.post("/api/v1/answers", json=QUESTION).status_code == 200
    assert first.post("/api/v1/answers", json=QUESTION).status_code == 402

    # A different browser still has its own taste.
    assert second.post("/api/v1/answers", json=QUESTION).status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════

def test_rate_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr(security, "_general", security.SlidingWindow(2))
    assert client.post("/api/v1/answers", json=QUESTION).status_code == 200
    assert client.post("/api/v1/answers", json=QUESTION).status_code == 200
    blocked = client.post("/api/v1/answers", json=QUESTION)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]


def test_sign_in_has_its_own_tighter_budget(client, mailbox, monkeypatch):
    monkeypatch.setattr(security, "_sign_in", security.SlidingWindow(1))
    assert client.post("/api/v1/auth/sign-in", json={"email": "a@example.org"}).status_code == 202
    assert client.post("/api/v1/auth/sign-in", json={"email": "a@example.org"}).status_code == 429


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENTS
# ═══════════════════════════════════════════════════════════════════════════

def test_checkout_is_refused_when_stripe_is_not_configured(client):
    response = client.post("/api/v1/billing/checkout", json={"plan": "pro"})
    assert response.status_code == 503


def test_webhook_requires_a_signature(client, monkeypatch):
    monkeypatch.setattr(gateway, "enabled", lambda: True)
    assert client.post("/api/v1/webhooks/stripe", json={"id": "evt_1"}).status_code == 400


def test_webhook_replay_does_not_move_the_plan_twice(client, test_store, monkeypatch):
    monkeypatch.setattr(gateway, "enabled", lambda: True)
    user = test_store.upsert_user("buyer@example.org", verified=True)

    event = {
        "id": "evt_replay",
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": user["id"],
            "customer": "cus_1",
            "subscription": "sub_1",
        }},
    }
    monkeypatch.setattr(gateway, "parse_webhook", lambda payload, signature: event)

    first = client.post("/api/v1/webhooks/stripe", headers={"Stripe-Signature": "t=1,v1=x"})
    assert first.status_code == 200
    assert "replayed" not in first.json()
    assert test_store.user(user["id"])["plan"] == "pro"

    # Same event id delivered again: acknowledged, but no second mutation.
    test_store.update_subscription(user["id"], plan="free", status="canceled")
    second = client.post("/api/v1/webhooks/stripe", headers={"Stripe-Signature": "t=1,v1=x"})
    assert second.json()["replayed"] is True
    assert test_store.user(user["id"])["plan"] == "free"


def test_paid_features_refuse_to_run_without_a_price(client, monkeypatch):
    monkeypatch.setattr(gateway, "enabled", lambda: True)
    monkeypatch.setattr(billing, "paid_plans_configured", lambda: False)
    response = client.post("/api/v1/billing/checkout", json={"email": "a@example.org"})
    assert response.status_code == 503


# ═══════════════════════════════════════════════════════════════════════════
# PRODUCTION CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

def test_production_refuses_dev_defaults(monkeypatch):
    from src.config import production_problems

    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "session_secret", DEV_SESSION_SECRET)
    monkeypatch.setattr(settings, "base_url", "http://example.org")
    monkeypatch.setattr(settings, "allowed_hosts", ["*"])

    problems = "\n".join(production_problems())
    assert "ACE_SESSION_SECRET" in problems
    assert "https" in problems
    assert "ACE_ALLOWED_HOSTS" in problems


def test_production_requires_mail_when_stripe_is_enabled(monkeypatch):
    from src.config import production_problems

    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "session_secret", "x" * 40)
    monkeypatch.setattr(settings, "base_url", "https://ace.example.org")
    monkeypatch.setattr(settings, "allowed_hosts", ["ace.example.org"])
    monkeypatch.setattr(settings, "require_email_verification", True)
    monkeypatch.setattr(settings, "smtp_host", None)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_dummy")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_dummy")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_dummy")

    problems = "\n".join(production_problems())
    assert "ACE_SMTP_HOST" in problems


def test_production_refuses_unverified_sign_in_with_stripe(monkeypatch):
    from src.config import production_problems

    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "session_secret", "x" * 40)
    monkeypatch.setattr(settings, "base_url", "https://ace.example.org")
    monkeypatch.setattr(settings, "allowed_hosts", ["ace.example.org"])
    monkeypatch.setattr(settings, "require_email_verification", False)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.org")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_dummy")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_dummy")
    monkeypatch.setattr("src.billing.plans.paid_plans_configured", lambda: True)

    problems = "\n".join(production_problems())
    assert "ACE_REQUIRE_EMAIL_VERIFICATION" in problems


def test_auth_pages_are_not_cacheable(client):
    for path in ("/account", "/api/v1/auth/me"):
        response = client.get(path)
        assert "no-store" in response.headers.get("Cache-Control", ""), path


def test_production_configuration_is_clean_when_complete(monkeypatch):
    from src.config import production_problems

    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "session_secret", "x" * 40)
    monkeypatch.setattr(settings, "base_url", "https://ace.example.org")
    monkeypatch.setattr(settings, "allowed_hosts", ["ace.example.org"])
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.org")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_dummy")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_dummy")
    # `PRO.stripe_price_id` is read at import time; the check calls this helper.
    monkeypatch.setattr("src.billing.plans.paid_plans_configured", lambda: True)

    assert production_problems() == []
