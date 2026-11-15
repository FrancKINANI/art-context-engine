"""Billing: sign-in, checkout, portal, webhook, pricing.

Design notes worth keeping in mind when this is published:

* **Sign-in is an email link, not a password.** The account only needs to be
  recoverable and to hold a plan; storing hashes of reused passwords to achieve
  that is a liability, not a feature. The link also *proves the address* — the
  earlier version issued a session to anyone who typed a subscriber's email.
* **Stripe Checkout owns the card form.** No card data touches this server, which
  keeps the install out of PCI scope.
* **The webhook is the only thing that grants Pro.** `checkout.session.completed`
  and `customer.subscription.*` move the plan; the browser redirect just says
  "thanks". Replayed events are ignored by id, so a retry cannot resurrect or
  cancel a plan out of order.
* **Downgrades happen on `customer.subscription.deleted`** and when a
  subscription turns `past_due`/`unpaid` — a failed payment is not a plan.
* **JSON lives under /api/v1, pages do not.** A pricing URL a customer bookmarked
  must not break when the API version changes.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, EmailStr

from src.api import reader, site
from src.api.deps import store
from src.billing import entitlements, gateway, mailer
from src.billing.plans import FREE, PLANS, PRO, paid_plans_configured
from src.config import settings

logger = logging.getLogger("ace.billing")

router = APIRouter(tags=["billing"])
#: HTML pages are unversioned: a pricing link a customer bookmarked must not
#: break when the API moves to v2.
pages = APIRouter(include_in_schema=False)


# ═══════════════════════════════════════════════════════════════════════════
# JSON API
# ═══════════════════════════════════════════════════════════════════════════

class SignInRequest(BaseModel):
    email: EmailStr


class CheckoutRequest(BaseModel):
    email: EmailStr | None = None
    plan: str = "pro"


def _send_link(email: str) -> None:
    token = entitlements.create_login_token(store(), email)
    if not mailer.send_sign_in_link_safely(email, entitlements.login_link(token)):
        logger.warning("sign-in mail could not be sent")


def _start_sign_in(email: str) -> Response:
    """Issue a session (development only) or mail a one-time link.

    When verification is disabled the session is issued directly — that switch
    is refused by ``production_problems`` whenever Stripe is enabled.
    """
    if not settings.require_email_verification:
        user = store().upsert_user(email, verified=True)
        response = Response(
            content=json.dumps({"status": "signed_in", "user": entitlements.account(store(), user["id"])}),
            media_type="application/json",
        )
        entitlements.set_session_cookie(response, user["id"])
        return response

    if not mailer.configured():
        logger.error("sign-in requested but SMTP is not configured")
        raise HTTPException(
            status_code=503,
            detail="Sign-in is temporarily unavailable. Please try again later.",
        )

    _send_link(email)
    # Same answer either way: an attacker must not learn which addresses have
    # accounts, and a mail failure must not become an oracle either.
    return Response(
        content=json.dumps({"status": "link_sent"}),
        media_type="application/json",
        status_code=202,
    )


@router.get("/billing/plans")
def list_plans() -> dict:
    return {
        "billing_enabled": gateway.enabled(),
        "plans": [
            {
                "id": p.id,
                "name": p.name,
                "price": p.price_display,
                "answers_per_month": p.answers_per_month,
                "features": p.features,
            }
            for p in PLANS.values()
        ],
    }


@router.get("/billing/entitlement")
def entitlement(request: Request) -> dict:
    return entitlements.check(entitlements.identity(request, store()), store()).as_dict()


@router.post("/auth/sign-in", response_model=None)
def sign_in(request: SignInRequest) -> Response:
    return _start_sign_in(str(request.email))


@router.post("/auth/sign-out")
def sign_out() -> Response:
    response = Response(content=json.dumps({"status": "signed_out"}), media_type="application/json")
    entitlements.clear_session_cookie(response)
    return response


@router.get("/auth/me")
def me(request: Request) -> dict:
    user_id = entitlements.read_session(request.cookies.get(entitlements.COOKIE_NAME))
    account = entitlements.account(store(), user_id) if user_id else None
    if account is None:
        raise HTTPException(status_code=401, detail="not signed in")
    return account


@router.post("/billing/checkout")
def checkout(request: Request, body: CheckoutRequest) -> dict:
    if not gateway.enabled():
        raise HTTPException(status_code=503, detail="billing is not configured on this install")
    if not paid_plans_configured():
        raise HTTPException(status_code=503, detail="no Stripe price configured for Pro")

    # A signed-in reader subscribes as themselves; a visitor may start a checkout
    # with any address, and the webhook attaches the plan to that address once it
    # is verified by a sign-in link.
    user_id = entitlements.read_session(request.cookies.get(entitlements.COOKIE_NAME))
    user = store().user(user_id) if user_id else None
    email = str(body.email) if body.email else (user or {}).get("email")
    if user is None:
        user = store().upsert_user(email) if email else None

    session = gateway.create_checkout_session(
        customer_email=email or "",
        price_id=PRO.stripe_price_id or "",
        success_url=f"{settings.base_url.rstrip('/')}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.base_url.rstrip('/')}/pricing",
        client_reference_id=(user or {}).get("id", ""),
        customer_id=(user or {}).get("stripe_customer_id"),
    )
    return {"checkout_url": session.url}


@router.post("/billing/portal")
def portal(request: Request) -> dict:
    user_id = entitlements.read_session(request.cookies.get(entitlements.COOKIE_NAME))
    if user_id is None:
        raise HTTPException(status_code=401, detail="not signed in")
    user = store().user(user_id)
    if not user or not user.get("stripe_customer_id"):
        raise HTTPException(status_code=400, detail="no subscription to manage")
    url = gateway.create_portal_session(
        customer_id=user["stripe_customer_id"],
        return_url=f"{settings.base_url.rstrip('/')}/account",
    )
    return {"portal_url": url}


# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOK
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> dict:
    if not gateway.enabled():
        raise HTTPException(status_code=503, detail="billing is not configured")
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="missing signature")

    payload = await request.body()
    try:
        event = gateway.parse_webhook(payload, stripe_signature)
    except Exception as exc:  # bad signature, malformed payload
        logger.warning("rejected webhook: %s", type(exc).__name__)
        raise HTTPException(status_code=400, detail=f"invalid webhook: {type(exc).__name__}") from exc

    if store().event_seen(event.get("id", "")):
        # Stripe retries; a second delivery of the same event must not move the
        # plan again (a late `subscription.deleted` replay could cancel a new one).
        logger.info("ignoring replayed webhook %s", event.get("id"))
        return {"received": True, "type": event.get("type", ""), "replayed": True}

    kind = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    if kind == "checkout.session.completed":
        user_id = data.get("client_reference_id") or ""
        customer = data.get("customer")
        subscription = data.get("subscription")
        if user_id and store().user(user_id):
            store().update_subscription(
                user_id,
                plan=PRO.id,
                customer_id=customer,
                subscription_id=subscription,
                status="active",
            )
            logger.info("upgraded user %s to pro", user_id)

    elif kind in ("customer.subscription.created", "customer.subscription.updated"):
        customer = data.get("customer")
        user = store().user_by_customer(customer) if customer else None
        status = data.get("status", "")
        plan = "pro" if status in ("active", "trialing") else "free"
        if user:
            store().update_subscription(
                user["id"],
                plan=plan,
                subscription_id=data.get("id"),
                status=status,
                period_end=_period_end(data),
            )

    elif kind == "customer.subscription.deleted":
        user = store().user_by_customer(data.get("customer") or "")
        if user:
            store().update_subscription(
                user["id"], plan=FREE.id, status="canceled", period_end=_period_end(data)
            )

    elif kind == "invoice.payment_failed":
        customer = data.get("customer")
        user = store().user_by_customer(customer) if customer else None
        if user:
            store().update_subscription(user["id"], plan=FREE.id, status="past_due")

    # 200 for everything we understood, including events we ignore: Stripe
    # retries anything else and will otherwise retry forever. The id is recorded
    # only after the mutation succeeds, so a failure is retried rather than lost.
    store().mark_event(event.get("id", ""))
    return {"received": True, "type": kind}


def _period_end(obj: dict) -> str | None:
    value = obj.get("current_period_end")
    if not value:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# HTML PAGES
# ═══════════════════════════════════════════════════════════════════════════

@pages.get("/pricing", response_class=HTMLResponse)
def pricing() -> str:
    return site.pricing_page(list(PLANS.values()), billing_enabled=gateway.enabled())


@pages.post("/billing/subscribe")
def subscribe_form(email: str = Form(...)) -> Response:
    if not gateway.enabled() or not paid_plans_configured():
        return HTMLResponse(
            site.message_page(
                "Billing is not configured",
                "This install has no payment provider set up, so every feature is free.",
            ),
            status_code=200,
        )
    user = store().upsert_user(email)
    session = gateway.create_checkout_session(
        customer_email=email,
        price_id=PRO.stripe_price_id or "",
        success_url=f"{settings.base_url.rstrip('/')}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.base_url.rstrip('/')}/pricing",
        client_reference_id=user["id"],
        customer_id=user.get("stripe_customer_id"),
    )
    return RedirectResponse(session.url, status_code=303)


@pages.get("/billing/success", response_class=HTMLResponse)
def billing_success() -> str:
    return site.message_page(
        "Thanks — your plan is active",
        "Stripe has taken the payment. Your account is upgraded as soon as the "
        "webhook lands (usually a second or two). Sign in to see your plan.",
        form_action="/account/sign-in",
        form_field="email",
        cta="Send me a sign-in link",
    )


@pages.get("/account", response_class=HTMLResponse)
def account_page(request: Request) -> str:
    user_id = entitlements.read_session(request.cookies.get(entitlements.COOKIE_NAME))
    if user_id is None:
        return site.message_page(
            "Sign in",
            "There is no password. We email you a link that signs you in once.",
            form_action="/account/sign-in",
            form_field="email",
            cta="Send me a sign-in link",
        )
    account = entitlements.account(store(), user_id)
    if account is None:
        return site.message_page("Account not found", "That session is no longer valid.")
    return site.account_page(account, billing_enabled=gateway.enabled())


@pages.post("/account/sign-in")
def account_sign_in(email: str = Form(...)) -> Response:
    # Development only: no mailer needed, and the cookie must land on the
    # response that is actually returned (setting it on a discarded response was
    # a silent no-op that made the form appear to work while signing nobody in).
    if not settings.require_email_verification:
        user = store().upsert_user(email, verified=True)
        response = RedirectResponse("/account", status_code=303)
        entitlements.set_session_cookie(response, user["id"])
        return response

    if not mailer.configured():
        return HTMLResponse(
            site.message_page(
                "Sign-in is unavailable",
                "This install has no mail provider configured, so sign-in links "
                "cannot be sent. Please try again later.",
            ),
            status_code=503,
        )

    _send_link(email)
    return HTMLResponse(
        site.message_page(
            "Check your email",
            "If that address is valid, a sign-in link is on its way. It works once "
            "and expires in fifteen minutes.",
        ),
        status_code=202,
    )


@pages.get("/auth/verify", response_class=HTMLResponse)
def verify(token: str = "") -> Response:
    """Burn the link and start the session."""
    email = entitlements.consume_login_token(store(), token)
    if email is None:
        return HTMLResponse(
            site.message_page(
                "That link is not valid",
                "It may have been used already or expired. Ask for a new one.",
                form_action="/account/sign-in",
                form_field="email",
                cta="Send me a sign-in link",
            ),
            status_code=400,
        )

    user = store().upsert_user(email, verified=True)
    response = RedirectResponse("/account", status_code=303)
    entitlements.set_session_cookie(response, user["id"])
    logger.info("signed in user %s", user["id"])
    return response
