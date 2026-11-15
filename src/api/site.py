"""Marketing and account pages: pricing, account, small messages.

Deliberately spare. The pitch is one sentence — "understand a work in its world,
with every sentence tied to a dated source" — and the pricing table says what
the free tier gives you and what Pro removes. No feature grid that implies the
free tier is dishonest, because it is not: the citations, the conflicts and the
refusals are the same on both plans.
"""

from __future__ import annotations

import html

from src.api.reader import HEAD


def esc(value: object) -> str:
    return html.escape(str(value or ""))


def _shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — Art Context Engine</title>
{HEAD}</head><body>
<header class="top"><h1>Art Context Engine</h1>
  <a href="/" class="question">Ask</a>
  <a href="/pricing" class="question">Pricing</a>
  <a href="/account" class="question">Account</a>
</header>
<main><article>{body}</article><aside></aside></main>
</body></html>"""


def pricing_page(plans, *, billing_enabled: bool) -> str:
    cards = []
    for plan in plans:
        featured = " featured" if plan.id == "pro" else ""
        features = "".join(f"<li>{esc(f)}</li>" for f in plan.features)
        if plan.id == "pro":
            if billing_enabled:
                action = f"""
                <form method="post" action="/billing/subscribe">
                  <input type="email" name="email" placeholder="you@example.com" required>
                  <button type="submit">Subscribe</button>
                </form>
                <p class="note">Billed by Stripe. Cancel any time from your account.</p>"""
            else:
                action = '<p class="note">Payments are not configured on this install — everything is free.</p>'
        else:
            action = '<p class="note">No account needed to try it.</p>'
        cards.append(
            f'<div class="plan{featured}"><h3>{esc(plan.name)}</h3>'
            f'<div class="price">{esc(plan.price_display)}</div>'
            f"<ul>{features}</ul>{action}</div>"
        )

    return _shell("Pricing", f"""
<h2>Understand a work in its world.</h2>
<p class="question">Every answer is a document: what the dated sources say, where
they disagree, and what nobody recorded. The citations and the disagreements are
the same on every plan — Pro only removes the monthly limit.</p>
<div class="price-grid">{''.join(cards)}</div>
<p class="note">Answers are counted per calendar month. An answer that refuses for
lack of evidence still counts as an answer; it is a real result, not a failure.</p>
""")


def account_page(account: dict, *, billing_enabled: bool) -> str:
    usage = account.get("used_this_month", 0)
    limit = account.get("limit")
    usage_line = (
        "Unlimited answers" if limit is None
        else f"{usage} of {limit} answers used this month"
    )
    manage = ""
    if billing_enabled and account.get("has_customer"):
        manage = """
        <form method="post" action="/api/v1/billing/portal" id="portal">
          <button type="submit">Manage subscription</button>
        </form>"""

    return _shell("Account", f"""
<h2>Your account</h2>
<p class="question">{esc(account['email'])} · plan: <strong>{esc(account['plan_name'])}</strong></p>
<p>{esc(usage_line)}</p>
<p class="note">Status: {esc(account.get('subscription_status') or 'no subscription')}</p>
{manage}
<p><a href="/">Ask a question →</a></p>
""")


def message_page(title: str, body: str, *, form_action: str | None = None,
                 form_field: str | None = None, cta: str | None = None,
                 status: int = 200) -> str:
    form = ""
    if form_action and form_field:
        form = f"""
        <form class="ask" method="post" action="{esc(form_action)}">
          <input type="email" name="{esc(form_field)}" placeholder="you@example.com" required>
          <button type="submit">{esc(cta or 'Continue')}</button>
        </form>"""
    return _shell(title, f"<h2>{esc(title)}</h2><p class=\"question\">{esc(body)}</p>{form}")
