"""Marketing and account pages: pricing, account, small messages.

Deliberately spare. The pitch is one sentence — "what a work means, and who said
so" — and the pricing table says what the free tier gives you and what Pro
removes. No feature grid that implies the free tier is dishonest, because it is
not: the citations, the conflicts and the refusals are the same on both plans.

The pages share the reader's shell (`reader.shell`) so the masthead, the theme
toggle and the type scale never drift between a document and the page that sells
it.
"""

from __future__ import annotations

import html

from src.api.reader import BRAND, shell


def esc(value: object) -> str:
    return html.escape(str(value or ""))


_PITCH = {
    "free": "Enough to test whether the evidence holds up. No card, no account to start.",
    "pro": "For readers who come back: the same document, without the monthly ceiling.",
}


def pricing_page(plans, *, billing_enabled: bool) -> str:
    cards = []
    for plan in plans:
        featured = plan.id == "pro"
        pitch = _PITCH.get(plan.id, "")
        features = "".join(f"<li>{esc(f)}</li>" for f in plan.features)

        if featured:
            if billing_enabled:
                action = f"""
                <form method="post" action="/billing/subscribe">
                  <label class="sr-only" for="plan-email">Email for your receipt</label>
                  <input id="plan-email" type="email" name="email"
                         placeholder="you@example.com" required autocomplete="email">
                  <button type="submit">Subscribe</button>
                </form>
                <p class="note">Billed by Stripe. Cancel any time from your account.</p>"""
            else:
                action = (
                    '<p class="note">Payments are not configured on this install — '
                    "every feature is free.</p>"
                )
        else:
            action = '<p class="note">No account needed to try it.</p>'

        ribbon = '<span class="ribbon">Unlimited</span>' if featured else ""
        cards.append(
            f'<div class="plan{" featured" if featured else ""}">'
            f"{ribbon}<h3>{esc(plan.name)}</h3>"
            f'<p class="price">{esc(plan.price_display)}</p>'
            f'<p class="pitch">{esc(pitch)}</p>'
            f"<ul>{features}</ul>{action}</div>"
        )

    body = f"""<main id="main"><div class="wrap">
<header class="page-head">
  <p class="kicker">Plans</p>
  <h2>What a work means, and who said so.</h2>
  <p>Every answer is a document: what the dated sources say, where they disagree,
    and what nobody recorded. The citations, the conflicts and the refusals are
    identical on every plan — Pro only removes the monthly ceiling.</p>
</header>
<div class="plans">{''.join(cards)}</div>
<p class="note plans-note">Answers are counted per calendar month. An answer that refuses for
lack of evidence still counts as an answer; it is a real result, not a failure.
Quoted text appears only where the licence allows it — a link is not a downgrade.</p>
</div></main>"""

    return shell(
        f"Plans · {BRAND}",
        body,
        description="Free to try, Pro to keep reading. The same sourced document on every plan.",
        path="/pricing",
        active="pricing",
    )


def account_page(account: dict, *, billing_enabled: bool) -> str:
    usage = account.get("used_this_month", 0)
    limit = account.get("limit")
    unlimited = limit is None
    remaining = "Unlimited" if unlimited else max(0, limit - usage)

    if unlimited:
        meter = '<p class="note">No monthly ceiling on this plan.</p>'
    else:
        meter = (
            f'<progress class="meter" max="{int(limit)}" value="{min(usage, int(limit))}">'
            f"{usage} of {limit} answers used</progress>"
            f'<p class="note">{usage} of {limit} used this month · {remaining} left</p>'
        )

    manage = ""
    if billing_enabled and account.get("has_customer"):
        manage = """
    <form method="post" action="/api/v1/billing/portal" id="portal">
      <button class="btn btn-ghost" type="submit">Manage subscription</button>
    </form>"""

    status = account.get("subscription_status") or "no subscription"

    body = f"""<main id="main"><div class="wrap">
<section class="account">
  <p class="kicker">Account</p>
  <h2>Your reading room</h2>
  <p class="who">{esc(account['email'])}</p>
  <dl class="rows">
    <div class="row"><dt>Plan</dt><dd>{esc(account['plan_name'])}</dd></div>
    <div class="row"><dt>This month</dt><dd>{esc(remaining)} remaining</dd></div>
    <div class="row"><dt>Status</dt><dd>{esc(status)}</dd></div>
  </dl>
  {meter}
  <div class="account-actions">{manage}
    <a class="btn" href="/">Ask a question →</a>
  </div>
</section>
</div></main>"""

    return shell(
        f"Account · {BRAND}",
        body,
        description="Your plan, your quota and your subscription.",
        path="/account",
        noindex=True,
        active="account",
    )


def message_page(title: str, body: str, *, form_action: str | None = None,
                 form_field: str | None = None, cta: str | None = None,
                 status: int = 200) -> str:
    form = ""
    if form_action and form_field:
        label = "Email address" if form_field == "email" else "Your question"
        field_type = "email" if form_field == "email" else "text"
        placeholder = "you@example.com" if form_field == "email" else "Name the work"
        form = f"""
  <form class="ask" method="post" action="{esc(form_action)}">
    <label class="sr-only" for="msg-field">{esc(label)}</label>
    <input id="msg-field" type="{field_type}" name="{esc(form_field)}"
           placeholder="{placeholder}" required maxlength="500"
           {'autocomplete="email"' if form_field == 'email' else 'autocomplete="off"'}>
    <button type="submit">{esc(cta or 'Continue')}</button>
  </form>"""

    page = f"""<main id="main"><div class="wrap">
<section class="message">
  <h2>{esc(title)}</h2>
  <p class="question">{esc(body)}</p>
  {form}
</section>
</div></main>"""

    return shell(f"{title} · {BRAND}", page, path="/message", noindex=True)
