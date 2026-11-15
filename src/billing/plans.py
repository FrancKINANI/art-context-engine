"""Plans and entitlements.

The product decision encoded here is deliberately narrow: the free tier is enough
to understand *a few* works properly, and Pro removes the limit. It is not "free
gives you a worse answer" — rights, citations, conflicts and honest refusals are
never behind a paywall. §3.7 *is* the product; selling it would be selling the
one thing that makes the tool worth trusting.

Quotas count **answers**, not questions asked: assembling a document costs the
corpus, the extraction and the assembly. A refusal still counts, because it
still cost that work and because otherwise a broken question is free retries.

Everything here is configuration, so pricing can change without touching the
pipeline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price_display: str
    answers_per_month: int | None      # None = unlimited
    features: list[str] = field(default_factory=list)
    stripe_price_id: str | None = None

    @property
    def is_unlimited(self) -> bool:
        return self.answers_per_month is None


FREE = Plan(
    id="free",
    name="Free",
    price_display="€0",
    answers_per_month=int(os.environ.get("ACE_FREE_QUOTA", "5")),
    features=[
        "5 sourced answers per month",
        "Full citations, conflicts and gaps",
        "Markdown and JSON export",
        "Private annotations",
    ],
)

PRO = Plan(
    id="pro",
    name="Pro",
    price_display=os.environ.get("ACE_PRO_PRICE_DISPLAY", "€9 / month"),
    answers_per_month=None,
    features=[
        "Unlimited sourced answers",
        "Inline deepening on every block",
        "Follow-up documents and permalinks",
        "Everything in Free",
    ],
    stripe_price_id=os.environ.get("STRIPE_PRICE_PRO"),
)

PLANS: dict[str, Plan] = {FREE.id: FREE, PRO.id: PRO}


def get_plan(plan_id: str) -> Plan:
    return PLANS.get(plan_id, FREE)


def plan_from_stripe_price(price_id: str | None) -> Plan:
    for plan in PLANS.values():
        if plan.stripe_price_id and plan.stripe_price_id == price_id:
            return plan
    return FREE


def paid_plans_configured() -> bool:
    """Without a price id there is nothing to sell, and checkout is refused."""
    return bool(PRO.stripe_price_id)
