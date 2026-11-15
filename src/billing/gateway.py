"""Stripe gateway — a thin, optional wrapper.

Stripe is the payments platform recommended by the catalogue in this environment
(`quality_score 95`: "safest generic default for payments"). It is the only
integration here that touches money, so the rules are explicit:

* **The SDK is optional.** Without `STRIPE_SECRET_KEY` the app runs in free mode
  and never imports Stripe. A missing key must not break the reader.
* **Checkout is server-created.** Prices are server-side ids; the browser never
  sends an amount.
* **Webhooks are the source of truth.** A returning customer whose checkout
  succeeded but whose webhook has not landed is still on the free plan. Trusting
  the redirect is how people get free subscriptions.
* **Signature verification is not optional.** An unsigned webhook is a stranger
  telling us someone paid.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import stripe


@dataclass
class CheckoutSession:
    url: str
    id: str


class BillingDisabled(RuntimeError):
    """Raised when a paid feature is requested without configured keys."""


def secret_key() -> str | None:
    return os.environ.get("STRIPE_SECRET_KEY")


def publishable_key() -> str | None:
    return os.environ.get("STRIPE_PUBLISHABLE_KEY")


def webhook_secret() -> str | None:
    return os.environ.get("STRIPE_WEBHOOK_SECRET")


def enabled() -> bool:
    return bool(secret_key())


def _client() -> None:
    if not enabled():
        raise BillingDisabled("STRIPE_SECRET_KEY is not configured")
    stripe.api_key = secret_key()


def create_checkout_session(
    *,
    customer_email: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
    client_reference_id: str,
    customer_id: str | None = None,
) -> CheckoutSession:
    _client()
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        customer=customer_id or None,
        customer_email=None if customer_id else customer_email,
        client_reference_id=client_reference_id,
        success_url=success_url,
        cancel_url=cancel_url,
        allow_promotion_codes=True,
        subscription_data={"metadata": {"user_id": client_reference_id}},
    )
    return CheckoutSession(url=session.url, id=session.id)


def create_portal_session(*, customer_id: str, return_url: str) -> str:
    _client()
    session = stripe.billing_portal.Session.create(
        customer=customer_id, return_url=return_url
    )
    return session.url


def parse_webhook(payload: bytes, signature: str) -> dict:
    """Verify and decode. Raises on a bad signature — never best-effort."""
    _client()
    secret = webhook_secret()
    if not secret:
        raise BillingDisabled("STRIPE_WEBHOOK_SECRET is not configured")
    event = stripe.Webhook.construct_event(payload, signature, secret)
    return event if isinstance(event, dict) else event.to_dict_recursive()
