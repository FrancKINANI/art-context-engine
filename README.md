# Art Context Engine

Ask a question about a work of art and get a **sourced document**, not a summary:
what the dated documents say, when they said it, where they disagree, and what
nobody recorded. Every sentence is tied to a verified span in a stored source;
unknown rights means citation and link, never text.

The full design is in [`specs/SPEC.md`](specs/SPEC.md). This file is the operator's
view: how to run it, test it, and deploy it without giving away the store.

---

## Quick start

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'

cp .env.example .env
# development without SMTP: set ACE_REQUIRE_EMAIL_VERIFICATION=0 in .env
set -a && . ./.env && set +a

.venv/bin/uvicorn src.api.app:app --reload
# open http://localhost:8000  (API docs at /api/docs in development)
```

The app runs without Stripe and without SMTP: billing disabled means every
feature is free and no quota applies. Sign-in is the only thing that needs mail
(or the development switch).

### Try it

```bash
curl -s localhost:8000/api/v1/works/search?q='op 111' | head
curl -s -X POST localhost:8000/api/v1/answers \
  -H 'content-type: application/json' \
  -d '{"question":"Why does the second movement sound so plain?","work_id":"beethoven-op111"}' \
  | .venv/bin/python -m json.tool | head -40
```

## Tests and evaluation

```bash
.venv/bin/python -m pytest -q                 # the full suite, offline, ~2s
.venv/bin/python -m tests.evaluation.harness  # golden set, hard gates, exit 1 on failure
```

The evaluation suite runs against **recorded HTTP fixtures** by default so the
numbers are reproducible; `--live` is available and deliberately noisy. Hard
gates (§10.2) are citation coverage, unsupported claims, hallucinations, temporal
fidelity, rights violations and refusal correctness. A change that breaks one is
reverted, not tuned.

## What is where

| Path | Responsibility |
|---|---|
| `src/models/` | The contract: `Temporal`, `RightsInfo`, `Answer`, `Block`, `Claim`, `Span` |
| `src/temporal.py`, `src/rights.py` | Precision-preserving dates; the single render/export gate |
| `src/retrieval/` | Adapters, entity resolution, claim extraction, conflict detection, ranking |
| `src/answer/` | Deterministic assembly, citation verification, deepening, export |
| `src/api/` | FastAPI routes, server-rendered reader, hardening middleware |
| `src/billing/` | Plans, entitlements, magic links, Stripe gateway |
| `src/store.py` | SQLite persistence (answers, sources, accounts, usage) |
| `tests/` | Unit, integration, evaluation and adversarial fixtures |
| `deploy/` | systemd unit and Caddy reverse proxy config |

---

## Deploying

One uvicorn process. SQLite is the database, so do not scale workers until the
store moves to Postgres — the store serializes writes and would fight itself.

```bash
sudo useradd -r -s /usr/sbin/nologin ace
sudo mkdir -p /srv/ace && sudo chown ace: /srv/ace
sudo -u ace git clone <repo> /srv/ace
cd /srv/ace && sudo -u ace python -m venv .venv && sudo -u ace .venv/bin/pip install .
sudo cp deploy/ace.service /etc/systemd/system/ && sudo systemctl enable --now ace
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

Caddy terminates TLS and forwards to `127.0.0.1:8000`, so set
`ACE_TRUST_PROXY=1` and `ACE_BASE_URL=https://your.domain`.

### Configuration that must be right

| Variable | Why it matters |
|---|---|
| `ACE_ENV=production` | Enables fail-fast checks; a bad deploy refuses to boot |
| `ACE_SESSION_SECRET` | Signs session cookies. Without it, sessions are forgeable |
| `ACE_BASE_URL=https://…` | Sets the cookie `Secure` flag, HSTS, Stripe return URLs and sign-in links |
| `ACE_ALLOWED_HOSTS` | Host header allowlist; never `*` |
| `ACE_SMTP_*` | Required for sign-in links when Stripe is enabled |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO` | Payments |

`python -c "from src.config import production_problems; print(production_problems())"`
prints what is still missing for this install.

### Stripe

1. Create a recurring Price (monthly) and copy its `price_...` id to `STRIPE_PRICE_PRO`.
2. Add a webhook endpoint: `https://your.domain/api/v1/webhooks/stripe`.
3. Subscribe to `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`,
   `invoice.payment_failed`.
4. Copy the signing secret to `STRIPE_WEBHOOK_SECRET`.

The webhook is the only thing that grants Pro. The browser redirect after
checkout just says "thanks"; the plan moves when the signed event lands. Replays
are ignored by event id, so a retry cannot resurrect a cancelled plan.

### Backups

`data/runtime/ace.sqlite3` is the whole state: answers, sources, accounts, usage.
Back it up with `sqlite3 ace.sqlite3 ".backup /backups/ace-$(date +%F).sqlite3"`;
copying the file while the process writes is not a backup.

---

## Security model

What is enforced in code, and where:

| Control | Implementation |
|---|---|
| Sign-in proves the address | One-time link (32-byte token, SHA-256 at rest, 15 min, single use) |
| Sessions are signed, not guessable | HMAC-SHA256 cookie; random opaque user ids |
| No account enumeration | Sign-in answers identically for known and unknown addresses |
| Cookie theft is limited | `HttpOnly`, `SameSite=Lax`, `Secure` when `ACE_BASE_URL` is https |
| Sign-in links are not cached | `Cache-Control: no-store` on account/auth responses |
| Private annotations stay private | Every row carries an owner; every read filters on it |
| Per-browser free quota | Server-issued anonymous id, so one visitor cannot drain another's |
| Scraping / quota farming | Per-IP sliding window; tighter on sign-in |
| Request body flood | `ACE_MAX_BODY_BYTES`, rejected with 413 before the pipeline |
| Stored XSS from cited sources | Everything HTML-escaped; only `http(s)` URLs become links |
| Script injection | CSP without `unsafe-inline`; CSS and JS are `/static` files |
| Clickjacking, sniffing, referer leak | `frame-ancestors 'none'`, `nosniff`, `no-referrer` |
| Webhook forgery | Stripe signature verification; unsigned events are rejected |
| Webhook replay | Processed event ids are recorded and skipped |
| Host header tricks | `TrustedHostMiddleware` allowlist |
| Error leakage | Unhandled errors return `{"error":"internal_error"}` and are logged |

Known limitations, stated rather than hidden:

- **Rate limits are per process.** Run one worker (which SQLite wants anyway) or
  move the counters to Redis/the database.
- **Anonymous identity is a cookie.** Clearing it is a new trial; the IP limit is
  what bounds that.
- **A leaked sign-in email is a leaked link** for fifteen minutes. That is the
  trade of passwordless auth; the token is single-use.
- **No CSRF tokens**, because session cookies are `SameSite=Lax` and every
  state-changing route is a POST. Revisit if a cross-site integration appears.
- **Answers are public permalinks.** Anyone with an answer id can read the
  document. That is deliberate; it is how a reader shares an explanation.

If you change something in that table, change the test that guards it in
`tests/integration/test_security.py` in the same commit.

## Licence and rights posture

The engine is a context tool; it does not redistribute text it has no right to
display. Rights are determined at ingest, and the render/export gates are the
only path to output (`src/rights.py`). A source with unknown rights appears as
metadata and a link — never as text.
