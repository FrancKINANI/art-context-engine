"""The reader view (§7). Server-rendered HTML, no build step.

The document is the page; the context rail sits beside it and the deepening
expands inline. Nothing here is a framework: the MVP needs one view, and a
template engine would be more code than the view.

Two rendering rules from the spec are enforced in code:

* citation pins are numbers from `render.citation_index`, so a footnote in the
  text always matches the attribution list;
* a source card with `LINK_ONLY`/`METADATA_ONLY` shows *"read at source"* where a
  quote would be — never text, never a paraphrase presented as a quotation
  (§7.2).
"""

from __future__ import annotations

import html
from datetime import datetime

from src import rights
from src.answer.render import citation_index
from src.models import (
    Answer,
    Block,
    BlockKind,
    Conflict,
    ConflictKind,
    ContextPack,
    Deepening,
    Gap,
    Source,
    Sufficiency,
    TimelineEvent,
)
from src.temporal import describe

CSS = """
:root {
  --bg: #fbfaf7; --fg: #1b1a17; --muted: #6b675f; --line: #e2ded4;
  --card: #fff; --accent: #7a4b2a; --warn: #a1431f; --ok: #2f6b4f;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#141311; --fg:#ece8e1; --muted:#9a958c; --line:#2c2a26;
          --card:#1c1a17; --accent:#d8a077; --warn:#e08a5f; --ok:#7fc0a0; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font: 17px/1.65 ui-serif, Georgia, "Iowan Old Style", serif; }
a { color: var(--accent); }
header.top { border-bottom:1px solid var(--line); padding:18px 28px;
  display:flex; gap:16px; align-items:baseline; flex-wrap:wrap; }
header.top h1 { font-size:15px; margin:0; letter-spacing:.04em; text-transform:uppercase;
  color:var(--muted); font-weight:600; }
main { display:grid; grid-template-columns: minmax(0,1fr) 320px; gap:48px;
  max-width:1180px; margin:0 auto; padding:36px 28px 96px; }
@media (max-width: 900px) { main { grid-template-columns: 1fr; } aside { order:-1; } }
h2 { font-size:26px; line-height:1.25; margin:0 0 6px; font-weight:600; }
.question { color:var(--muted); font-size:15px; margin-bottom:28px; }
.thesis { font-size:20px; line-height:1.5; border-left:3px solid var(--accent);
  padding:6px 0 6px 18px; margin:0 0 26px; }
.block { margin:0 0 22px; }
.pins { font-size:11px; vertical-align:super; margin-left:3px; color:var(--accent);
  text-decoration:none; }
.pins a { margin-right:2px; }
blockquote { margin:0 0 10px; padding:12px 16px; background:var(--card);
  border-left:3px solid var(--line); font-style:italic; white-space:pre-wrap; }
blockquote .src { display:block; font-style:normal; font-size:13px;
  color:var(--muted); margin-top:10px; }
.quote-note { font-size:13px; color:var(--muted); margin:0 0 20px; }
.conflict { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:16px 18px; margin:0 0 22px; }
.conflict h3 { font-size:14px; margin:0 0 10px; letter-spacing:.03em;
  text-transform:uppercase; color:var(--warn); }
.conflict.readings h3 { color:var(--muted); }
.conflict ul { list-style:none; margin:0; padding:0; }
.conflict li { margin:0 0 10px; font-size:15px; }
.conflict .label { font-size:11px; text-transform:uppercase; color:var(--muted);
  margin-left:6px; letter-spacing:.04em; }
.conflict .span { color:var(--muted); font-style:italic; display:block; margin-top:3px; }
.verdict { font-size:13px; color:var(--muted); border-top:1px solid var(--line);
  padding-top:8px; margin-top:6px; }
.gaps { background:var(--card); border:1px dashed var(--line); border-radius:10px;
  padding:16px 18px; margin:0 0 22px; }
.gaps h3 { font-size:13px; text-transform:uppercase; letter-spacing:.05em;
  color:var(--muted); margin:0 0 8px; }
.gaps ul { margin:0; padding-left:18px; font-size:15px; }
button.deepen { background:none; border:1px solid var(--line); color:var(--muted);
  font:inherit; font-size:12px; padding:4px 10px; border-radius:99px; cursor:pointer; }
button.deepen:hover { color:var(--accent); border-color:var(--accent); }
.deepening { margin-top:14px; border-left:2px solid var(--line); padding-left:18px; }
.deepening .meta { font-size:12px; color:var(--muted); text-transform:uppercase;
  letter-spacing:.04em; margin-bottom:6px; }
aside { font-size:14px; }
aside section { margin-bottom:30px; }
aside h3 { font-size:12px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--muted); margin:0 0 10px; }
.timeline { list-style:none; margin:0; padding:0; border-left:1px solid var(--line); }
.timeline li { margin:0 0 12px; padding-left:14px; position:relative; }
.timeline li::before { content:""; position:absolute; left:-4px; top:7px; width:7px;
  height:7px; border-radius:50%; background:var(--accent); }
.timeline .when { display:block; font-size:12px; color:var(--muted); }
.timeline .undated::before { background:var(--muted); }
.sufficiency { font-size:13px; color:var(--muted); border:1px solid var(--line);
  border-radius:8px; padding:10px 12px; margin-bottom:24px; }
.sufficiency.insufficient { border-color:var(--warn); color:var(--warn); }
.attribution { margin-top:42px; border-top:1px solid var(--line); padding-top:18px;
  font-size:13px; color:var(--muted); }
.attribution li { margin-bottom:8px; }
.attribution .credit { display:block; opacity:.85; }
footer { padding:0 28px 40px; color:var(--muted); font-size:12px; text-align:center; }
form.ask { display:flex; gap:8px; margin:0 0 26px; }
form.ask input[type=text] { flex:1; padding:10px 12px; border:1px solid var(--line);
  border-radius:8px; background:var(--card); color:var(--fg); font:inherit; font-size:15px; }
form.ask button { padding:10px 16px; border-radius:8px; border:1px solid var(--accent);
  background:var(--accent); color:var(--bg); font:inherit; font-size:14px; cursor:pointer; }
.candidates { list-style:none; padding:0; }
.candidates li { border:1px solid var(--line); border-radius:10px; padding:14px 16px;
  margin-bottom:10px; background:var(--card); }
.candidates .why { font-size:12px; color:var(--muted); }
.price-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
  gap:20px; margin:28px 0; }
.plan { background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:22px 24px; }
.plan.featured { border-color:var(--accent); }
.plan h3 { margin:0 0 4px; font-size:16px; }
.plan .price { font-size:26px; margin:6px 0 14px; }
.plan ul { padding-left:18px; margin:0 0 16px; font-size:14px; color:var(--muted); }
.plan form { display:flex; gap:8px; }
.plan input { flex:1; padding:8px 10px; border:1px solid var(--line);
  border-radius:8px; background:var(--bg); color:var(--fg); font:inherit; }
.plan button { padding:8px 14px; border-radius:8px; border:1px solid var(--accent);
  background:var(--accent); color:var(--bg); font:inherit; cursor:pointer; }
.note { font-size:13px; color:var(--muted); }
"""

#: Served at /static/app.js. Kept out of the HTML so the Content-Security-Policy
#: can forbid inline script entirely — a source string that smuggles markup has
#: nowhere to execute.
SCRIPT = """
document.querySelectorAll('button.deepen').forEach(function (b) {
  b.addEventListener('click', async function () {
    var target = document.getElementById('deep-' + b.dataset.block);
    if (!target) return;
    if (target.dataset.open === '1') { target.innerHTML = ''; target.dataset.open = '0'; return; }
    var res = await fetch(b.dataset.url);
    target.innerHTML = await res.text();
    target.dataset.open = '1';
  });
});

var portal = document.getElementById('portal');
if (portal) {
  portal.addEventListener('submit', async function (e) {
    e.preventDefault();
    var res = await fetch('/api/v1/billing/portal', {method: 'POST'});
    var data = await res.json().catch(function () { return {}; });
    if (data.portal_url) window.location = data.portal_url;
  });
}
"""

HEAD = (
    '<link rel="stylesheet" href="/static/app.css">'
    '<script src="/static/app.js" defer></script>'
)


def stylesheet() -> str:
    return CSS


def esc(value: str | None) -> str:
    return html.escape(value or "")


def safe_url(url: str | None) -> str:
    """Only http(s) links are rendered.

    Source URLs come from third-party APIs, not from us. `javascript:` in an
    href is stored XSS with a click as the payload, so the scheme is checked
    rather than escaped.
    """
    candidate = (url or "").strip()
    if candidate.startswith(("http://", "https://")):
        return html.escape(candidate, quote=True)
    return ""



# ═══════════════════════════════════════════════════════════════════════════
# PAGE
# ═══════════════════════════════════════════════════════════════════════════

def page(
    answer: Answer,
    pack: ContextPack,
    sources: dict[str, Source],
    *,
    children: list[Answer] | None = None,
    ask_action: str = "/ask",
) -> str:
    index = citation_index(answer)
    title = answer.question
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — Art Context Engine</title>
<meta name="description" content="{esc(answer.thesis[:180])}">
{HEAD}
</head><body>
<header class="top">
  <h1>Art Context Engine</h1>
  <span class="question">{esc(answer.model)} · {answer.sufficiency.value.lower()}</span>
</header>
<main>
  <article>
    {_sufficiency_banner(answer)}
    <h2>{esc(answer.question)}</h2>
    <p class="question">{esc(_work_line(pack, sources))}</p>
    {''.join(_block_html(b, answer, index, pack, sources) for b in answer.blocks)}
    {_children_html(children or [])}
    {_attribution_html(answer, index)}
  </article>
  <aside>
    {_timeline_html(answer.timeline)}
    {_gaps_html(pack.gaps)}
    {_sources_html(sources, index, answer)}
    <section>
      <h3>Ask a follow-up</h3>
      <form class="ask" method="post" action="{ask_action}">
        <input type="hidden" name="parent_id" value="{answer.id}">
        <input type="hidden" name="work_id" value="{answer.work_id}">
        <input type="text" name="question" placeholder="Go deeper on…" required>
        <button type="submit">Ask</button>
      </form>
    </section>
  </aside>
</main>
<footer>Every sentence above is tied to a dated source. Gaps and disagreements are
shown, not smoothed over.</footer>
</body></html>"""


def _work_line(pack: ContextPack, sources: dict[str, Source]) -> str:
    primary = next((s for s in sources.values() if s.type.value == "PRIMARY"), None)
    count = len(sources)
    line = f"{count} sources"
    if primary is not None:
        line += f" · primary: {primary.title}"
    return line


def _sufficiency_banner(answer: Answer) -> str:
    if answer.sufficiency == Sufficiency.SUFFICIENT:
        return ""
    klass = "insufficient" if answer.sufficiency == Sufficiency.INSUFFICIENT else ""
    note = esc(answer.sufficiency_note or "")
    return f'<div class="sufficiency {klass}"><strong>{answer.sufficiency.value.title()}.</strong> {note}</div>'


# ═══════════════════════════════════════════════════════════════════════════
# BLOCKS
# ═══════════════════════════════════════════════════════════════════════════

def _pins(block: Block, index: dict[str, int]) -> str:
    nums = sorted({index[sid] for sid in block.source_ids if sid in index})
    if not nums:
        return ""
    links = "".join(f'<a href="#src-{n}">{n}</a>' for n in nums)
    return f'<span class="pins">{links}</span>'


def _block_html(
    block: Block, answer: Answer, index: dict[str, int],
    pack: ContextPack, sources: dict[str, Source],
) -> str:
    if block.kind == BlockKind.QUOTE:
        return (
            f'<div class="block"><blockquote>{esc(block.text)}</blockquote>'
            f'<p class="quote-note">Quoted verbatim {_pins(block, index)}</p></div>'
        )

    if block.kind == BlockKind.CONFLICT:
        cards = "".join(
            _conflict_html(c, sources)
            for c in pack.conflicts if c.id in block.conflict_ids
        )
        return f'<div class="block">{cards}</div>'

    if block.kind == BlockKind.GAP:
        items = "".join(
            f"<li>{esc(g.description)}</li>"
            for g in pack.gaps if g.id in block.gap_ids
        )
        return f'<div class="gaps"><h3>What is missing</h3><ul>{items}</ul></div>'

    klass = "thesis" if block.kind == BlockKind.THESIS else "block"
    body = f"<p class='{klass}'>{esc(block.text)}{_pins(block, index)}</p>" if klass == "thesis" \
        else f"<p>{esc(block.text)}{_pins(block, index)}</p>"
    deep = _deepen_button(block, answer)
    return f'<div class="block">{body}{deep}<div class="deepening" id="deep-{block.id}"></div></div>'


def _deepen_button(block: Block, answer: Answer) -> str:
    if not block.claim_ids and block.kind != BlockKind.QUOTE:
        return ""
    return (
        f'<button class="deepen" data-block="{block.id}" '
        f'data-url="/a/{answer.id}/b/{block.id}/deepen">go deeper ⌄</button>'
    )


def _conflict_html(conflict: Conflict, sources: dict[str, Source]) -> str:
    readings = conflict.kind == ConflictKind.INTERPRETIVE
    klass = "conflict readings" if readings else "conflict"
    header = (
        "Readings differ" if readings
        else f"Conflict · {conflict.severity.value.lower()} · {conflict.kind.value.lower()}"
    )
    items = []
    for position in conflict.positions:
        source = sources.get(position.source_id)
        shown = (
            rights.gate_span(source, position.span.start, position.span.end)
            if source else None
        )
        quote = f'<span class="span">“{esc(shown)}”</span>' if shown else ""
        items.append(
            f"<li><strong>{esc(position.value_text)}</strong>"
            f'<span class="label">{esc(position.authority.value)}</span>'
            f'<span class="span">{esc(position.source_title)} · {esc(_date_of(source))}</span>'
            f"{quote}</li>"
        )
    verdict = (
        "Unresolved. Both positions stand; neither is deleted."
        if conflict.resolution.value == "UNRESOLVED"
        else f"Preferred: {conflict.resolution.value}."
    )
    return (
        f'<div class="{klass}"><h3>{header}</h3>'
        f"<p>{esc(conflict.question.capitalize())}</p>"
        f'<ul>{"".join(items)}</ul>'
        f'<p class="verdict">{esc(verdict)}</p></div>'
    )


def _date_of(source: Source | None) -> str:
    if source is None or source.date_document is None:
        return "undated"
    return describe(source.date_document)


# ═══════════════════════════════════════════════════════════════════════════
# ASIDE
# ═══════════════════════════════════════════════════════════════════════════

def _timeline_html(events: list[TimelineEvent]) -> str:
    if not events:
        return ""
    rows = []
    for event in events:
        klass = "undated" if event.is_undated else ""
        when = event.when.display or "undated"
        detail = f"<em>{esc(event.description)}</em>" if event.description else ""
        rows.append(
            f'<li class="{klass}"><span class="when">{esc(when)}</span>'
            f"{esc(event.title)} {detail}</li>"
        )
    return f'<section><h3>Timeline</h3><ul class="timeline">{"".join(rows)}</ul></section>'


def _gaps_html(gaps: list[Gap]) -> str:
    if not gaps:
        return ""
    horizon = [g for g in gaps if g.gap_type != "missing_layer"]
    if not horizon:
        return ""
    items = "".join(f"<li>{esc(g.description)}</li>" for g in horizon)
    return f'<section><h3>Open questions</h3><ul class="gaps-list">{items}</ul></section>'


def _sources_html(sources: dict[str, Source], index: dict[str, int], answer: Answer) -> str:
    ordered = sorted(
        ((index[sid], s) for sid, s in sources.items() if sid in index),
        key=lambda pair: pair[0],
    )
    if not ordered:
        return ""
    items = []
    for number, source in ordered:
        href = safe_url(source.url)
        link = (
            f'<a href="{href}" target="_blank" rel="noopener noreferrer">read at source ↗</a>'
            if href else ""
        )
        items.append(
            f'<li id="src-{number}"><strong>{number}.</strong> {esc(source.author)} — '
            f"<em>{esc(source.title)}</em> ({esc(_date_of(source))}) "
            f'<span class="label">{esc(source.authority.value)}</span><br>{link}</li>'
        )
    return f'<section><h3>Sources</h3><ul class="candidates">{"".join(items)}</ul></section>'


def _attribution_html(answer: Answer, index: dict[str, int]) -> str:
    if not answer.attribution:
        return ""
    items = []
    for entry in answer.attribution:
        number = index.get(entry.source_id, 0)
        credit = f'<span class="credit">{esc(entry.attribution_text)}</span>' if entry.attribution_text else ""
        items.append(
            f"<li><strong>{number}.</strong> {esc(entry.author)}, <em>{esc(entry.title)}</em>"
            f" ({esc(entry.date_display)})"
            + (f" · {esc(entry.license_id)}" if entry.license_id else "")
            + credit + "</li>"
        )
    return (
        f'<div class="attribution"><h3>Attribution</h3><ul>{"".join(items)}</ul>'
        f"<p>Generated by art-context-engine on {answer.generated_at:%Y-%m-%d}. "
        "Quotation limits were applied per source licence.</p></div>"
    )


def _children_html(children: list[Answer]) -> str:
    if not children:
        return ""
    items = "".join(
        f'<li><a href="/a/{c.id}">{esc(c.question)}</a></li>' for c in children
    )
    return f'<section><h3>Follow-ups</h3><ul>{items}</ul></section>'


# ═══════════════════════════════════════════════════════════════════════════
# DEEPENING FRAGMENT
# ═══════════════════════════════════════════════════════════════════════════

def deepening_fragment(
    deepening: Deepening, sources: dict[str, Source]
) -> str:
    parts: list[str] = []
    for quote in deepening.quotes:
        parts.append(
            f'<blockquote>{esc(quote.text)}'
            f'<span class="src">{esc(quote.author)} — <em>{esc(quote.source_title)}</em>'
            f' ({esc(quote.date_display)}) · {esc(quote.authority.value)}</span></blockquote>'
        )
    if not parts:
        parts.append(
            '<p class="meta">No quotable text for this block — the sources behind it are '
            "link-only under their licences.</p>"
        )
    for conflict in deepening.conflicts:
        parts.append(_conflict_html(conflict, sources))
    for gap in deepening.gaps:
        parts.append(f'<p class="meta">Gap: {esc(gap.description)}</p>')
    if deepening.context:
        titles = ", ".join(esc(s.title) for s in deepening.context)
        parts.append(f'<p class="meta">Context sources: {titles}</p>')
    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# INDEX / CANDIDATES
# ═══════════════════════════════════════════════════════════════════════════

def index_page(recent: list[Answer], ask_action: str = "/ask") -> str:
    items = "".join(
        f'<li><a href="/a/{a.id}">{esc(a.question)}</a>'
        f'<div class="why">{esc(a.sufficiency.value.title())} · {a.generated_at:%Y-%m-%d}</div></li>'
        for a in recent
    ) or "<li>No answers yet.</li>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Art Context Engine</title>{HEAD}</head><body>
<header class="top"><h1>Art Context Engine</h1></header>
<main><article>
<h2>Understand a work in its world.</h2>
<p class="question">Ask why a piece sounds the way it does. You get a sourced answer:
what the documents say, when they said it, and where they disagree.</p>
<form class="ask" method="post" action="{ask_action}">
  <input type="text" name="question" placeholder="Why does the Arietta sound so plain?" required>
  <button type="submit">Ask</button>
</form>
<h3>Recent</h3><ul class="candidates">{items}</ul>
</article><aside></aside></main></body></html>"""


def candidates_page(question: str, candidates, ask_action: str = "/ask") -> str:
    items = "".join(
        f"<li><strong>{esc(c.title)}</strong> — {esc(c.creator)}"
        f'<div class="why">{esc(c.work_type.value.title())} · {esc(c.date_display)}'
        f' · match {c.confidence:.2f} ({esc(c.matched_on)})</div>'
        f'<form method="post" action="{ask_action}">'
        f'<input type="hidden" name="question" value="{esc(question)}">'
        f'<input type="hidden" name="work_id" value="{esc(c.work_id)}">'
        f'<button type="submit">Use this work</button></form></li>'
        for c in candidates
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Which work did you mean?</title>{HEAD}</head><body>
<header class="top"><h1>Art Context Engine</h1></header>
<main><article>
<h2>Which work did you mean?</h2>
<p class="question">{esc(question)}</p>
<ul class="candidates">{items}</ul>
</article><aside></aside></main></body></html>"""
