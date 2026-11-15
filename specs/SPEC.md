# Art Context Engine — Specification

**Project:** art-context-engine
**Version:** 0.3.0 (MVP spec — rights, conflicts, time, evaluation)
**Status:** Draft
**Owner:** Julius

---

## 1. Vision

A tool that, given any artwork (music, poetry, visual art, etc.), assembles a **sourced, temporal, multi-perspective context pack** — primary sources, scholarly analysis, contemporary reception, artist's life at that moment — so anyone can understand the work *in its world*.

Not a summary. A **context engine**.

**The unit of the product is a question, not a pack.** Someone asks *"why does the
Arietta sound so plain?"* and receives a sourced explanation they can read, share,
export, and open sentence by sentence. The context pack is the evidence layer
behind that explanation — surfaced where it earns its place, never dumped wholesale.

Three properties decide whether this is credible, and each has its own section here:

1. **Rights** (§3.4) — we may only show what we are allowed to show, and we must know
   which is which before a source is stored, not after it is rendered.
2. **Time** (§3.2) — a 1840 biography describing an 1822 event is evidence about 1822
   dated 1840. Collapsing the two destroys the timeline.
3. **Conflict** (§4.5) — disagreement between sources is the product's differentiator
   and its most dangerous output. False conflicts are worse than silence.

And one property decides whether it improves: **evaluation** (§10).

---

## 2. Scope (MVP → v1)

| Phase | Art Forms | Sources | Users | Social |
|-------|-----------|---------|-------|--------|
| **MVP (weeks 1-4)** | Music (classical + contemporary rap/hip-hop), Poetry | Public domain + open APIs + web retrieval | Curious self-learner | Private annotations only |
| **v0.2** | Visual art, Literature | Paywalled scholarly (via partnerships) | + Students/teachers | Shared context packs |
| **v1.0** | All + film, theater | Full source graph | Everyone | Discussions, teacher→student flows |

**MVP constraint:** One question about one work → one sourced explanation (a document)
→ inline deepening → export. No auth, no social graph, no real-time.

**Chosen for MVP (decision recorded):**
- **Answer shape:** a document, with inline deepening. Follow-ups on a document become
  child documents — never a transient chat.
- **User #1:** the curious self-learner. Later audiences (students, teachers) change
  length and export needs, not the pipeline.
- **Social:** deferred, but the seams are designed now (§3.8).
- **Rights posture:** fail closed. Unknown rights means metadata + link, never text.
- **Conflict posture:** show disagreement, never resolve it silently.

---

## 3. Core Concepts

### 3.1 Work Entity

```python
Work:
  id: str                       # ULID
  title: str
  type: WorkType                # MUSIC, POETRY, VISUAL_ART, etc.
  creators: list[CreatorRef]    # composer, performer, poet, artist
  date_created: Temporal        # composition, writing, painting period (§3.2)
  date_published: Temporal?     # premiere, publication, exhibition
  location_created: GeoRef?
  location_premiered: GeoRef?
  identifiers: dict             # ISWC, ISRC, OCLC, Wikidata QID, MusicBrainz MBID
  metadata: dict                # form, key, instrumentation, language, medium
```

`Temporal` everywhere a date appears. There are no bare date strings in the model, and
no `DateRange` shortcut — a range is just a `Temporal` whose bounds differ (§3.2).

### 3.2 Temporal Model (gap: precision)

Art history is full of dates that are known to the decade, disputed to the year, or only
survivable as "shortly after the Congress of Vienna". A timeline built on `datetime` lies
about all of them. Every date in this system is a **bounded interval with declared
precision and a verbatim original**.

```python
Temporal:
  earliest: date?               # absolute lower bound; None = open ("before 1820")
  latest: date?                 # absolute upper bound; None = open ("after 1820")
  precision: Precision          # DAY, MONTH, YEAR, DECADE, CENTURY, UNKNOWN
  qualifier: Qualifier          # EXACT, BEFORE, AFTER, CIRCA, BETWEEN, DISPUTED, UNKNOWN
  display: str                  # the original wording, verbatim: "Kurz nach 1820"
  calendar: Calendar            # GREGORIAN (default), JULIAN, HIJRI, OTHER
  confidence: float             # 0-1: how sure we are of the bounds themselves
  source_id: str?               # the evidence for this dating
```

**Rules**

1. **Bounds, not points.** `earliest == latest` only when precision is `DAY`.
2. **CIRCA has a declared window**, never a silent one:

   | precision | CIRCA window |
   |-----------|--------------|
   | DAY       | ±1 year (a "circa day" is a year) |
   | MONTH     | ±1 year |
   | YEAR      | ±2 years |
   | DECADE    | ±5 years (i.e. the decade itself) |
   | CENTURY   | ±50 years |
   | UNKNOWN   | ±100 years, and the UI must show "date unknown" |

3. **Open bounds are legal.** `BEFORE` sets `latest` only; `AFTER` sets `earliest` only.
   Sorting uses `earliest` and puts open pasts first, open futures last.
4. **UNKNOWN is not a fallback date.** Undated events render in a separate *undated*
   bucket with the reason ("premiere documented, date not recorded"), never at 1820
   because that is where the work sits.
5. **Non-Gregorian dates are stored with their calendar** and converted for sorting;
   `display` keeps the original. Never rewrite a Russian 1825 date to Gregorian silently.
6. **Precision is displayed.** `c. 1821`, `1821?`, `1820s`, `1821-01-15` are four
   different claims and the UI renders each as written.
7. **Disputed datings are conflicts**, not averages: if two sources date the composition
   differently beyond both windows, that is a `CHRONOLOGICAL` conflict (§4.5), not a
   midpoint.

### 3.2.1 Document date vs. asserted date (the anchoring distinction)

This is the single most common way timelines in this category go wrong.

```python
Source:
  date_document: Temporal        # when this document was written/published
  ...
Event:                           # a timeline entry
  when: Temporal                  # when the event happened (may be derived from a
                                  # document that postdates it)
  date_asserted_by_document: Temporal   # when the evidence said it
  attested_by: list[SourceRef]    # source + span + who asserts it
```

A letter from 1822 is evidence about January 1822. A biography from 1840 describing
January 1822 is evidence about January 1822 **as told in 1840**. The timeline places the
event at 1822 and records the 1840 attribution; the reader can see both. Consequence: a
claim's credibility is a function of *both* dates, and a very late account of a very
early event is marked as such ("first attested 1962").

### 3.3 Source Document

```python
Source:
  id: str
  work_id: str
  type: SourceType               # PRIMARY, SECONDARY, TERTIARY, CONTEMPORARY_REVIEW
  subtype: str                   # "letter", "diary", "review", "scholarly_article",
                                 # "sketch", "interview", "liner_notes", "score"
  title: str
  author: str
  date_document: Temporal        # §3.2.1 — when written, not what it describes
  location: GeoRef?
  url: str?                      # canonical link
  text: str                      # stored text, subject to rights.display_policy
  language: str
  rights: RightsInfo             # §3.4 — decides what may be stored and shown
  authority: AuthorityTier       # PRIMARY > SCHOLARLY > REFERENCE > CONTEMPORARY_REVIEW
  citations: list[Citation]      # internal references
```

Two fields changed from v0.2 on purpose:

- **`confidence` is gone.** It was a placeholder that duplicated ranking. Authority is a
  tier (§4.6); the numeric score is computed from tier, date, relevance, and rights.
- **`text` is not always the full text.** What is stored is whatever
  `rights.storage_allowed` permits; everything else is a link plus metadata.

### 3.4 Rights (gap: enforcement, not annotation)

Accessible is not redistributable. A `rights` field that nobody checks is decoration;
this section defines a model and the single enforcement point.

```python
RightsInfo:
  status: RightsStatus           # PUBLIC_DOMAIN, CC0, CC_BY, CC_BY_SA, CC_BY_NC,
                                 # PROPRIETARY_LICENSED, IN_COPYRIGHT_UNLICENSED,
                                 # UNKNOWN
  license_id: str?               # SPDX-style: "CC-BY-4.0", "PD-US", "GENIUS-TOS"
  license_url: str?
  jurisdiction: str              # where the determination was made: "US", "EU", "FR"
  rights_holder: str?
  determined_at: date
  determination: Determination   # API_METADATA, PAGE_DECLARED, CURATOR, HEURISTIC
  display_policy: DisplayPolicy  # FULL_TEXT, EXCERPT, QUOTE_SPAN, LINK_ONLY,
                                 # METADATA_ONLY
  excerpt_limit_chars: int?      # enforced when policy is EXCERPT or QUOTE_SPAN
  storage_allowed: bool          # may we persist text at all?
  redistribution_allowed: bool   # may exports contain the text?
  attribution_required: bool
  attribution_text: str?         # exact string the license demands
  notes: str?
```

**The enforcement rule.** Every render path — document, deepening, export, API — calls
`rights.gate(source, span) -> RenderedText | CitationOnly`. There is no second
implementation, no template that formats `source.text` directly, and no "we'll fix it at
export". A source whose policy is `LINK_ONLY` or `METADATA_ONLY` renders as a citation
with a URL and nothing else.

| display_policy | Document may show | Export may include | Stored |
|---|---|---|---|
| `FULL_TEXT` | full text | text | text |
| `EXCERPT` | up to `excerpt_limit_chars` | same | truncated |
| `QUOTE_SPAN` | only the verified span, ≤ limit | span | span only |
| `LINK_ONLY` | title, author, date, URL | citation + URL | metadata only |
| `METADATA_ONLY` | title, author, date | citation | metadata only |

**Default is fail-closed.** Anything not positively determined is `UNKNOWN` →
`METADATA_ONLY` + `storage_allowed=False`. A new adapter cannot grant itself rights.

**Consequences that are deliberate, not inconvenient:**

- **Lyrics are never stored or quoted.** Genius returns licensed lyrics; we keep the URL,
  the song metadata, and our own claim referencing them (`stance: CITATION_ONLY`, §3.6).
  The document may say "the verse names X (see Genius)"; it may not reproduce the verse.
- **A claimed span must fit the policy.** If the span needed to support a claim exceeds
  the excerpt limit, the claim degrades to citation-only — it is not silently truncated
  into a misquote, and a paraphrase is not substituted for the quote.
- **Jurisdiction is recorded**, because PD is not universal: a work PD in the US may be
  in copyright in the EU. MVP determines rights in the source's jurisdiction and the
  user's, and when they conflict takes the stricter.
- **Attribution travels with the text.** `attribution_text` is emitted in exports
  (§6) and cannot be stripped by a renderer.
- **Determinations are data with dates.** `determined_at` older than 12 months on a
  `HEURISTIC` determination is re-checked before export.

### 3.5 Context Pack (Evidence Layer)

```python
ContextPack:
  work_id: str
  generated_at: datetime
  timeline: list[TimelineEvent]      # artist life + world events + work milestones
  sources: list[Source]              # ranked, clustered, deduplicated, rights-gated
  claims: list[Claim]                # extracted assertions (§4.4) — the conflict input
  conflicts: list[Conflict]          # §4.5
  gaps: list[Gap]                    # known missing context
  annotations: list[Annotation]?     # user's private notes
```

The pack is not the deliverable. It is the retrieved, dated, ranked evidence set that
the answer is assembled from — and the thing every sentence in the answer must trace
back to.

### 3.6 Answer (Output)

The answer is a **document**. It is the only thing the user came for.

```python
Answer:
  id: str                    # ULID, also the permalink
  parent_id: str?            # set when this is a follow-up on another answer
  work_id: str
  question: str              # the user's own words, preserved verbatim
  question_type: QuestionType # CONTEXT, MEANING, TECHNIQUE, HISTORY, COMPARISON
  audience: Audience         # LEARNER (MVP), STUDENT, TEACHER
  thesis: str                # one-sentence answer; the claim that must be defensible
  blocks: list[Block]        # ordered document body
  source_ids: list[str]      # every source cited anywhere in the document
  conflict_ids: list[str]
  gap_ids: list[str]
  timeline: list[TimelineEvent]
  attribution: list[Attribution]  # §3.4 — travels with the document
  sufficiency: Sufficiency   # SUFFICIENT, THIN, INSUFFICIENT (§10.6)
  generated_at: datetime
  model: str                 # model + prompt + assembler version, for reproducibility

Block:                       # one paragraph of the answer
  id: str
  kind: BlockKind            # THESIS, CLOSE_READING, CONTEXT, QUOTE, CONFLICT, GAP, CLOSE
  text: str                  # rendered prose (rights-gated at render time)
  claim_ids: list[str]       # every assertion maps to >= 1 claim
  deepening: Deepening?      # generated on demand when the user expands this block

Claim:                       # an assertion tied to evidence
  id: str
  text: str
  source_ids: list[str]      # >= 1 unless stance == INFERENCE
  spans: list[Span]          # char offsets into source.text that support the claim
  stance: Stance             # SUPPORTS, DISPUTES, INFERENCE, CITATION_ONLY
  extractor_confidence: float  # §4.4, separate from the claim's truth

Deepening:                   # what "go deeper" expands into, inline
  block_id: str
  quotes: list[Quote]        # verbatim source text, never paraphrase; rights-gated
  context: list[Source]      # the world/artist/situation material behind the block
  conflicts: list[Conflict]
  gaps: list[Gap]
```

`CITATION_ONLY` exists for sources we may reference but not reproduce (lyrics, paywalled
articles). It renders as a citation with no quote and is never counted as having a span.

### 3.7 Answer Discipline (non-negotiable)

These rules are the product. Violating them makes the tool a summary generator.

1. **No claim without a source.** If it is not in a retrieved document, it is not in
   the answer. Model memory is not a source.
2. **Every block resolves to a claim.** A block whose claims are empty fails assembly;
   it does not render with a softer tone.
3. **Quote what is contested, paraphrase what is settled.** Metronome marks, lyric
   variants, disputed dates, translations — verbatim, with enough span to verify.
4. **Mark inference as inference.** `stance: INFERENCE` renders visibly as *"no
   source states this directly"*. It is allowed; pretending it is sourced is not.
5. **Gaps are output.** "No letters survive from 1822" belongs in the document. An
   honest unknown is why a teacher or a reader can trust the rest.
6. **Conflicts are output.** Contradicting sources are shown side by side, not
   averaged into a confident middle.
7. **Relevance beats completeness.** Context that does not explain *this* work, for
   *this* question, is left in the pack and out of the document.
8. **Rights outrank completeness.** A better quote we cannot show becomes a citation,
   not a copyright violation.
9. **Dates keep their precision.** "c. 1821" is not "1821"; an undated event is not
   placed at the work's date (§3.2).

### 3.8 Social Seams (design now, build v0.2)

- Every answer has a permalink → the collected questions become the community.
- The single social primitive is **"add context" / "this is wrong"** on a block: a
  correction, not a like. It serves truth-seeking and produces the social graph.
- Follow-ups create child answers, so a conversation is a tree of citable documents.
- Teacher mode (class questions → curated answer) is parked; nothing in MVP blocks it.

---

## 4. Retrieval Pipeline

### 4.0 Question Interpretation
Input: a question, not a keyword string.
→ Classify `QuestionType` (CONTEXT / MEANING / TECHNIQUE / HISTORY / COMPARISON)
→ Identify the work and the aspect in question
→ Set the evidence priorities (a MEANING question leads with close reading and primary
  sources; a HISTORY question leads with timeline and reception)

A learner's question is often imprecise ("why is the ending so weird?"). The aspect
is extracted first, because it decides what gets retrieved and what the answer may
safely leave out.

### 4.1 Entity Resolution
Input: free-text query ("Kendrick Lamar To Pimp a Butterfly", "Beethoven Op. 111", "Rimbaud Le Bateau Ivre")
→ Normalize → Disambiguate → Link to knowledge graph (Wikidata, MusicBrainz, Genius, Poetry Foundation)

Resolution returns **candidates with confidence**, not a single answer. Ambiguous input
("Beethoven Op. 111" vs. sonata no. 32) must surface a choice; guessing silently is the
most expensive failure in the pipeline.

### 4.2 Source Gathering (Parallel, rights-gated at ingest)

| Source Type | Classical Music | Rap/Hip-Hop | Poetry | Visual Art |
|-------------|-----------------|-------------|--------|------------|
| **Primary** | Letters, diaries, sketches, manuscripts (IMSLP, Beethoven-Haus, LOC) | Lyrics (Genius — link only), interviews, studio logs, producer notes | Manuscripts, letters, journals (author archives) | Sketchbooks, letters, studio photos |
| **Contemporary** | Newspaper reviews (AMZ, Neue Zeitschrift), premiere programs | Magazine reviews (The Source, XXL, Pitchfork), blog posts, forum threads | Literary journal reviews, letters to editor | Exhibition catalogs, critic reviews |
| **Scholarly** | Grove, MGG, JSTOR, RILM, musicology dissertations | Hip-hop studies journals, cultural studies, ethnomusicology | Literary criticism, PMLA, modern language studies | Art Bulletin, October, exhibition histories |
| **Reference** | Wikipedia, AllMusic, Discogs | Genius annotations, WhoSampled, Wikipedia | Poetry Foundation, Academy of American Poets | WikiArt, museum catalogues |

Every adapter must emit `RightsInfo` with its payload. An adapter that cannot state the
license returns `UNKNOWN`, which means the source is stored as metadata and rendered as
a link. Adapters do not get to decide display policy; §3.4 does.

### 4.3 Temporal Anchoring
Every source → two dates (§3.2.1): when the document was made, and what period it
describes. Events take `when` from the assertion, `date_asserted_by_document` from the
source, and both are kept. World events (wars, laws, tech shifts) are overlaid from
Wikipedia/DBpedia, dated with the same `Temporal` model.

### 4.4 Claim Extraction (the input to conflict detection)

Conflicts cannot be detected over prose. They are detected over **extracted claims**,
and a claim without a span is not usable.

```python
ExtractedClaim:
  id: str
  work_id: str
  source_id: str
  span: Span                  # char offsets into source.text — mandatory
  subject: str                # canonical key: "op111.mov2.metronome"
  predicate: str              # "metronome_mark"
  value: JSON                 # normalized: {"beat_unit": "quarter", "bpm": 50}
  value_text: str             # as written: "♩ = 50"
  polarity: Polarity          # ASSERT, DENY
  about: Temporal?            # the period the claim describes
  qualifiers: dict            # {"attribution": "Schindler", "hedge": "allegedly"}
  extractor_confidence: float # 0-1, how sure we are we read the source correctly
```

Extraction steps: LLM proposes (subject, predicate, value, span) → the span is checked
against the stored text → the value is normalized (units, tempo, dates, spelling,
transliteration) → the claim is stored. A claim whose span does not match its source
text is dropped, not repaired.

`subject` and `predicate` are controlled vocabulary seeded in `data/seed/`. Free-form
triples produce conflicts that are really just different wordings, which is the main
source of false positives (§4.5.4).

### 4.5 Conflict Detection (gap: precision, not recall)

Disagreement is the differentiator and the easiest thing to get wrong. Two sources
saying different things about different things is noise; a false conflict card destroys
trust faster than a missing one.

#### 4.5.1 Conflict types

| Type | Definition | Example | Rendered as |
|---|---|---|---|
| `FACTUAL` | same subject+predicate, incompatible values | metronome ♩=50 vs ♩=72 | conflict |
| `CHRONOLOGICAL` | same event, datings that do not overlap (§3.2) | composed 1821 vs 1822 | conflict |
| `ATTRIBUTIONAL` | same statement, different attributed source | "Beethoven said" vs "Schindler said Beethoven said" | conflict |
| `TEXTUAL` | different text of the work itself | lyric version, edition, translation | conflict |
| `INTERPRETIVE` | different readings of the same evidence | "ironic" vs "sincere" | **not a conflict** — labeled *readings differ* |
| `NULL` | one source is silent | no letters from 1822 | gap, not conflict |

Silence is never a conflict. Absence of evidence is a `Gap`. Two interpretations that are
both defensible are `INTERPRETIVE` and are presented as a pair of readings, not an error.

#### 4.5.2 Detection pipeline

1. **Blocking** — group extracted claims by `(subject, predicate)`. Nothing outside a
   block is ever compared.
2. **Normalization** — units, date precision, name forms, transliteration. If values are
   equal after normalization, there is no conflict.
3. **Incompatibility test** — values differ, and the difference is not explainable by
   qualifier, scope, or time. Checked in that order, because most apparent conflicts are
   actually different scopes.
4. **Adjudication** — classify into §4.5.1, produce `severity` and `confidence`.
5. **Threshold** — a `Conflict` reaches the document only if all hold:
   - ≥ 2 claims, each with `extractor_confidence ≥ 0.80`
   - each claim has a verified `span`
   - adjudicator confidence ≥ 0.70
   - at least one claim is not from the same document as the other (self-contradiction
     inside one document is real but rarer; it goes to the review queue first)
6. **Everything below threshold** becomes a `ConflictCandidate` in the review queue. It
   is visible to curators, never to readers.

#### 4.5.3 Conflict record

```python
Conflict:
  id: str
  kind: ConflictKind            # FACTUAL, CHRONOLOGICAL, ATTRIBUTIONAL, TEXTUAL
  subject: str
  predicate: str
  positions: list[Position]     # >= 2, each with claim + source + span + value
  question: str                 # the reader-facing framing: "Metronome mark for Op. 111/II"
  resolution: Resolution        # UNRESOLVED (default), TIER_PREFERRED, CONSENSUS
  preferred_position_id: str?   # set only when resolution != UNRESOLVED, and shown as
                                # a preference, never as a verdict
  severity: Severity            # MATERIAL (changes interpretation), MINOR (detail)
  confidence: float
  detected_at: datetime
  detector: str                 # rules-v1 | llm-v1 — for evaluation attribution
```

MVP ships `UNRESOLVED` for everything. Tier is used to *order* positions (primary first),
never to delete one. `TIER_PREFERRED` is a v0.2 feature and requires evaluation evidence
that it improves reader trust.

#### 4.5.4 False-positive controls

- Controlled `subject`/`predicate` vocabulary (§4.4) — free-form triples are the main
  source of noise.
- Qualifier awareness: "allegedly", "according to X", "in some editions" usually means
  two claims scoped differently, not a contradiction.
- Time awareness: claims about 1821 and 1822 are not contradictory if the fact changed.
- Same-document check: a source contradicting itself is flagged for review, not rendered.
- Recall budget: target precision 0.90 (hide rather than cry wolf), recall 0.60
  (measured in §10). A missed conflict is a disappointment; a fake one is a lie.

### 4.6 Ranking

Authority tier dominates, then question relevance, then recency, then rights
(showable before unshowable). The score is computed, never stored on the source:

```python
score = 0.50 * tier_weight     # PRIMARY 1.0, SCHOLARLY 0.8, REFERENCE 0.6,
                               # CONTEMPORARY_REVIEW 0.5
      + 0.30 * relevance       # §4.0 question relevance + §4.4 claim overlap
      + 0.10 * recency         # documents closer in time to the work rank higher
      + 0.10 * displayable     # 1.0 if the rights gate allows text, 0.0 if link only
```

Weights live in config and are tuned against the golden set (§10), not by taste.

### 4.7 Answer Assembly
Input: question + work + top 50 sources (ranked by §4.6) + claims + conflicts + timeline
→ `Answer` document.

Two implementations, same interface:
- **Deterministic assembler (first).** Builds the document from ranked evidence with
  templates: thesis from the strongest supported claim, blocks by block kind, citations
  straight from spans. No model, no keys, fully testable offline.
- **LLM assembler.** Same output contract, filled by Hermes/Nemotron. It may only write
  prose over evidence that survives the claim → span check; anything unsupported is
  dropped before rendering.

The deterministic path ships first because it makes the citation contract testable and
gives the LLM a baseline to be measured against — not because it writes better prose.

---

## 5. MVP Data Sources and Rights Matrix

| Domain | Sources | Access | License / status | MVP display |
|---|---|---|---|---|
| **General** | Wikipedia | API | CC BY-SA 4.0 | EXCERPT + attribution |
| **General** | Wikidata | API | CC0 | METADATA_ONLY (facts) |
| **General** | DBpedia | API | CC BY-SA | METADATA_ONLY |
| **General** | Internet Archive | API | per item | per item; default LINK_ONLY |
| **Classical** | IMSLP | scrape | per file (mostly PD) | EXCERPT for PD scores; LINK_ONLY otherwise |
| **Classical** | Beethoven-Haus digital archive | scrape | per item, often restricted | LINK_ONLY |
| **Classical** | Open Opus | API | CC0 | METADATA_ONLY |
| **Classical** | MusicBrainz | API | CC0 / CC BY-NC-SA data | METADATA_ONLY |
| **Rap/Hip-Hop** | Genius (lyrics) | API | licensed, not redistributable | LINK_ONLY — never stored |
| **Rap/Hip-Hop** | Genius (annotations) | API | user-generated, platform terms | LINK_ONLY |
| **Rap/Hip-Hop** | WhoSampled, Discogs | scrape / API | restrictive | LINK_ONLY |
| **Poetry** | Poetry Foundation | scrape | copyrighted | LINK_ONLY |
| **Poetry** | Poets.org | API | per poem | LINK_ONLY unless PD |
| **Poetry** | Project Gutenberg, Wikisource | API / dump | PD / CC | FULL_TEXT or EXCERPT |
| **Scholarly** | Semantic Scholar | API | metadata open, abstracts per publisher | METADATA_ONLY + abstract snippet |
| **Scholarly** | DOAJ, arXiv | API | CC BY variants | EXCERPT with attribution |
| **Scholarly** | JSTOR / Grove / RILM | — | paywalled, no API rights | out of MVP; links only |

**Rights reality check.** "Public" in the Access column means *retrievable*, not
*redistributable*. The rows that make classical packs excellent — Grove, MGG, JSTOR,
RILM — are exactly the rows we cannot retrieve without a partnership, and the roadmap
should stop pretending otherwise. The realistic MVP corpus is Wikimedia + IMSLP + open
archives: enough to prove the loop, thin enough that depth (one work done well) beats
breadth (three works done shallowly).

---

## 6. API Design (FastAPI)

```python
# GET /api/v1/works/search?q=beethoven+op+111&type=music
# → list[WorkCandidate]        # ranked, with confidence

# POST /api/v1/answers        { question, work_id?, parent_id? }
# → Answer                    # the document; synchronous for MVP

# GET /api/v1/answers/{answer_id}
# → Answer                    # includes sufficiency + attribution

# GET /api/v1/answers/{answer_id}/blocks/{block_id}/deepening
# → Deepening                 # on-demand inline expansion, rights-gated

# GET /api/v1/works/{work_id}/context
# → ContextPack               # the evidence layer, for inspection

# GET /api/v1/works/{work_id}/timeline
# → list[TimelineEvent]       # includes Temporal, never bare dates

# GET /api/v1/works/{work_id}/sources?type=primary&limit=20
# → list[Source]              # rights in the payload, text omitted if not allowed

# GET /api/v1/works/{work_id}/conflicts
# → list[Conflict]            # positions + spans

# POST /api/v1/annotations (private, stored locally or user DB)
# → Annotation

# POST /api/v1/answers/{answer_id}/feedback  { block_id, kind: ADD_CONTEXT|THIS_IS_WRONG }
# → Feedback                 # social seam, v0.2; recorded from MVP

# GET /api/v1/export/{answer_id}?format=markdown|json|pdf
# → file download            # attribution block mandatory; rights applied per source
```

**Export contract.** Every export contains an attribution section generated from
`Answer.attribution`: source title, author, date, license, URL, and any
`attribution_text` the license demands. An export whose sources are `LINK_ONLY` carries
titles and URLs, not text. Markdown and JSON ship first; PDF waits until the attribution
format is settled (§11).

---

## 7. UI Specification (Reader View)

### 7.1 Layout
The document is the page. Context is a rail beside it and an expansion inside it —
never a parallel tab you have to go looking for.

```
┌──────────────────────────────────────────────────────────────────────┐
│  WORK: Title • Creator • Date • Type               [Export] [Ask more]│
├───────────────────────────────────┬──────────────────────────────────┤
│  ANSWER DOCUMENT                  │  CONTEXT RAIL (sticky)           │
│                                   │                                  │
│  Q: "why does the Arietta sound   │  TIMELINE                        │
│      so plain?"                   │   c. 1798 — first sketches       │
│                                   │   1821 — composed                │
│  ┌ thesis ───────────────────────┐│   1822 — published               │
│  │ "He meant it to be heard as   ││   ████████ work period           │
│  │  an ending, not a showpiece." ││   — undated — premiere date lost │
│  │                      ¹²       ││                                  │
│  └───────────────────────────────┘│  WORLD                           │
│                                   │   Vienna after the Congress      │
│  ¶ Paragraph, prose, inline       │                                  │
│    citation pins ¹².              │  OPEN QUESTIONS                  │
│    [ go deeper ⌄ ]                │   • no letters from 1822         │
│                                                                      │
│  ¶ … paragraph, expanded inline:  │                                  │
│    ┌ source quote, dated c. 1791 ┐│                                  │
│    │ ⚠ conflicting source      │ │                                  │
│    └───────────────────────────┘ │                                  │
└───────────────────────────────────┴──────────────────────────────────┘
```

### 7.2 Source Card (on expansion)
```
[PRIMARY]  Beethoven to Schlesinger, 1822-01-15  (Vienna)
           "The last sonata is not a sonata..."
           [View full] [Cite] [Annotate]  ↑ PRIMARY
```
When rights are `LINK_ONLY` or `METADATA_ONLY`, the card shows title/author/date and a
**"read at source"** link where the quote would be, with no text and no paraphrase
presented as quotation. When the date is imprecise, it renders exactly as stored:
`c. 1791`, `1821?`, `1820s`.

### 7.3 Claim Provenance (hover / focus on a citation pin)
```
¹ Sketchbook BH 115, p. 23 (c. 1791) — PRIMARY, sketch
   "…" (verified span)
↳ Kinderman 2006, p. 118 — SCHOLARLY, supports
   Claim: the Arietta theme derives from a 1791 sketch
   First attested: 2006 (the sketch is 1791)
   Why it matters: 30-year gestation; the simplicity is deliberate
```

### 7.4 Conflict Card
```
⚠ CONFLICT (MATERIAL): Metronome mark for Op. 111/II
   • Autograph manuscript (c. 1821): ♩=72            [PRIMARY]
   • Schindler (1840): ♩=50 "as Beethoven wanted"    [SECONDARY]
   • Modern editions: split
   → Unresolved. [Listen at 50] [Listen at 72] [Read the evidence]
```
`INTERPRETIVE` disagreements render as *readings differ* (a paired list), not with a
warning icon. Undated or imprecise positions show their precision; no card invents a day.

### 7.5 Inline Deepening
"Go deeper" expands in place: the verbatim quote, its date (with precision) and place, the
conflicting source if the claim is contested, and the gap if the block rests on an
inference. It never navigates away, never replaces the document, and never lengthens the
page for a reader who did not ask.

### 7.6 Insufficient evidence
When `Answer.sufficiency != SUFFICIENT` the document opens with what is missing and what
*is* known, never with manufactured prose:
```
We can't answer this well yet.
  What we found: 2 sources (below our threshold of 3)
  What's missing: no contemporaneous account of the metronome mark
  What we do know: the autograph gives ♩=72 [view]
```
This state is a feature and is evaluated (§10.6), not an error path.

---

## 8. Technical Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| **API** | FastAPI + Pydantic v2 | Type-safe, fast, async |
| **DB** | PostgreSQL + pgvector | Relational + embeddings in one |
| **Queue** | Redis + BullMQ (or Celery) | Background retrieval jobs |
| **Embeddings** | sentence-transformers (local) or NVIDIA NIM | Cost control |
| **LLM** | Hermes (local) or NVIDIA Nemotron | Your infra |
| **Frontend** | Next.js 14 + React 18 + Tailwind | Your stack |
| **State** | TanStack Query + Zustand | Server + client state |
| **Deploy** | Docker Compose → single VPS | Simple, portable |

**MVP note (not a decision yet):** weeks 1–2 do not need pgvector, Redis, Celery, or
embeddings. Twenty sources per work rank fine with BM25 + authority tiers, and one
question → one document fits in a request. Add each when a measurement demands it, not
before. The reader view is a document and can be server-rendered; TanStack Query and
Zustand become worth their weight when annotations sync.

---

## 9. MVP Milestones

| Week | Goal | Exit Criteria |
|------|------|---------------|
| **1** | Question in → work resolved; golden set defined | `POST /answers` resolves 3 real questions to works, returning candidates when ambiguous. 12 golden questions written with expected claims and `must_not_claim` entries. |
| **2** | Evidence gathering, temporal anchoring, rights gate | One question → 20+ dated sources → timeline rail populated with precision preserved; every source carries a `RightsInfo`; rights gate tested. Automated evaluation harness runs offline on fixtures. |
| **3** | Claims, conflicts, answer assembly | 3 documents generated; every non-inference block resolves to a claim with a verified span; conflict precision ≥ 0.90 on the golden set; gaps surfaced. |
| **4** | Inline deepening + export | Any block expands to its evidence; answer exports to markdown/JSON with attribution; 0 rights violations in the export test. |

Milestone discipline: a week is not done because the endpoint responds. It is done when
the document would survive a reader clicking every citation, and the evaluation suite
says so.

---

## 10. Evaluation (gap: there is currently no bar)

Without this section, "the pack is good" is unfalsifiable and every prompt, ranker, or
threshold change is vibes. Evaluation is a deliverable, not a chore: it lands in week 1
and gates week 3 onward.

### 10.1 The golden set

Twelve questions across three works (four each), written by a human, stored in
`data/golden/`. Each entry is a **contract**, not a reference answer:

```yaml
- id: beethoven-op111-arietta-simplicity
  work: beethoven-op111           # Sonata No. 32 in C minor, Op. 111; QID resolved at ingest
  question: "Why does the second movement's theme sound so plain?"
  question_type: MEANING
  must_cite:                      # evidence that MUST appear somewhere in the document
    - sketch-bh115#p23
    - kinderman-2006#p118
    - beethoven-letter-1822-01-15
  required_claims:                # semantic match by a judge, not string match
    - the Arietta theme derives from a sketch of c. 1791
    - the simplicity is deliberate rather than a failure of invention
  must_not_claim:                 # common hallucinations; any hit fails the run
    - the theme was composed in a single sitting in 1821
    - Beethoven dedicated the sonata to his nephew Karl
  expected_conflicts:
    - subject: op111.mov2.metronome
      min_positions: 2
  expected_gaps:
    - no surviving letter explains the ending of the variations
  min_independent_sources: 5
```

Fixtures are recorded HTTP responses (with their retrieval timestamps) so the suite runs
offline and deterministically. Re-recording fixtures is a deliberate, reviewed act.

### 10.2 Metrics

| # | Metric | Definition | Gate |
|---|--------|-----------|------|
| 1 | **Citation coverage** | % of blocks with ≥1 claim whose span verifies against stored text | 100% |
| 2 | **Unsupported claim rate** | % claims with no verified span or no source | 0% |
| 3 | **Evidence recall** | % of `must_cite` sources retrieved *and* cited | ≥ 70% |
| 4 | **Required claim recall** | % of `required_claims` present (judged semantically) | ≥ 80% |
| 5 | **Hallucination rate** | % runs containing any `must_not_claim` | 0% |
| 6 | **Conflict precision** | true conflicts / all conflicts shown | ≥ 0.90 |
| 7 | **Conflict recall** | found / `expected_conflicts` | ≥ 0.60 |
| 8 | **Temporal fidelity** | % timeline dates whose precision and bounds match the source | 100% |
| 9 | **Rights violations** | rendered text exceeding the source's display policy | 0 |
| 10 | **Refusal correctness** | insufficient-evidence questions that produce prose instead of `INSUFFICIENT` | 0 |
| 11 | **Relevance** (human) | 1–5: does the document answer *this* question from *this* evidence | ≥ 4.0 |
| 12 | **Cost / latency** | tokens and wall time per answer | tracked, no gate |

Metrics 1, 2, 5, 8, 9, 10 are hard gates: a change that breaks one is reverted, not
tuned. Metrics 3, 4, 6, 7 are quality targets that improve over time.

### 10.3 Human evaluation

Weekly, 12 answers × 2 blind raters, 5-point rubric:

| Score | Anchor |
|---|---|
| 5 | Answers the question, every step traceable, teaches something a knowledgeable reader didn't know |
| 4 | Answers the question, citations hold, one weak or over-general paragraph |
| 3 | Partially answers, defensible but reads like a good Wikipedia section |
| 2 | Answers around the question; context present but not tied to the question |
| 1 | Summary, unsupported, or wrong about the work |

Raters score the document without seeing the model or prompt version. Disagreement > 1
point triggers a third rating; the set is re-examined, not averaged.

### 10.4 Baselines and regression gates

- **Baseline A:** the Wikipedia article's lead section, trimmed. If the document cannot
  beat this on relevance, the product has no reason to exist.
- **Baseline B:** the deterministic assembler. The LLM must beat B on relevance (11)
  *without* losing gates 1, 2, 5.
- **Regression gate:** no merge may lower any automated metric. Human scores are
  recorded per release; a drop ≥ 0.3 in mean relevance blocks release.

### 10.5 Conflict and rights test fixtures

Both gaps get adversarial fixtures, because both fail silently:

- **Conflict fixtures:** same fact in different units, "allegedly" framings, two scopes
  that look incompatible, a source contradicting itself, a translation difference
  misread as a factual one. Expected label for each: conflict / not a conflict.
- **Rights fixtures:** a source marked PD-US whose span is longer than the excerpt
  limit; a lyrics source with `storage_allowed=False`; a link-only source cited in an
  export. Every one must render without text and without error.

### 10.6 Sufficiency threshold (refusal policy)

Refuse before writing prose when either holds:
- fewer than **3 independent sources** (independent = different documents, not different
  mirrors of one), or
- no source with `authority >= SCHOLARLY` **and** a verified span.

The refusal is an `Answer` with `sufficiency: INSUFFICIENT`, the evidence found, and the
gaps — never an apology, never a guess. It is scored (metric 10), because a system that
never refuses is a system that cannot tell you what it doesn't know.

### 10.7 What evaluation does not cover (yet)

Pronunciation, musical sound, and aesthetic judgment. The engine explains context; it
does not play the piece. Say so, and do not let an answer imply otherwise.

---

## 11. Open Questions

**Resolved (MVP):**
- Authority weighting: hardcoded tiers, score computed at rank time (§4.6).
- Contradiction presentation: show all sides, `UNRESOLVED`; tier orders, never deletes
  (§4.5.3).
- Answer shape: document with inline deepening (§2).
- User #1: curious self-learner (§2).
- Temporal representation: bounded intervals with declared precision (§3.2).
- Evaluation: golden set + metric gates (§10).
- Paywall/bad-rights sources: citation and link only, no text (§3.4).

**Open:**
1. **How much is enough?** Target length for an answer — 400–800 words plus expansion?
   The sufficiency *threshold* is fixed (§10.6); the target *length* is not.
2. **Identification from vague input.** A self-learner may not know the opus number
   ("the sad piano piece from that film"). Name matching will not save this; decide
   whether MVP asks the user to disambiguate from candidates or accepts description.
3. **Depth vs. breadth.** Evidence is thin outside Wikimedia + open archives. Is one
   deeply-covered work better than three shallow ones for the first release?
4. **Multilingual.** Answer language follows the question; source language is preserved
   and quoted, not translated away. Does a translation count as a `TEXTUAL` conflict
   or as a separate source?
5. **Attribution of the engine.** How is the tool cited in exports versus the original
   sources? Needed before PDF export ships.
6. **`TIER_PREFERRED` conflicts.** Does preferring the primary source improve reader
   trust, or hide a genuine puzzle? Answer with evaluation data, not opinion.

---

## 12. Immediate Next Steps

1. **Write 3 real questions** (not 3 works): Beethoven Op. 111, Kendrick "To Pimp a
   Butterfly", Rimbaud "Le Bateau Ivre" — the question decides the evidence.
2. **Write the golden set** (§10.1): 12 entries, including `must_not_claim` and the
   two adversarial fixture families. This is week 1 work, not week 4.
3. **Model the document and time** — `Answer`, `Block`, `Claim`, `Span`, `Temporal`,
   `RightsInfo`. Nothing else works if dates and citations are loose strings.
4. **Implement the rights gate** (`src/rights.py`) with its fixtures before the first
   adapter, so no adapter can bypass it later.
5. **Build adapter interfaces** — `SourceAdapter` ABC with `search()`, `fetch()`,
   `normalize()`, `rights()`.
6. **Implement Wikipedia + Wikidata adapters**, then a **deterministic assembler** so
   the citation contract is testable offline.
7. **Claim extraction + conflict detector**, tuned against the adversarial fixtures,
   before Genius + IMSLP are added.

---

## 13. File Structure

```
art-context-engine/
├── specs/
│   └── SPEC.md                 # This file
├── src/
│   ├── api/                    # FastAPI routes
│   │   ├── routes/
│   │   │   ├── answers.py      # POST /answers, GET, deepening, feedback
│   │   │   ├── works.py
│   │   │   ├── context.py
│   │   │   ├── timeline.py
│   │   │   ├── sources.py
│   │   │   ├── conflicts.py
│   │   │   ├── annotations.py
│   │   │   └── export.py
│   │   ├── models/             # Pydantic models (Work, Source, ContextPack, etc.)
│   │   └── deps.py             # DB, LLM, queue dependencies
│   ├── workers/
│   │   ├── retrieval.py        # Orchestrates parallel source gathering
│   │   ├── synthesis.py        # LLM answer generation
│   │   └── embeddings.py       # Vector embedding jobs
│   ├── rights.py               # RightsInfo + the single render/export gate (§3.4)
│   ├── temporal.py             # Temporal, precision, parsing, sorting (§3.2)
│   ├── retrieval/
│   │   ├── adapters/
│   │   │   ├── base.py         # SourceAdapter ABC
│   │   │   ├── wikipedia.py
│   │   │   ├── wikidata.py
│   │   │   ├── genius.py
│   │   │   ├── imslp.py
│   │   │   ├── poetry_foundation.py
│   │   │   └── semantic_scholar.py
│   │   ├── entity_resolver.py  # Query → WorkCandidate[]
│   │   ├── temporal_anchor.py  # document date vs. asserted date (§3.2.1)
│   │   ├── claim_extractor.py  # Source → ExtractedClaim[] with spans (§4.4)
│   │   ├── conflict_detector.py# ExtractedClaim[] → Conflict[] (§4.5)
│   │   └── ranker.py           # §4.6 scoring
│   ├── answer/                 # the document layer (the product)
│   │   ├── assemble.py         # evidence → Answer (deterministic first, LLM later)
│   │   ├── claims.py           # claim → span verification, CITATION_ONLY handling
│   │   ├── deepen.py           # on-demand block expansion
│   │   └── render.py           # markdown/JSON export + attribution
│   ├── models/
│   │   └── database.py         # SQLAlchemy models + migrations
│   └── ui/                     # Next.js app (separate repo eventually)
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── evaluation/             # harness, judges, scoring reports (§10)
│   └── fixtures/
│       ├── http/               # recorded responses, offline
│       ├── conflicts/          # adversarial conflict fixtures (§10.5)
│       └── rights/             # adversarial rights fixtures (§10.5)
├── data/
│   ├── seed/                   # eras, genres, controlled subject/predicate vocab
│   └── golden/                 # the 12 golden questions (§10.1)
└── docs/
    └── architecture.md
```

---

*End of SPEC v0.3.0*

### Changelog

- **0.3.0** — Fixed the four gaps. Added the bounded-interval temporal model with declared
  precision and the document-date/asserted-date split (§3.2, §3.2.1, §4.3); a rights model
  with an enforcement gate, fail-closed defaults, and a per-source rights matrix (§3.4,
  §5, §7.2); claim extraction and a typed conflict detector with thresholds, false-positive
  controls and an explicit precision-over-recall stance (§4.4, §4.5); and an evaluation
  section with a golden set, twelve metrics, hard gates, human rubric, baselines and a
  sufficiency/refusal policy (§10). Milestones now gate on evaluation (§9).
- **0.2.0** — Repositioned the product around the question: `Answer`/`Block`/`Claim`
  (§3.4), answer discipline (§3.5), social seams (§3.6), document-with-inline-deepening
  reader (§7), per-answer export, question-aware retrieval (§4.0), milestones re-cut
  around question → evidence → document.
- **0.1.0** — Initial MVP spec.
