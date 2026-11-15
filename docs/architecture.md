# Architecture

A one-page map of what talks to what, and which invariants are not allowed to
break. The *why* is in [`specs/SPEC.md`](../specs/SPEC.md); this is the *where*.

```
question
   │
   ├─ entity_resolver ──────► Work (curated registry, data/seed/works.json)
   ├─ question.interpret ───► Interpretation (type, aspect, predicates)
   │
   ├─ gatherer ─────────────► ContextPack + sources
   │     ├─ adapters/*  (Wikipedia, Wikidata, Genius, IMSLP, Poetry Foundation,
   │     │               OpenAlex, Semantic Scholar, Wikisource)
   │     ├─ http.Backend (fixture | live | auto)  ← the only place bytes enter
   │     ├─ temporal_anchor  document date vs asserted date (§3.2.1)
   │     ├─ claim_extractor  spans verified against stored text (§4.4)
   │     ├─ conflict_detector typed conflicts, precision over recall (§4.5)
   │     └─ ranker           authority tiers + relevance (§4.6)
   │
   ├─ answer.assemble ──────► Answer (blocks, claims, timeline, gaps, attribution)
   │     ├─ sufficiency      refuse before prose (§10.6)
   │     └─ claims           citation verification, CITATION_ONLY handling
   │
   └─ store.save ───────────► SQLite: answers, packs, sources
```

## Layers and what may depend on what

| Layer | May import | Must not |
|---|---|---|
| `models/`, `temporal.py`, `rights.py` | stdlib, pydantic | anything network or storage |
| `retrieval/` | models, `http`, adapters | `answer/`, `api/` |
| `answer/` | models, `rights` | adapters, `api/` |
| `api/`, `store.py`, `billing/` | everything | reach around `service.answer_question` for the pipeline |

`service.answer_question` is the only pipeline entry point. The API, the HTML
reader and the evaluation harness all call it, so a metric measured there is a
metric on the product (§10). If a route re-implements the pipeline, the
evaluation stops meaning anything.

## Invariants enforced in code, not in prose

1. **No bare dates.** Every date is a `Temporal` with bounds, precision and the
   original wording (§3.2). Sorting uses `earliest`; `UNKNOWN` is not a date.
2. **Rights gate at ingest and at render.** `Source.rights` is determined before
   storage; `rights.gate_span` is the only way text reaches output. Fail closed:
   unknown rights → metadata + link.
3. **A claim without a verified span is not evidence.** `answer/claims.py`
   re-resolves the span against the stored text; assembly drops blocks whose
   claims do not verify.
4. **Conflicts are shown, never resolved silently.** Every position keeps its
   own source and span; precision is preferred over recall.
5. **Refusal is a result, not an error.** Fewer than three independent sources,
   or no scholarly source with a verifiable quotation → `INSUFFICIENT` with the
   evidence and the gaps (§10.6).
6. **Determinism first, LLM second.** The assembler is deterministic; the LLM is
   optional and must beat it on relevance without losing a hard gate (§10.4).

## HTTP surface

| Group | Routes |
|---|---|
| Answers | `POST /api/v1/answers`, `GET /api/v1/answers/{id}`, `/deepening`, `/feedback` |
| Works | `/api/v1/works/search`, `/{id}/context`, `/{id}/timeline`, `/{id}/sources`, `/{id}/conflicts` |
| Export | `GET /api/v1/export/{id}?format=markdown\|json` |
| Annotations | `POST/GET /api/v1/annotations` (owner-scoped) |
| Accounts | `/api/v1/auth/sign-in`, `/auth/sign-out`, `/auth/me`, `/auth/verify` |
| Billing | `/api/v1/billing/plans`, `/entitlement`, `/checkout`, `/portal`, `/webhooks/stripe` |
| Pages (unversioned) | `/`, `/ask`, `/a/{id}`, `/a/{id}/b/{block}/deepen`, `/pricing`, `/account` |
| Static | `/static/app.css`, `/static/app.js` (allowlist, no directory serving) |
| Ops | `/healthz` |

JSON under `/api/v1`, pages unversioned: a permalink or a pricing URL a customer
bookmarked must not break on an API version bump.

## Request lifecycle (security)

1. `TrustedHostMiddleware` — reject a forged Host.
2. `harden` middleware — body size (413), per-IP rate limit (429), then mint the
   visitor id for a new browser *before* the route so the first answer is metered
   to the right visitor.
3. Route — entitlement check, pipeline, persistence.
4. Response — security headers, `no-store` on credential paths, anonymous cookie.

## Storage

One SQLite file, WAL mode, `busy_timeout=5000`. Payloads are the model JSON, so
the store cannot drift from the schema. `_migrate` adds columns that a deployed
database is missing; rows are read by name because `ALTER TABLE` appends and
positional reads would silently shift fields.

Orders of magnitude the MVP is sized for: thousands of answers, tens of
concurrent readers, one writer. Move to Postgres before adding a second uvicorn
worker, not after.
