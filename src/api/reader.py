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

The visual language is in `CSS` below: a museum at night. Deep ink, gilded
hairlines and parchment plates, Fraunces for display and Newsreader for the
reading column. Everything is a file under /static so the Content-Security-Policy
can keep forbidding inline code; see `src/api/app.py`.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from urllib.parse import urlparse

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

# ═══════════════════════════════════════════════════════════════════════════
# DESIGN SYSTEM
# ═══════════════════════════════════════════════════════════════════════════

CSS = """
/* ── Faces ──────────────────────────────────────────────────────────────
   Self-hosted (OFL). No CDN: the CSP allows fonts from 'self' only, and a
   reading product should not phone a third party to render its text.        */
@font-face {
  font-family: "Fraunces"; font-style: normal; font-weight: 100 900;
  font-display: swap;
  src: url("/static/fonts/fraunces-latin.woff2") format("woff2");
}
@font-face {
  font-family: "Newsreader"; font-style: normal; font-weight: 200 700;
  font-display: swap;
  src: url("/static/fonts/newsreader-latin.woff2") format("woff2");
}
@font-face {
  font-family: "Newsreader"; font-style: italic; font-weight: 200 500;
  font-display: swap;
  src: url("/static/fonts/newsreader-italic-latin.woff2") format("woff2");
}

/* ── Tokens ───────────────────────────────────────────────────────────────
   Three radii, two shadows, one accent. The restraint is the design: a room
   lit by one lamp reads better than a room lit by six.                      */
:root {
  color-scheme: dark;
  --ink: #07060a;
  --ink-1: #0a0910;
  --ink-2: #0f0d15;
  --ink-3: #15121c;
  --text: #ece3d1;
  --dim: #a2957f;
  --faint: #6b6153;
  --gold: #c49c56;
  --gold-hi: #e7cd99;
  --gold-soft: rgba(196, 156, 86, .12);
  --rule: rgba(196, 156, 86, .2);
  --rule-soft: rgba(236, 227, 209, .085);
  --oxblood: #a63a4a;
  --oxblood-hi: #d97684;
  --verdigris: #6aa895;
  --card: rgba(255, 247, 232, .03);
  --card-hi: rgba(255, 247, 232, .06);
  --paper: #f4ecdb;
  --paper-2: #e6dac2;
  --paper-ink: #191317;
  --shadow-1: 0 1px 2px rgba(0, 0, 0, .5), 0 12px 30px -20px rgba(0, 0, 0, .9);
  --shadow-2: 0 2px 5px rgba(0, 0, 0, .45), 0 46px 90px -46px rgba(0, 0, 0, .95);

  --masthead: 78px;
  --wrap: 1200px;
  --pad: clamp(20px, 4.2vw, 52px);
  --btn-bg: #c49c56;
  --btn-fg: #140e06;

  --font-display: "Fraunces", ui-serif, Georgia, "Times New Roman", serif;
  --font-body: "Newsreader", ui-serif, Georgia, serif;
  --font-mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;

  --r: 2px;
  --r-lg: 4px;
  --ease: cubic-bezier(.22, .61, .36, 1);
  --ease-out: cubic-bezier(.16, 1, .3, 1);
}

/* Light theme. Kept in sync with the media-query block below: the attribute
   wins when the reader has chosen, the OS preference is the default. */
:root[data-theme="light"] {
  color-scheme: light;
  --ink: #efe8d8;
  --ink-1: #f5efe2;
  --ink-2: #fbf6ea;
  --ink-3: #e8dec9;
  --text: #171216;
  --dim: #5b5245;
  --faint: #877d70;
  --gold: #77531a;
  --gold-hi: #9c7327;
  --gold-soft: rgba(119, 83, 26, .09);
  --rule: rgba(60, 42, 20, .2);
  --rule-soft: rgba(23, 18, 22, .085);
  --oxblood: #8d2b3b;
  --oxblood-hi: #a63a4a;
  --verdigris: #2f6b5c;
  --card: #fbf6ea;
  --card-hi: #fffdf7;
  --shadow-1: 0 1px 2px rgba(60, 45, 25, .13), 0 12px 26px -18px rgba(60, 45, 25, .4);
  --shadow-2: 0 2px 6px rgba(60, 45, 25, .15), 0 40px 80px -40px rgba(60, 45, 25, .45);
  --btn-bg: #1d171a;
  --btn-fg: #f5efe2;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) {
    color-scheme: light;
    --ink: #efe8d8;
    --ink-1: #f5efe2;
    --ink-2: #fbf6ea;
    --ink-3: #e8dec9;
    --text: #171216;
    --dim: #5b5245;
    --faint: #877d70;
    --gold: #77531a;
    --gold-hi: #9c7327;
    --gold-soft: rgba(119, 83, 26, .09);
    --rule: rgba(60, 42, 20, .2);
    --rule-soft: rgba(23, 18, 22, .085);
    --oxblood: #8d2b3b;
    --oxblood-hi: #a63a4a;
    --verdigris: #2f6b5c;
    --card: #fbf6ea;
    --card-hi: #fffdf7;
    --shadow-1: 0 1px 2px rgba(60, 45, 25, .13), 0 12px 26px -18px rgba(60, 45, 25, .4);
    --shadow-2: 0 2px 6px rgba(60, 45, 25, .15), 0 40px 80px -40px rgba(60, 45, 25, .45);
    --btn-bg: #1d171a;
    --btn-fg: #f5efe2;
  }
}

/* ── Reset ─────────────────────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; scroll-behavior: smooth; }
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } }
body {
  margin: 0;
  background: var(--ink);
  color: var(--text);
  font-family: var(--font-body);
  font-size: 1.0625rem;
  line-height: 1.66;
  font-optical-sizing: auto;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
  overflow-x: hidden;
}
img, svg { max-width: 100%; }
h1, h2, h3, h4 {
  font-family: var(--font-display);
  font-weight: 400;
  font-variation-settings: "SOFT" 30, "WONK" 1;
  letter-spacing: -.024em;
  line-height: 1.08;
  margin: 0;
  text-wrap: balance;
}
p { margin: 0 0 1.05em; text-wrap: pretty; }
a { color: var(--gold); text-decoration-color: color-mix(in srgb, var(--gold) 45%, transparent); text-underline-offset: .2em; }
a:hover { color: var(--gold-hi); }
::selection { background: var(--gold); color: #120d07; }
:focus-visible { outline: 2px solid var(--gold); outline-offset: 3px; border-radius: 2px; }
button { font: inherit; color: inherit; }
[hidden] { display: none !important; }

/* ── Canvas: one lamp, not a light show ───────────────────────────────── */
main { position: relative; z-index: 1; }
.wrap {
  width: 100%;
  max-width: var(--wrap);
  margin-inline: auto;
  padding-inline: var(--pad);
}
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
}
.skip {
  position: fixed; top: -100px; left: 16px; z-index: 100;
  background: var(--btn-bg); color: var(--btn-fg); padding: 10px 16px;
  border-radius: var(--r); font: 500 13px/1 var(--font-body);
}
.skip:focus { top: 12px; }

/* ── Primitives ───────────────────────────────────────────────────────── */
/* A kicker is a book's subtitle, not a badge: italic, one line, no box. */
.kicker {
  font-family: var(--font-body); font-style: italic; font-size: .95rem;
  color: var(--dim); margin: 0 0 20px; letter-spacing: .01em;
}
.kicker::after {
  content: ""; display: block; width: 42px; height: 1px; margin-top: 14px;
  background: linear-gradient(90deg, var(--gold), transparent);
}
.label {
  font-family: var(--font-mono); font-size: 10px; letter-spacing: .12em;
  text-transform: uppercase; color: var(--faint);
}
.hair { height: 1px; border: 0; background: var(--rule-soft); margin: 0; }

/* Classification stamps: a call number, not a pill. */
.chip {
  display: inline-flex; align-items: center; gap: 7px;
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .11em;
  text-transform: uppercase; padding: 4px 8px 3px;
  border: 1px solid var(--rule); border-radius: var(--r); color: var(--dim);
  background: transparent; white-space: nowrap;
}
.chip-ok { color: var(--gold); border-color: color-mix(in srgb, var(--gold) 42%, transparent); }
.chip-thin { color: var(--dim); }
.chip-warn { color: var(--oxblood-hi); border-color: color-mix(in srgb, var(--oxblood) 55%, transparent); }
.chip-verdigris { color: var(--verdigris); border-color: color-mix(in srgb, var(--verdigris) 45%, transparent); }

/* ── Masthead ─────────────────────────────────────────────────────────── */
.masthead {
  position: sticky; top: 0; z-index: 50;
  background: var(--ink);
  border-bottom: 1px solid var(--rule-soft);
  transition: box-shadow .3s var(--ease);
}
.masthead.scrolled { box-shadow: 0 10px 30px -22px rgba(0, 0, 0, .9); }
.masthead-in {
  display: flex; align-items: center; gap: 30px;
  height: var(--masthead);
  transition: height .35s var(--ease);
}
.masthead.scrolled .masthead-in { height: 58px; }
.brand {
  display: inline-flex; align-items: baseline; gap: 10px; flex: none;
  text-decoration: none; color: var(--text);
  font-family: var(--font-display); font-size: 1.32rem; letter-spacing: -.028em;
  font-variation-settings: "SOFT" 40, "WONK" 1;
  transition: color .2s var(--ease);
}
.brand:hover { color: var(--gold-hi); }
.brand .brand-rule {
  width: 18px; height: 1px; background: var(--gold); flex: none;
  transform-origin: 0 50%; transition: width .3s var(--ease);
  align-self: center;
}
.brand:hover .brand-rule { width: 30px; }
.masthead-note {
  margin: 0; font-family: var(--font-body); font-style: italic;
  font-size: .92rem; color: var(--faint); white-space: nowrap;
  transition: opacity .25s var(--ease);
}
.masthead.scrolled .masthead-note { opacity: 0; }
.menu { display: flex; align-items: center; gap: 26px; margin-left: auto; }
.menu a {
  position: relative; padding-block: 4px;
  font-family: var(--font-body); font-size: .96rem; text-decoration: none;
  color: var(--dim); border-bottom: 1px solid transparent;
  transition: color .18s var(--ease), border-color .18s var(--ease);
}
.menu a:hover { color: var(--text); border-bottom-color: var(--gold); }
.menu a[aria-current="page"] { color: var(--text); border-bottom-color: var(--gold); }
.theme-toggle {
  width: 30px; height: 30px; flex: none; display: grid; place-items: center;
  border: 0; background: none; padding: 0; color: var(--faint); cursor: pointer;
  transition: color .2s var(--ease), transform .4s var(--ease);
}
.theme-toggle:hover { color: var(--gold); transform: rotate(22deg); }
.theme-toggle .glyph { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; }
.theme-toggle .moon { display: none; }
:root[data-theme="light"] .theme-toggle .sun { display: none; }
:root[data-theme="light"] .theme-toggle .moon { display: block; }
:root[data-theme="dark"] .theme-toggle .sun { display: block; }
:root[data-theme="dark"] .theme-toggle .moon { display: none; }
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) .theme-toggle .sun { display: none; }
  :root:not([data-theme="dark"]) .theme-toggle .moon { display: block; }
}
@media (max-width: 900px) {
  .masthead-note { display: none; }
  .menu { gap: 18px; }
}
@media (max-width: 560px) {
  .masthead-in { gap: 14px; }
  .brand { font-size: 1.16rem; }
  .brand .brand-rule { width: 12px; }
  .menu { gap: 14px; }
  .menu a { font-size: .9rem; }
}

/* ── Buttons and fields ──────────────────────────────────────────────── */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 9px;
  padding: 11px 18px; border-radius: var(--r); cursor: pointer;
  border: 1px solid var(--btn-bg); background: var(--btn-bg); color: var(--btn-fg);
  font-family: var(--font-body); font-size: .95rem; font-weight: 500;
  text-decoration: none;
  transition: transform .18s var(--ease), box-shadow .2s var(--ease), background .2s var(--ease);
  box-shadow: 0 10px 24px -18px rgba(0, 0, 0, .9);
}
.btn:hover { color: var(--btn-fg); transform: translateY(-1px); }
.btn:active { transform: translateY(0); }
.btn-ghost { background: transparent; color: var(--text); border-color: var(--rule); box-shadow: none; }
.btn-ghost:hover { color: var(--gold); border-color: var(--gold); }

/* The question field is a line in a form, not a search box. */
.ask { display: flex; gap: 14px; align-items: flex-end; }
.ask input[type="text"], .ask input[type="email"], .plan input[type="email"] {
  flex: 1; min-width: 0;
  padding: 12px 2px;
  border: 0; border-bottom: 1px solid var(--rule);
  border-radius: 0;
  background: transparent;
  color: var(--text);
  font-family: var(--font-body); font-style: italic;
  font-size: clamp(1.08rem, 1rem + .3vw, 1.2rem);
  transition: border-color .2s var(--ease);
}
.ask input::placeholder { color: var(--faint); font-style: italic; }
.ask input:focus { outline: none; border-bottom-color: var(--gold); }
.ask button {
  flex: none; display: inline-flex; align-items: center; gap: 10px;
  padding: 12px 20px; border-radius: var(--r); cursor: pointer;
  border: 1px solid var(--btn-bg); background: var(--btn-bg); color: var(--btn-fg);
  font-family: var(--font-body); font-size: .95rem; font-weight: 500;
  transition: transform .18s var(--ease), box-shadow .25s var(--ease);
}
.ask button:hover { transform: translateY(-1px); box-shadow: 0 14px 28px -18px rgba(0, 0, 0, .8); }
.ask button svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.7; transition: transform .25s var(--ease); }
.ask button:hover svg { transform: translateX(3px); }
@media (max-width: 560px) {
  .ask { flex-direction: column; align-items: stretch; gap: 16px; }
  .ask button { justify-content: center; padding: 13px 20px; }
}

/* ── Hero ─────────────────────────────────────────────────────────────── */
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.06fr) minmax(0, .94fr);
  gap: clamp(30px, 4.4vw, 72px);
  align-items: start;
  padding-block: clamp(44px, 6.2vw, 92px) clamp(38px, 5vw, 76px);
}
.hero-copy { min-width: 0; padding-top: clamp(0px, 2vw, 26px); }
.display {
  font-size: clamp(2.7rem, 1.4rem + 4.6vw, 5.1rem);
  line-height: .95;
  letter-spacing: -.038em;
  font-variation-settings: "SOFT" 46, "WONK" 1;
  margin: 0 0 26px;
}
.display em {
  font-style: italic;
  color: var(--gold-hi);
  font-variation-settings: "SOFT" 96, "WONK" 1;
}
.display .line { display: block; overflow: hidden; padding-bottom: .04em; }
.display .line > span { display: block; }
.lede {
  font-size: clamp(1.04rem, .97rem + .32vw, 1.16rem);
  line-height: 1.7;
  color: var(--dim);
  max-width: 42ch;
  margin-bottom: 34px;
}
.lede b { color: var(--text); font-weight: 500; }

/* ── The index of written documents: a table of contents, not pills ───── */
.index { margin-top: 42px; border-top: 1px solid var(--rule-soft); }
.index-head {
  display: flex; align-items: baseline; justify-content: space-between; gap: 16px;
  padding: 14px 0 0;
}
.index-head .label { color: var(--faint); }
.index-head a {
  font-family: var(--font-body); font-style: italic; font-size: .9rem;
  text-decoration: none; color: var(--dim);
}
.index-head a:hover { color: var(--gold); }
.index ol { list-style: none; margin: 8px 0 0; padding: 0; }
.index li { margin: 0; }
.index-link {
  display: grid; grid-template-columns: 30px minmax(0, 1fr) auto;
  gap: 12px; align-items: baseline;
  padding: 10px 0; border-bottom: 1px solid var(--rule-soft);
  text-decoration: none; color: var(--text);
  transition: color .18s var(--ease);
}
.index-link:hover { color: var(--gold-hi); }
.index-link .n {
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .1em;
  color: var(--gold); padding-top: 2px;
}
.index-link .t {
  font-family: var(--font-display); font-size: 1.02rem; line-height: 1.3;
  letter-spacing: -.012em;
  min-width: 0;
}
.index-link .w {
  display: block; margin-top: 2px;
  font-family: var(--font-body); font-style: italic; font-size: .86rem;
  color: var(--faint);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.index-link .arrow {
  font-family: var(--font-mono); font-size: 11px; color: var(--faint);
  transition: transform .2s var(--ease), color .2s var(--ease);
}
.index-link:hover .arrow { color: var(--gold); transform: translateX(3px); }

/* ── The plate: one sheet of paper on a dark desk ─────────────────────── */
.plate-col { position: relative; min-width: 0; }
.plate-wrap {
  position: relative;
  perspective: 1600px;
  padding: 18px 12px 34px;
}
.plate-wrap::before,
.plate-wrap::after {
  content: ""; position: absolute; pointer-events: none; border-radius: var(--r);
  background: linear-gradient(158deg, #e4d5b6, #c6b28c);
  box-shadow: 0 20px 40px -24px rgba(0, 0, 0, .8);
}
.plate-wrap::before { inset: 34px 34px 42px 24px; transform: rotate(-2.2deg); opacity: .42; }
.plate-wrap::after { inset: 46px 24px 36px 40px; transform: rotate(1.6deg); opacity: .2; }
:root[data-theme="light"] .plate-wrap::before,
:root[data-theme="light"] .plate-wrap::after { opacity: .28; }
.plate {
  position: relative; z-index: 1;
  transform-style: preserve-3d;
  transform: rotateX(var(--rx, 5deg)) rotateY(var(--ry, -11deg)) rotateZ(var(--rz, -1deg));
  transition: transform .8s var(--ease-out), box-shadow .8s var(--ease-out);
  background: linear-gradient(157deg, #fdf7e9 0%, var(--paper) 40%, var(--paper-2) 100%);
  color: var(--paper-ink);
  border-radius: var(--r);
  padding: clamp(20px, 2.4vw, 30px) clamp(18px, 2.4vw, 30px) clamp(20px, 2.6vw, 28px);
  box-shadow: var(--shadow-2);
}
/* Paper, not plastic: fibre grain and a spine shadow, on the sheet only. */
.plate::before {
  content: ""; position: absolute; inset: 0; border-radius: var(--r);
  pointer-events: none; opacity: .5; mix-blend-mode: multiply;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='p'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23p)' opacity='.16'/%3E%3C/svg%3E");
}
.plate::after {
  content: ""; position: absolute; inset: 0 auto 0 0; width: 10px;
  border-radius: var(--r) 0 0 var(--r);
  background: linear-gradient(90deg, rgba(78, 54, 18, .22), rgba(78, 54, 18, .04) 70%, transparent);
}
.plate a { color: #7a2b22; }
.plate-head {
  position: relative;
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding-bottom: 12px; margin-bottom: 18px;
  border-bottom: 1px solid rgba(110, 78, 28, .26);
}
.plate-head .label { color: #7b6544; }
.plate-q {
  position: relative;
  font-family: var(--font-display); font-size: clamp(1.14rem, 1.5vw, 1.45rem);
  line-height: 1.14; letter-spacing: -.022em; margin: 0 0 12px;
  font-variation-settings: "SOFT" 40, "WONK" 1;
  color: #191317;
}
.plate-link { text-decoration: none; display: block; }
.plate-link:hover .plate-q { text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 4px; }
.plate-thesis {
  position: relative;
  font-family: var(--font-body); font-size: 15px; line-height: 1.56;
  color: #33282a; margin: 0 0 18px;
}
.plate-srcs { position: relative; list-style: none; margin: 0 0 16px; padding: 0; }
.plate-srcs li {
  display: grid; grid-template-columns: 16px 1fr; gap: 9px;
  padding: 7px 0; border-top: 1px solid rgba(110, 78, 28, .14);
  font-size: 13px; line-height: 1.34; color: #4a3d38;
}
.plate-srcs li:last-child { border-bottom: 1px solid rgba(110, 78, 28, .14); }
.plate-srcs .n { font-family: var(--font-mono); font-size: 9.5px; color: #8a6a2c; padding-top: 2px; }
.plate-foot {
  position: relative;
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding-right: 44px;
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .12em;
  text-transform: uppercase; color: #836f4e;
}
.plate-seal {
  position: absolute; right: -16px; bottom: -24px; z-index: 2;
  width: 80px; height: 80px; border-radius: 50%;
  display: grid; place-items: center;
  transform: rotate(-12deg) translateZ(40px);
  background: radial-gradient(circle at 34% 28%, #b8402f, #7d1f18 62%, #5e1411);
  color: #f7e3c4; text-align: center;
  font-family: var(--font-mono); font-size: 7.5px; letter-spacing: .13em;
  text-transform: uppercase; line-height: 1.5;
  box-shadow: 0 12px 26px -10px rgba(0, 0, 0, .8), inset 0 2px 6px rgba(255, 255, 255, .22);
}
.plate-seal::before {
  content: ""; position: absolute; inset: 6px; border-radius: 50%;
  border: 1px dashed rgba(247, 227, 196, .45);
}
.plate-seal b { display: block; font-family: var(--font-display); font-size: 16px; letter-spacing: .02em; }
.plate-note {
  margin: 18px 0 0; text-align: center;
  font-family: var(--font-body); font-style: italic; font-size: .88rem;
  color: var(--faint);
}
.plate-note a { color: var(--dim); text-underline-offset: 3px; }
.plate-note a:hover { color: var(--gold); }
.plate-shadow {
  position: absolute; left: 14%; right: 14%; bottom: 10px; height: 30px;
  filter: blur(24px); background: rgba(0, 0, 0, .6); border-radius: 50%;
  z-index: 0;
}
:root[data-theme="light"] .plate-shadow { background: rgba(60, 42, 20, .28); }
/* One lamp behind the sheet, and a highlight that follows the pointer. */
.plate-col {
  display: flex; flex-direction: column; justify-content: center;
}
.plate-col::before {
  content: ""; position: absolute; inset: -12% -16% -8% -20%; z-index: -1;
  pointer-events: none;
  background: radial-gradient(48% 46% at 58% 42%, rgba(196, 156, 86, .16), transparent 70%);
}
.plate-glare {
  position: absolute; inset: 0; z-index: 2; border-radius: var(--r);
  pointer-events: none; opacity: 0;
  transition: opacity .5s var(--ease-out);
  background: radial-gradient(220px 220px at var(--mx, 50%) var(--my, 0%),
    rgba(255, 255, 255, .5), rgba(255, 255, 255, 0) 62%);
  mix-blend-mode: soft-light;
}

/* ── Bands ────────────────────────────────────────────────────────────── */
.band { padding-block: clamp(48px, 7vw, 96px); border-top: 1px solid var(--rule-soft); }
.band-grid {
  display: grid; grid-template-columns: minmax(0, .78fr) minmax(0, 1.22fr);
  gap: clamp(26px, 5vw, 88px); align-items: start;
}
.band-head h2 { font-size: clamp(1.6rem, 1.1rem + 1.5vw, 2.35rem); margin-bottom: 14px; }
.band-head p { color: var(--dim); margin: 0; max-width: 34ch; }
.clauses { list-style: none; margin: 0; padding: 0; counter-reset: clause; }
.clauses li {
  counter-increment: clause;
  display: grid; grid-template-columns: 34px minmax(0, 1fr);
  gap: 4px 14px; padding: 22px 0;
  border-top: 1px solid var(--rule-soft);
  margin: 0;
}
.clauses li:first-child { border-top: 0; padding-top: 0; }
.clauses li::before {
  content: counter(clause, decimal-leading-zero);
  grid-row: span 2;
  font-family: var(--font-mono); font-size: 10px; letter-spacing: .08em;
  color: var(--gold); padding-top: 5px;
}
.clauses h3 { font-size: 1.12rem; margin: 0 0 6px; letter-spacing: -.015em; }
.clauses p { color: var(--dim); font-size: .97rem; margin: 0; max-width: 52ch; }

.sec-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 18px; margin-bottom: 26px;
}
.sec-head h2 { font-size: clamp(1.4rem, 1.1rem + 1vw, 1.9rem); }
.sec-head a {
  font-family: var(--font-body); font-style: italic; font-size: .92rem;
  text-decoration: none; color: var(--dim); white-space: nowrap;
}
.sec-head a:hover { color: var(--gold); }

/* ── The index of documents (table) ───────────────────────────────────── */
.ledger { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--rule); }
.ledger li { margin: 0; }
.doc-row {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) 15rem 7.5rem 5rem;
  gap: 14px; align-items: baseline;
  padding: 17px 4px; border-bottom: 1px solid var(--rule-soft);
  text-decoration: none; color: var(--text);
  transition: background .2s var(--ease), padding-left .2s var(--ease);
}
.doc-row:hover { background: var(--card); padding-left: 10px; color: var(--text); }
.doc-row .i { font-family: var(--font-mono); font-size: 10px; color: var(--gold); letter-spacing: .1em; }
.doc-row .q {
  font-family: var(--font-display); font-size: clamp(1.02rem, .95rem + .35vw, 1.2rem);
  line-height: 1.28; letter-spacing: -.014em;
}
.doc-row .w {
  font-family: var(--font-body); font-style: italic; font-size: .88rem; color: var(--faint);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.doc-row .d {
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .09em;
  text-transform: uppercase; color: var(--faint); white-space: nowrap;
}
.doc-row .s {
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .09em;
  text-transform: uppercase; color: var(--faint); text-align: right; white-space: nowrap;
  transition: color .2s var(--ease);
}
.doc-row:hover .s { color: var(--gold); }
.doc-empty {
  display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 14px;
  padding: 22px 4px; border-bottom: 1px solid var(--rule-soft); color: var(--faint);
}
@media (max-width: 900px) {
  .doc-row { grid-template-columns: 34px minmax(0, 1fr); gap: 6px 12px; }
  .doc-row .w, .doc-row .d, .doc-row .s { display: none; }
}

/* ── Document page ────────────────────────────────────────────────────── */
.doc-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 322px;
  gap: clamp(32px, 5vw, 80px);
  align-items: start;
  padding-block: clamp(32px, 4.5vw, 60px) clamp(56px, 8vw, 108px);
}
.doc { position: relative; min-width: 0; }
/* Reading progress as the margin of the manuscript, not a bar on the screen. */
.margin-rule {
  display: none; position: absolute; left: calc(-1 * clamp(18px, 2.6vw, 34px));
  top: 0; bottom: 0; width: 1px; background: var(--rule-soft);
}
.margin-rule span {
  display: block; width: 100%; height: 100%; transform-origin: 50% 0;
  transform: scaleY(0);
  background: linear-gradient(180deg, var(--gold), color-mix(in srgb, var(--gold) 35%, transparent));
}
@media (min-width: 1200px) { .margin-rule { display: block; } }

.doc-head { margin-bottom: clamp(26px, 3.6vw, 42px); }
.doc-title {
  font-size: clamp(1.95rem, 1.2rem + 2.4vw, 3.2rem);
  line-height: 1.05;
  letter-spacing: -.03em;
  font-variation-settings: "SOFT" 42, "WONK" 1;
  margin: 12px 0 14px;
}
.doc-work {
  font-family: var(--font-display); font-size: 1rem; color: var(--dim);
  margin: 0 0 18px; letter-spacing: 0;
}
.doc-work em { color: var(--text); font-style: italic; }
.doc-meta {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px;
  padding-top: 14px; border-top: 1px solid var(--rule);
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--dim);
}
.doc-meta .dot { width: 3px; height: 3px; border-radius: 50%; background: var(--faint); }
.copy-link {
  margin-left: auto; display: inline-flex; align-items: center; gap: 7px;
  background: none; border: 0; padding: 3px 0; cursor: pointer;
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--dim);
}
.copy-link:hover { color: var(--gold); }
.copy-link svg { width: 11px; height: 11px; fill: none; stroke: currentColor; stroke-width: 1.7; }

/* Thesis: a pull-quote in the margin, not a card */
.thesis {
  position: relative;
  font-family: var(--font-display);
  font-size: clamp(1.24rem, 1rem + .72vw, 1.58rem);
  line-height: 1.44;
  letter-spacing: -.014em;
  font-variation-settings: "SOFT" 54, "WONK" 1;
  padding: 2px 0 2px clamp(18px, 2.6vw, 26px);
  margin: 0 0 clamp(28px, 3.6vw, 42px);
  border-left: 1px solid var(--rule);
  color: var(--text);
}
.thesis::before {
  content: ""; position: absolute; left: -1px; top: 0; width: 1px; height: 26px;
  background: var(--gold);
}
.thesis .pins { font-size: 10px; vertical-align: baseline; margin-left: 6px; }

.prose { font-size: clamp(1.04rem, .96rem + .26vw, 1.15rem); }
.prose > .block { margin-bottom: 30px; }
.prose > .block > p { line-height: 1.74; color: var(--text); max-width: 34em; margin-bottom: 6px; }
.prose p { line-height: 1.72; }
.prose .dropcap::first-letter {
  float: left; font-family: var(--font-display); font-size: 3.5em; line-height: .82;
  padding: .05em .1em 0 0; color: var(--gold); font-weight: 500;
}

.pins {
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .04em;
  vertical-align: super; margin-left: 4px; line-height: 0;
}
.pins a {
  display: inline-block; padding: 1px 3px; margin-right: 1px; border-radius: var(--r);
  color: var(--gold); text-decoration: none;
  transition: background .15s var(--ease), color .15s var(--ease);
}
.pins a:hover, .pins a.lit { background: var(--gold); color: #150e07; }

/* Quotes: an indented block from another book, ruled on the left */
.quote {
  position: relative; margin: 0 0 26px; padding: 4px 0 4px clamp(20px, 3vw, 30px);
  border-left: 1px solid var(--rule);
  font-style: italic; font-size: 1.08rem; line-height: 1.66;
  color: var(--text); white-space: pre-wrap; max-width: 36em;
}
.quote .src {
  display: block; margin-top: 12px;
  font-family: var(--font-mono); font-style: normal; font-size: 9.5px;
  letter-spacing: .09em; text-transform: uppercase; color: var(--dim);
}
.quote-note {
  margin: -16px 0 28px; font-family: var(--font-mono); font-size: 9px;
  letter-spacing: .12em; text-transform: uppercase; color: var(--faint);
}

/* Conflicts: a ledger with two columns and a rule between them */
.conflict {
  margin: 0 0 30px; border: 1px solid var(--rule); border-radius: var(--r-lg);
  overflow: hidden; background: var(--card);
}
.conflict-head {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 13px 18px; border-bottom: 1px solid var(--rule);
}
.conflict-q {
  font-family: var(--font-display); font-size: 1rem; color: var(--text);
  margin: 0; flex: 1 1 220px; line-height: 1.3;
}
.positions { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 1px; background: var(--rule); }
.pos { background: var(--ink-1); padding: 18px; min-width: 0; }
.pos-value {
  font-family: var(--font-display); font-size: 1rem; line-height: 1.36;
  margin: 0 0 10px; color: var(--text);
}
.pos-src {
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .09em;
  text-transform: uppercase; color: var(--faint); margin: 0 0 10px;
}
.pos blockquote {
  margin: 0; padding-left: 11px; border-left: 1px solid var(--rule);
  font-style: italic; font-size: .92rem; line-height: 1.55; color: var(--dim);
}
.verdict {
  margin: 0; padding: 11px 18px; border-top: 1px solid var(--rule);
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--dim);
}
.conflict.readings .chip { color: var(--verdigris); }

/* Gaps: what nobody recorded, set as a note in the margin of the page */
.gaps {
  margin: 0 0 30px; padding: 4px 0 4px clamp(18px, 2.6vw, 26px);
  border-left: 1px dashed var(--rule);
}
.gaps h3 {
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .14em;
  text-transform: uppercase; color: var(--dim); margin-bottom: 10px;
}
.gaps ul { margin: 0; padding: 0; list-style: none; }
.gaps li {
  position: relative; padding-left: 18px; margin-bottom: 7px;
  font-size: .97rem; line-height: 1.5; color: var(--dim); max-width: 34em;
}
.gaps li::before { content: "—"; position: absolute; left: 0; color: var(--gold); }
.gaps li:last-child { margin-bottom: 0; }

/* Deepening */
.deepen {
  display: inline-flex; align-items: center; gap: 7px; margin-top: 2px;
  padding: 4px 0; background: none; border: 0; cursor: pointer;
  font-family: var(--font-body); font-style: italic; font-size: .9rem;
  color: var(--faint); transition: color .18s var(--ease);
}
.deepen:hover { color: var(--gold); }
.deepen .chev { width: 10px; height: 10px; fill: none; stroke: currentColor; stroke-width: 2; transition: transform .3s var(--ease); }
.deepen[aria-expanded="true"] { color: var(--gold); }
.deepen[aria-expanded="true"] .chev { transform: rotate(180deg); }
.deepen[disabled] { opacity: .5; cursor: progress; }
.deepening:empty { display: none; }
.deepening {
  margin-top: 12px; padding-left: clamp(14px, 2.2vw, 24px);
  border-left: 1px solid var(--rule);
  animation: unfold .4s var(--ease-out) both;
}
.deepening .meta {
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--faint); margin: 0 0 10px;
}
@keyframes unfold {
  from { opacity: 0; transform: translateY(-5px); }
  to { opacity: 1; transform: none; }
}

.banner {
  display: flex; gap: 12px; align-items: flex-start;
  padding: 14px 16px; margin: 0 0 30px; border-radius: var(--r);
  border: 1px solid var(--rule); background: var(--card);
  font-size: .96rem; line-height: 1.55; color: var(--dim);
}
.banner strong { color: var(--text); font-weight: 600; }
.banner.warn { border-color: color-mix(in srgb, var(--oxblood) 50%, transparent); }
.banner.warn strong { color: var(--oxblood-hi); }

.attribution { margin-top: clamp(38px, 5vw, 60px); padding-top: 22px; border-top: 1px solid var(--rule); }
.attribution h3 {
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .14em;
  text-transform: uppercase; color: var(--dim); margin-bottom: 14px;
}
.attribution ul { list-style: none; margin: 0 0 16px; padding: 0; }
.attribution li {
  display: grid; grid-template-columns: 24px 1fr; gap: 8px;
  padding: 8px 0; border-top: 1px solid var(--rule-soft);
  font-size: .87rem; line-height: 1.5; color: var(--dim);
}
.attribution li strong { color: var(--gold); font-family: var(--font-mono); font-size: 9.5px; font-weight: 400; padding-top: 3px; }
.attribution .credit { display: block; margin-top: 3px; color: var(--faint); font-size: .81rem; }
.attribution > p { font-family: var(--font-mono); font-size: 9px; letter-spacing: .09em; text-transform: uppercase; color: var(--faint); margin: 0; }

/* ── The rail: marginalia, in the literal sense ───────────────────────── */
.rail { display: grid; gap: 32px; align-content: start; }
@media (min-width: 1041px) {
  .rail {
    position: sticky; top: calc(58px + 26px);
    max-height: calc(100vh - 84px);
    overflow-y: auto; overscroll-behavior: contain;
    padding-right: 6px; scrollbar-width: thin;
  }
}
.rail section { min-width: 0; }
.rail h3 {
  display: flex; align-items: center; gap: 10px;
  font-family: var(--font-body); font-style: italic; font-size: .95rem;
  font-weight: 400; color: var(--dim); margin: 0 0 14px;
}
.rail h3::after { content: ""; flex: 1; height: 1px; background: var(--rule-soft); }

.timeline { list-style: none; margin: 0; padding: 0 0 0 2px; position: relative; }
.timeline::before {
  content: ""; position: absolute; left: 3px; top: 6px; bottom: 12px; width: 1px;
  background: linear-gradient(180deg, var(--gold), rgba(196, 156, 86, .22));
}
.timeline li {
  position: relative; padding: 0 0 16px 18px; margin: 0;
  font-size: .89rem; line-height: 1.46; color: var(--dim);
}
.timeline li::before {
  content: ""; position: absolute; left: -1px; top: 6px; width: 7px; height: 7px;
  border-radius: 50%; background: var(--ink);
  border: 1px solid var(--gold); box-shadow: 0 0 0 3px var(--ink);
}
.timeline li:last-child { padding-bottom: 0; }
.timeline .when {
  display: block; font-family: var(--font-mono); font-size: 9px;
  letter-spacing: .1em; text-transform: uppercase; color: var(--gold);
  margin-bottom: 2px;
}
.timeline .t-title { color: var(--text); }
.timeline li.undated::before { border-color: var(--faint); }
.timeline li.undated .when { color: var(--faint); }

.gaps-list { list-style: none; margin: 0; padding: 0; }
.gaps-list li {
  position: relative; padding: 0 0 11px 15px; margin: 0;
  font-size: .89rem; line-height: 1.5; color: var(--dim);
}
.gaps-list li::before { content: "—"; position: absolute; left: 0; color: var(--gold); }

.srcs { list-style: none; margin: 0; padding: 0; }
.srcs li {
  display: grid; grid-template-columns: 24px 1fr; gap: 3px 8px;
  padding: 11px 0; margin: 0;
  border-top: 1px solid var(--rule-soft);
  scroll-margin-top: 90px;
  transition: background .2s var(--ease);
}
.srcs li:first-child { border-top: 0; padding-top: 0; }
.srcs .n { font-family: var(--font-mono); font-size: 10px; color: var(--gold); line-height: 1.5; padding-top: 1px; }
.srcs .who { font-size: .89rem; line-height: 1.42; color: var(--text); transition: color .2s var(--ease); }
.srcs .who em { font-style: italic; }
.srcs .when {
  display: block; font-family: var(--font-mono); font-size: 8.5px; letter-spacing: .09em;
  text-transform: uppercase; color: var(--faint); margin-top: 3px;
}
.srcs .who a {
  display: inline-block; margin-top: 5px; font-family: var(--font-mono);
  font-size: 8.5px; letter-spacing: .1em; text-transform: uppercase;
  text-decoration: none; color: var(--gold);
}
.srcs .who a:hover { text-decoration: underline; }
.srcs li.lit .who { color: var(--gold-hi); }
.srcs li:target { background: var(--gold-soft); box-shadow: 0 0 0 6px var(--gold-soft); }

.rail .ask { flex-direction: column; align-items: stretch; gap: 14px; }
.rail .ask input[type="text"] { font-size: .95rem; padding: 9px 0; }
.rail .ask button { padding: 10px 18px; justify-content: center; font-size: .9rem; }

/* ── Candidates ───────────────────────────────────────────────────────── */
.page-head { padding-block: clamp(36px, 5.4vw, 68px) 0; }
.page-head h2 { font-size: clamp(1.8rem, 3.2vw, 2.7rem); margin-bottom: 12px; }
.page-head p { color: var(--dim); max-width: 56ch; }
.candidates { list-style: none; margin: 30px 0 0; padding: 0; border-top: 1px solid var(--rule); }
.candidates li {
  display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px 22px;
  align-items: center; padding: 18px 2px; margin: 0;
  border-bottom: 1px solid var(--rule-soft);
  transition: background .2s var(--ease), padding-left .2s var(--ease);
}
.candidates li:hover { background: var(--card); padding-left: 10px; }
.candidates .c-title { font-family: var(--font-display); font-size: 1.1rem; margin: 0 0 5px; }
.candidates .why {
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .09em;
  text-transform: uppercase; color: var(--faint);
}
.candidates form { margin: 0; }
.candidates button {
  padding: 8px 14px; border-radius: var(--r); cursor: pointer;
  border: 1px solid var(--rule); background: transparent; color: var(--text);
  font-family: var(--font-body); font-size: .88rem;
  transition: border-color .18s var(--ease), color .18s var(--ease), background .18s var(--ease);
}
.candidates button:hover { border-color: var(--gold); color: var(--gold); background: var(--gold-soft); }
@media (max-width: 620px) { .candidates li { grid-template-columns: 1fr; } }

/* ── Open questions ───────────────────────────────────────────────────── */
.oq { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--rule); }
.oq li { margin: 0; }
.oq a {
  display: flex; flex-wrap: wrap; gap: 4px 18px; justify-content: space-between;
  align-items: baseline; padding: 14px 2px; border-bottom: 1px solid var(--rule-soft);
  text-decoration: none; color: var(--text);
  transition: background .2s var(--ease), padding-left .2s var(--ease);
}
.oq a:hover { background: var(--card); padding-left: 10px; }
.oq-q { font-family: var(--font-display); font-size: 1rem; letter-spacing: -.012em; }
.oq-m {
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .09em;
  text-transform: uppercase; color: var(--faint);
}
/* ── Profile: a reader's margin ───────────────────────────────────────── */
.profile-tally {
  display: flex; flex-wrap: wrap; gap: 6px 20px;
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--faint);
}
/* A count and its label are one item. Separators were tried and dropped: a
   wrapped line began with a "·" belonging to the previous item. */
.profile-tally span { white-space: nowrap; }
.profile-tally b { color: var(--text); font-weight: 400; }
.profile-grid {
  display: grid; grid-template-columns: 17rem minmax(0, 1fr);
  gap: clamp(24px, 4vw, 56px); margin: 34px 0 0; padding-top: 26px;
  border-top: 1px solid var(--rule);
}
.profile-side h3, .profile-notes h3 {
  font-family: var(--font-body); font-style: italic; font-weight: 400;
  font-size: 1.02rem; margin: 0 0 14px;
}
.badges { list-style: none; margin: 0 0 22px; padding: 0; display: flex; flex-wrap: wrap; gap: 8px; }
.badges li {
  margin: 0; padding: 5px 12px; border: 1px solid var(--gold);
  border-radius: var(--r); color: var(--gold);
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .12em;
  text-transform: uppercase;
}
.profile-note {
  margin: 0 0 14px; max-width: 40ch; font-size: .88rem; line-height: 1.55;
  color: var(--faint);
}
.profile-note b { color: var(--text); font-family: var(--font-display); font-size: 1.05rem; }
.profile-empty { margin: 0; max-width: 56ch; color: var(--dim); font-style: italic; }
@media (max-width: 760px) {
  .profile-grid { grid-template-columns: 1fr; }
}
/* ── Plans ────────────────────────────────────────────────────────────── */
.plans {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(266px, 1fr));
  gap: clamp(14px, 2vw, 22px); margin: 42px 0 26px;
}
.plan {
  position: relative; display: flex; flex-direction: column;
  padding: 26px 24px 24px; border-radius: var(--r-lg);
  border: 1px solid var(--rule); background: var(--card);
  transition: border-color .25s var(--ease), box-shadow .25s var(--ease);
}
.plan:hover { border-color: color-mix(in srgb, var(--gold) 45%, transparent); box-shadow: var(--shadow-1); }
.plan h3 { font-family: var(--font-body); font-style: italic; font-size: 1rem; font-weight: 400; color: var(--dim); margin-bottom: 10px; }
.plan .price {
  font-family: var(--font-display); font-size: 2.3rem; line-height: 1;
  letter-spacing: -.03em; margin-bottom: 4px;
}
.plan .price small { font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .08em; color: var(--faint); }
.plan .pitch { font-size: .92rem; color: var(--dim); margin: 10px 0 18px; line-height: 1.55; }
.plan ul { list-style: none; margin: 0 0 22px; padding: 0; flex: 1; }
.plan li {
  position: relative; padding: 8px 0 8px 16px; font-size: .92rem; line-height: 1.45;
  color: var(--dim); border-top: 1px solid var(--rule-soft);
}
.plan li::before {
  content: ""; position: absolute; left: 1px; top: 14px;
  width: 4px; height: 4px; background: var(--gold); transform: rotate(45deg);
}
.plan form { display: flex; flex-direction: column; gap: 12px; }
.plan input[type="email"] { padding: 10px 0; border-radius: 0; font-size: .95rem; }
.plan button {
  padding: 11px 18px; border-radius: var(--r); cursor: pointer;
  border: 1px solid var(--btn-bg); background: var(--btn-bg); color: var(--btn-fg);
  font-family: var(--font-body); font-size: .93rem; font-weight: 500;
  transition: transform .18s var(--ease), box-shadow .22s var(--ease);
}
.plan button:hover { transform: translateY(-1px); box-shadow: 0 14px 26px -18px rgba(0, 0, 0, .8); }
.plan.featured {
  background: linear-gradient(157deg, #fdf7e9, var(--paper) 45%, var(--paper-2));
  color: var(--paper-ink); border-color: rgba(110, 78, 28, .4);
  box-shadow: var(--shadow-2);
}
.plan.featured h3 { color: #7b6544; }
.plan.featured .pitch, .plan.featured li { color: #4a3d38; }
.plan.featured li { border-top-color: rgba(110, 78, 28, .2); }
.plan.featured li::before { background: #a67c2c; }
.plan.featured .price { color: #191317; }
.plan.featured .price small { color: #8a6a2c; }
.plan.featured button { background: #191317; border-color: #191317; color: var(--paper); }
.plan.featured input[type="email"] { border-bottom-color: rgba(110, 78, 28, .4); color: #191317; }
.plan.featured input[type="email"]::placeholder { color: #8a7550; }
.plan .ribbon {
  position: absolute; top: 16px; right: 16px;
  font-family: var(--font-mono); font-size: 8px; letter-spacing: .14em;
  text-transform: uppercase; color: #7a2b22;
  border: 1px solid rgba(122, 43, 34, .4); border-radius: var(--r); padding: 3px 7px 2px;
}
.note {
  font-family: var(--font-body); font-size: .87rem; line-height: 1.6;
  color: var(--faint); max-width: 62ch;
}
.plan .note { margin: 10px 0 0; }
.plans-note { margin-top: 22px; }

/* ── Account ──────────────────────────────────────────────────────────── */
.account {
  max-width: 600px; margin: clamp(36px, 6vw, 76px) auto clamp(56px, 8vw, 104px);
  padding: clamp(24px, 3.6vw, 40px);
  border: 1px solid var(--rule); border-radius: var(--r-lg);
  background: var(--card);
}
.account h2 { font-size: clamp(1.6rem, 2.8vw, 2.2rem); margin-bottom: 6px; }
.account .who { color: var(--dim); margin: 0 0 26px; font-style: italic; }
.account .rows { display: grid; gap: 1px; background: var(--rule-soft); border-block: 1px solid var(--rule-soft); margin-bottom: 24px; }
.account .row {
  display: flex; justify-content: space-between; gap: 20px; align-items: baseline;
  background: var(--ink); padding: 13px 2px;
}
.account .row dt {
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .12em;
  text-transform: uppercase; color: var(--dim);
}
.account .row dd { margin: 0; font-family: var(--font-display); font-size: 1rem; }
progress.meter {
  appearance: none; -webkit-appearance: none;
  width: 100%; height: 4px; border: 0; border-radius: 999px;
  background: var(--rule-soft); overflow: hidden; margin: 4px 0 10px;
}
progress.meter::-webkit-progress-bar { background: var(--rule-soft); border-radius: 999px; }
progress.meter::-webkit-progress-value { background: linear-gradient(90deg, var(--oxblood), var(--gold)); border-radius: 999px; }
progress.meter::-moz-progress-bar { background: linear-gradient(90deg, var(--oxblood), var(--gold)); border-radius: 999px; }
.account-actions { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-top: 24px; }
.account-actions form { margin: 0; }

/* ── Message pages ────────────────────────────────────────────────────── */
.message {
  max-width: 540px; margin: clamp(44px, 8vw, 100px) auto clamp(56px, 8vw, 112px);
  text-align: center;
}
.message h2 { font-size: clamp(1.8rem, 3.4vw, 2.5rem); margin-bottom: 12px; line-height: 1.06; }
.message .question { color: var(--dim); font-size: 1.02rem; margin-bottom: 28px; }
.message .ask { text-align: left; }

/* ── Colophon ─────────────────────────────────────────────────────────── */
.colophon {
  position: relative; z-index: 1;
  border-top: 1px solid var(--rule);
  padding-block: clamp(34px, 4.6vw, 56px) clamp(26px, 3vw, 38px);
  margin-top: clamp(20px, 4vw, 44px);
}
.colophon-top {
  display: flex; flex-wrap: wrap; gap: 10px 40px; align-items: baseline;
  justify-content: space-between;
}
.colophon .brand-text {
  font-family: var(--font-display); font-size: 1.16rem; color: var(--text);
  letter-spacing: -.026em; margin: 0;
  font-variation-settings: "SOFT" 40, "WONK" 1;
}
.colophon .colophon-line {
  margin: 0; font-family: var(--font-body); font-style: italic;
  font-size: .93rem; color: var(--dim); max-width: 52ch;
}
.colophon-grid {
  display: grid; grid-template-columns: 1.5fr 1fr 1fr; gap: 22px 40px;
  margin-top: 26px; padding-top: 20px; border-top: 1px solid var(--rule-soft);
}
.colophon-grid h4 {
  font-family: var(--font-mono); font-size: 8.5px; font-weight: 400;
  letter-spacing: .14em; text-transform: uppercase; color: var(--faint);
  margin: 0 0 8px;
}
.colophon-grid ul { list-style: none; margin: 0; padding: 0; }
.colophon-grid li { margin: 0 0 3px; }
.colophon-grid a {
  font-family: var(--font-body); font-size: .92rem; color: var(--dim);
  text-decoration: none;
}
.colophon-grid a:hover { color: var(--gold); }
.colophon-grid p { margin: 0; font-size: .88rem; line-height: 1.55; color: var(--faint); }
.colophon-fine {
  margin: 26px 0 0; padding-top: 14px; border-top: 1px solid var(--rule-soft);
  display: flex; flex-wrap: wrap; gap: 6px 18px; justify-content: space-between;
  font-family: var(--font-mono); font-size: 8.5px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--faint);
}

/* ── Motion ───────────────────────────────────────────────────────────────
   One entrance for the title, one landing for the sheet, one press for the
   seal. Everything else that moves is a response to the reader.            */
.display .line > span {
  transform: translateY(104%);
  animation: line-in .95s var(--ease-out) both;
}
.display .line:nth-child(1) > span { animation-delay: .06s; }
.display .line:nth-child(2) > span { animation-delay: .14s; }
.display .line:nth-child(3) > span { animation-delay: .22s; }
.kicker, .lede, .hero .ask, .index { animation: rise-in .8s var(--ease-out) both; }
.kicker { animation-delay: .02s; }
.lede { animation-delay: .28s; }
.hero .ask { animation-delay: .34s; }
.index { animation-delay: .42s; }
.plate-wrap { animation: land 1.25s var(--ease-out) .12s both; }
.plate-seal { animation: press .9s var(--ease-out) .75s both; }
@keyframes line-in { to { transform: none; } }
@keyframes rise-in { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@keyframes land {
  from { opacity: 0; transform: translateY(30px) scale(.975); }
  to { opacity: 1; transform: none; }
}
@keyframes press {
  0% { opacity: 0; transform: rotate(-12deg) scale(1.28); }
  60% { opacity: 1; }
  100% { opacity: 1; transform: rotate(-12deg) scale(1); }
}

/* Section rules draw themselves once, and the text never moves. */
.rule-draw { transform: scaleX(0); transform-origin: 0 50%; transition: transform 1.1s var(--ease-out); }
.rule-draw.in { transform: scaleX(1); }
.band-head h2, .sec-head h2 { text-wrap: balance; }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .001ms !important; animation-delay: 0s !important;
    transition-duration: .001ms !important; scroll-behavior: auto !important;
  }
  .display .line > span { transform: none; }
  .rule-draw { transform: none; }
  .plate-glare { display: none; }
}

/* ── Forms index: the claim that every form is in scope ──────────────── */
.forms {
  list-style: none; margin: 0; padding: 0;
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: clamp(20px, 3vw, 46px);
}
.forms li {
  display: flex; align-items: baseline; justify-content: space-between; gap: 16px;
  padding: 12px 0; margin: 0; border-top: 1px solid var(--rule-soft);
}
.forms .form {
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .11em;
  text-transform: uppercase; color: var(--faint); white-space: nowrap;
}
.forms form { margin: 0; }
.forms button {
  background: none; border: 0; padding: 0; cursor: pointer;
  font-family: var(--font-display); font-size: .98rem; letter-spacing: -.012em;
  color: var(--text); text-align: right;
  transition: color .18s var(--ease);
}
.forms button:hover { color: var(--gold-hi); text-decoration: underline; text-underline-offset: 3px; }
@media (max-width: 700px) { .forms { grid-template-columns: 1fr; } }

/* ── Follow-ups, feedback ─────────────────────────────────────────────── */
.followups { margin: 34px 0 0; padding-top: 20px; border-top: 1px solid var(--rule-soft); }
.followups h3 {
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .14em;
  text-transform: uppercase; color: var(--dim); margin: 0 0 8px; font-weight: 400;
}
.followups ul { list-style: none; margin: 0; padding: 0; }
.followups li { margin: 0; padding: 9px 0; border-top: 1px solid var(--rule-soft); }
.followups li:first-child { border-top: 0; }
.followups a {
  font-family: var(--font-display); font-size: 1rem; letter-spacing: -.014em;
  text-decoration: none; color: var(--text);
}
.followups a:hover { color: var(--gold-hi); }

.consulted { margin: clamp(34px, 4.5vw, 54px) 0 0; padding-top: 22px; border-top: 1px solid var(--rule); }
.consulted h3 {
  font-family: var(--font-body); font-style: italic; font-weight: 400;
  font-size: 1.02rem; margin: 0 0 8px;
}
.consulted > p { margin: 0 0 14px; max-width: 58ch; font-size: .9rem; color: var(--dim); }
.consulted ul { list-style: none; margin: 0; padding: 0; }
.consulted li {
  margin: 0; padding: 10px 0; border-top: 1px solid var(--rule-soft);
  display: flex; flex-wrap: wrap; gap: 2px 14px; justify-content: space-between;
}
.consulted li:first-child { border-top: 0; padding-top: 0; }
.cons-title { font-size: .95rem; }
.cons-title a { color: var(--text); text-decoration: none; }
.cons-title a:hover { color: var(--gold); }
.cons-meta {
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .09em;
  text-transform: uppercase; color: var(--faint);
}
.margin { margin: clamp(34px, 4.5vw, 54px) 0 0; padding-top: 22px; border-top: 1px solid var(--rule); }
.margin h3 {
  font-family: var(--font-body); font-style: italic; font-weight: 400;
  font-size: 1.02rem; margin: 0 0 12px; color: var(--text);
}
.margin-invite {
  margin: 0 0 18px; max-width: 58ch; font-size: 1rem; line-height: 1.6;
  color: var(--dim);
}
.notes { list-style: none; margin: 0 0 18px; padding: 0; }
.notes li { margin: 0; padding: 14px 0; border-top: 1px solid var(--rule-soft); }
.notes li:first-child { border-top: 0; padding-top: 0; }
.note-body { margin: 0 0 8px; font-family: var(--font-body); font-size: 1rem; }
.notes blockquote {
  margin: 0 0 8px; padding-left: 14px; border-left: 2px solid var(--gold);
  font-family: var(--font-display); font-size: 1.02rem; line-height: 1.5;
  color: var(--dim);
}
.note-src {
  margin: 0; font-family: var(--font-mono); font-size: 9px;
  letter-spacing: .09em; text-transform: uppercase; color: var(--faint);
}
.note-src a { color: inherit; text-decoration: none; }
.note-src a:hover { color: var(--gold); }
.note-flash {
  margin: 0 0 14px; padding: 10px 12px; border-radius: var(--r);
  border: 1px solid var(--rule); font-size: .92rem; color: var(--dim);
}
.add-note summary {
  cursor: pointer; font-family: var(--font-mono); font-size: 9.5px;
  letter-spacing: .12em; text-transform: uppercase; color: var(--dim);
}
.add-note summary:hover { color: var(--gold); }
.add-note form { margin: 16px 0 0; display: grid; gap: 8px; max-width: 620px; }
.add-note label {
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--dim);
}
.add-note textarea, .add-note input, .add-note select {
  width: 100%; padding: 9px 2px; background: transparent;
  border: 0; border-bottom: 1px solid var(--rule-soft);
  color: var(--text); font-family: var(--font-body); font-size: .96rem;
  resize: vertical;
}
.add-note textarea:focus, .add-note input:focus, .add-note select:focus {
  outline: none; border-bottom-color: var(--gold);
}
/* Placeholder and hint are read, not decoration: at --faint they fall under
   4.5:1 on the plate. --dim is the quiet-but-legible ink. */
.add-note textarea::placeholder, .add-note input::placeholder { color: var(--dim); }
.add-note button {
  justify-self: start; margin-top: 6px; padding: 9px 16px; cursor: pointer;
  border: 1px solid var(--rule); border-radius: var(--r); background: transparent;
  color: var(--text); font-family: var(--font-body); font-size: .92rem;
}
.add-note button:hover { border-color: var(--gold); color: var(--gold); }
.note-hint { margin: 4px 0 0; max-width: 54ch; font-size: .84rem; color: var(--dim); }
.share-strip {
  margin: clamp(34px, 4.5vw, 54px) 0 0; padding: 20px 0 0;
  border-top: 1px solid var(--rule);
  display: flex; flex-wrap: wrap; gap: 18px 40px;
  justify-content: space-between; align-items: flex-start;
}
.share-strip h3 {
  font-family: var(--font-body); font-style: italic; font-weight: 400;
  font-size: 1.02rem; margin: 0 0 10px; color: var(--text);
}
.share-links { display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center; margin: 0; }
.share-links a, .share-links button {
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .12em;
  text-transform: uppercase; color: var(--dim); text-decoration: none;
  background: none; border: 0; padding: 0; cursor: pointer;
}
.share-links a:hover, .share-links button:hover { color: var(--gold); }
.share-links .copy-link { margin: 0; }
.endorse { text-align: right; }
.tally { margin: 0 0 10px; font-family: var(--font-body); font-size: .94rem; color: var(--dim); }
.tally b {
  font-family: var(--font-display); font-size: 1.3rem; color: var(--text);
  margin-right: 2px;
}
.endorse button {
  padding: 8px 16px; border: 1px solid var(--rule); border-radius: var(--r);
  background: transparent; color: var(--text); cursor: pointer;
  font-family: var(--font-body); font-size: .9rem;
  transition: border-color .18s var(--ease), color .18s var(--ease), background .18s var(--ease);
}
.endorse button:hover { border-color: var(--gold); color: var(--gold); background: var(--gold-soft); }
.endorsed { margin: 0; font-family: var(--font-body); font-style: italic; color: var(--dim); }
@media (max-width: 620px) {
  .share-strip { display: block; }
  .endorse { text-align: left; margin-top: 18px; }
}
.feedback { margin: clamp(34px, 4.5vw, 54px) 0 0; padding-top: 22px; border-top: 1px solid var(--rule); }
.feedback h3 {
  font-family: var(--font-body); font-style: italic; font-weight: 400;
  font-size: 1.02rem; color: var(--text); margin: 0 0 14px;
}
.feedback form { display: flex; flex-wrap: wrap; gap: 12px 10px; align-items: center; }
.verdicts { display: flex; gap: 8px; flex-wrap: wrap; }
.verdicts button {
  padding: 8px 14px; border-radius: var(--r); cursor: pointer;
  border: 1px solid var(--rule); background: transparent; color: var(--text);
  font-family: var(--font-body); font-size: .9rem;
  transition: border-color .18s var(--ease), color .18s var(--ease), background .18s var(--ease);
}
.verdicts button:hover { border-color: var(--gold); color: var(--gold); background: var(--gold-soft); }
.feedback input[type="text"] {
  flex: 1 1 240px; min-width: 0; padding: 8px 2px;
  background: transparent; border: 0; border-bottom: 1px solid var(--rule-soft);
  color: var(--text); font-family: var(--font-body); font-style: italic; font-size: .95rem;
  transition: border-color .2s var(--ease);
}
.feedback input[type="text"]::placeholder { color: var(--faint); }
.feedback input[type="text"]:focus { outline: none; border-bottom-color: var(--gold); }
.feedback .thanks { color: var(--dim); font-style: italic; margin: 0; max-width: 46ch; }

/* A theme change is a change of light, not a cross-fade. */
::view-transition-old(root), ::view-transition-new(root) {
  animation: none; mix-blend-mode: normal;
}

/* ── Responsive ───────────────────────────────────────────────────────── */
@media (max-width: 1080px) {
  .hero { grid-template-columns: 1fr; gap: 40px; }
  .hero-copy { padding-top: 0; }
  .plate-col { max-width: 520px; width: 100%; }
  .display { font-size: clamp(2.6rem, 9vw, 4.2rem); }
  .band-grid { grid-template-columns: 1fr; gap: 26px; }
  .doc-grid { grid-template-columns: 1fr; }
}
@media (max-width: 620px) {
  .plate { --rx: 3deg; --ry: -6deg; }
  .plate-wrap { padding: 10px 16px 28px; }
  .plate-seal {
    width: 66px; height: 66px; right: 8px; bottom: -16px;
    transform: rotate(-12deg);
  }
  .plate-seal b { font-size: 14px; }
  .plate-foot { display: block; padding-right: 54px; line-height: 1.7; }
  .plate-foot > span { display: block; }
  .copy-link { margin-left: 0; }
  .colophon-grid { grid-template-columns: 1fr 1fr; }
  .colophon-fine { flex-direction: column; }
  .display .line { padding-bottom: .06em; }
}
"""

#: Served at /static/app.js. Kept out of the HTML so the Content-Security-Policy
#: can forbid inline script entirely — a source string that smuggles markup has
#: nowhere to execute.
SCRIPT = """
(function () {
  'use strict';

  var root = document.documentElement;
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var fine = window.matchMedia('(hover: hover) and (pointer: fine)').matches;

  // ── Theme ────────────────────────────────────────────────────────────
  // The OS preference is the default (the CSS media query decides); an
  // explicit choice is remembered and wins. The switch wipes in from the
  // button, because a theme change is a change of light, not of page.
  var toggle = document.getElementById('theme-toggle');
  function effectiveTheme() {
    var set = root.getAttribute('data-theme');
    if (set === 'light' || set === 'dark') return set;
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  function syncToggle() {
    if (!toggle) return;
    var light = effectiveTheme() === 'light';
    toggle.setAttribute('aria-pressed', light ? 'true' : 'false');
    toggle.setAttribute('aria-label', light ? 'Switch to dark theme' : 'Switch to light theme');
  }
  function applyTheme(next) {
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('ace-theme', next); } catch (e) {}
    syncToggle();
  }
  try {
    var stored = localStorage.getItem('ace-theme');
    if (stored === 'light' || stored === 'dark') root.setAttribute('data-theme', stored);
  } catch (e) {}
  syncToggle();
  if (toggle) {
    toggle.addEventListener('click', function () {
      var next = effectiveTheme() === 'light' ? 'dark' : 'light';
      if (reduce || !document.startViewTransition) { applyTheme(next); return; }
      var box = toggle.getBoundingClientRect();
      var x = box.left + box.width / 2;
      var y = box.top + box.height / 2;
      var far = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
      var wipe = document.startViewTransition(function () { applyTheme(next); });
      wipe.ready.then(function () {
        root.animate(
          { clipPath: ['circle(0px at ' + x + 'px ' + y + 'px)',
                       'circle(' + far + 'px at ' + x + 'px ' + y + 'px)'] },
          { duration: 520, easing: 'cubic-bezier(.16,1,.3,1)',
            pseudoElement: '::view-transition-new(root)' }
        );
      }).catch(function () {});
    });
  }

  // ── Masthead: the bar tightens once you are reading ──────────────────
  var masthead = document.querySelector('.masthead');
  var spacer = document.querySelector('.margin-rule span');
  var article = document.querySelector('.doc');
  var queued = false;

  function paint() {
    queued = false;
    if (masthead) masthead.classList.toggle('scrolled', window.scrollY > 10);
    if (!spacer || !article) return;
    var box = article.getBoundingClientRect();
    var height = box.height || 1;
    var seen = (window.innerHeight * 0.55 - box.top) / height;
    var p = Math.min(1, Math.max(0, seen));
    spacer.style.transform = 'scaleY(' + p.toFixed(4) + ')';
  }
  function onScroll() {
    if (queued) return;
    queued = true;
    window.requestAnimationFrame(paint);
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll);
  paint();

  // ── Pin ↔ source: the product's one idea, made physical ──────────────
  // Hovering a numbered pin in the prose lights its row in the source list,
  // and the reverse. No tooltip, no popover: the link is the interaction.
  var pins = document.querySelectorAll('.pins a[data-src]');
  var rows = document.querySelectorAll('.srcs li[data-src-row]');
  if (pins.length && rows.length) {
    var byKey = {};
    Array.prototype.forEach.call(rows, function (row) {
      byKey[row.getAttribute('data-src-row')] = row;
    });
    Array.prototype.forEach.call(pins, function (pin) {
      var row = byKey[pin.getAttribute('data-src')];
      if (!row) return;
      var on = function () { row.classList.add('lit'); pin.classList.add('lit'); };
      var off = function () { row.classList.remove('lit'); pin.classList.remove('lit'); };
      pin.addEventListener('pointerenter', on);
      pin.addEventListener('pointerleave', off);
      pin.addEventListener('focus', on);
      pin.addEventListener('blur', off);
      row.addEventListener('pointerenter', on);
      row.addEventListener('pointerleave', off);
    });
  }

  // ── The plate: a sheet that catches the light ────────────────────────
  Array.prototype.forEach.call(document.querySelectorAll('[data-tilt]'), function (el) {
    var glass = el.querySelector('.plate-glare');
    var wrap = el.parentElement;
    if (!fine || reduce) return;
    var max = parseFloat(el.getAttribute('data-tilt')) || 6;
    var frame = null;
    function apply(x, y) {
      el.style.setProperty('--ry', (x * max).toFixed(2) + 'deg');
      el.style.setProperty('--rx', (-y * max).toFixed(2) + 'deg');
      if (glass) {
        glass.style.setProperty('--mx', ((x + 0.5) * 100).toFixed(1) + '%');
        glass.style.setProperty('--my', ((y + 0.5) * 100).toFixed(1) + '%');
      }
    }
    (wrap || el).addEventListener('pointerenter', function () {
      if (glass) glass.style.opacity = '1';
    });
    (wrap || el).addEventListener('pointermove', function (e) {
      if (frame) return;
      frame = window.requestAnimationFrame(function () {
        var r = el.getBoundingClientRect();
        apply((e.clientX - r.left) / r.width - 0.5, (e.clientY - r.top) / r.height - 0.5);
        frame = null;
      });
    });
    (wrap || el).addEventListener('pointerleave', function () {
      if (glass) glass.style.opacity = '0';
      el.style.setProperty('--ry', '0deg');
      el.style.setProperty('--rx', '0deg');
    });
  });

  // ── Rules draw once, when a section is first seen ────────────────────
  var rules = document.querySelectorAll('.rule-draw');
  if ('IntersectionObserver' in window && !reduce && rules.length) {
    var ruleObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('in');
        ruleObserver.unobserve(entry.target);
      });
    }, { rootMargin: '0px 0px -10% 0px', threshold: 0.15 });
    Array.prototype.forEach.call(rules, function (rule) { ruleObserver.observe(rule); });
  }

  // ── Deepening: expand in place, under the reader's cursor ────────────
  Array.prototype.forEach.call(document.querySelectorAll('button.deepen'), function (b) {
    b.addEventListener('click', function () {
      var target = document.getElementById('deep-' + b.dataset.block);
      if (!target) return;
      if (b.getAttribute('aria-expanded') === 'true') {
        target.innerHTML = '';
        target.hidden = true;
        b.setAttribute('aria-expanded', 'false');
        return;
      }
      b.disabled = true;
      b.setAttribute('aria-busy', 'true');
      fetch(b.dataset.url, { headers: { 'Accept': 'text/html' } })
        .then(function (res) { return res.text(); })
        .then(function (text) {
          target.innerHTML = text;
          target.hidden = false;
          b.setAttribute('aria-expanded', 'true');
        })
        .catch(function () {
          target.innerHTML = '<p class="meta">Could not load the evidence. Try again.</p>';
          target.hidden = false;
        })
        .finally(function () {
          b.disabled = false;
          b.removeAttribute('aria-busy');
        });
    });
  });

  // ── Copy permalink ───────────────────────────────────────────────────
  var copy = document.querySelector('[data-copy]');
  if (copy) {
    copy.addEventListener('click', function () {
      var url = window.location.href;
      var done = function () {
        var label = copy.querySelector('span') || copy;
        var old = label.textContent;
        label.textContent = 'Copied';
        window.setTimeout(function () { label.textContent = old; }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(done, function () {});
      } else {
        done();
      }
    });
  }
})();
"""

HEAD = (
    '<link rel="preload" href="/static/fonts/fraunces-latin.woff2" as="font"'
    ' type="font/woff2" crossorigin>'
    '<link rel="preload" href="/static/fonts/newsreader-latin.woff2" as="font"'
    ' type="font/woff2" crossorigin>'
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
# SHELL
# ═══════════════════════════════════════════════════════════════════════════

BRAND = "Subtext"
TAGLINE = "What a work means, and who said so."

_SUN = (
    '<svg class="glyph sun" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<circle cx="12" cy="12" r="4"/>'
    '<path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
    'M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>'
)
_MOON = (
    '<svg class="glyph moon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a7 7 0 1 0 10.5 10.5Z"/></svg>'
)
_ARROW = (
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="M5 12h14M13 6l6 6-6 6"/></svg>'
)
_COPY = (
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<rect x="9" y="9" width="11" height="11" rx="2"/>'
    '<path d="M5 15V5a2 2 0 0 1 2-2h8"/></svg>'
)
_CHEV = (
    '<svg class="chev" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="M6 9l6 6 6-6"/></svg>'
)


def _nav(active: str = "") -> str:
    def link(href: str, label: str, key: str) -> str:
        current = ' aria-current="page"' if active == key else ""
        return f'<a href="{href}"{current}>{label}</a>'

    return (
        '<header class="masthead"><div class="wrap masthead-in">'
        f'<a class="brand" href="/"><span class="brand-rule" aria-hidden="true"></span>'
        f"{BRAND}</a>"
        f'<p class="masthead-note">{TAGLINE}</p>'
        '<nav class="menu" aria-label="Primary">'
        + link("/", "Ask", "ask")
        + link("/pricing", "Plans", "pricing")
        + link("/account", "Account", "account")
        + "</nav>"
        '<button class="theme-toggle" id="theme-toggle" type="button"'
        ' aria-label="Switch to light theme" aria-pressed="false">'
        + _SUN + _MOON +
        "</button></div></header>"
    )


_FOOTER = f"""<footer class="colophon"><div class="wrap">
  <div class="colophon-top">
    <p class="brand-text">{BRAND}</p>
    <p class="colophon-line">A document of what the dated sources say, where they
      disagree, and what nobody recorded. The margin is part of the reading.</p>
  </div>
  <div class="colophon-grid">
    <div>
      <h4>Read</h4>
      <ul>
        <li><a href="/">Ask about a work</a></li>
        <li><a href="/pricing">Plans &amp; limits</a></li>
        <li><a href="/account">Your account</a></li>
        <li><a href="/api/docs">API</a></li>
      </ul>
    </div>
    <div>
      <h4>Colophon</h4>
      <p>Set in Fraunces and Newsreader (SIL Open Font License), served from this
        machine. No trackers, no third-party fonts, no analytics script.</p>
    </div>
    <div>
      <h4>Method</h4>
      <p>Every sentence carries the number of a dated source. Quoted text appears
        only where the licence allows it; otherwise you get the link.</p>
    </div>
  </div>
  <p class="colophon-fine">
    <span>{BRAND} — a research instrument, not a search engine.</span>
    <span>Sources dated and cited · disagreements left standing</span>
  </p>
</div></footer>"""


def _canonical(path: str) -> str:
    try:
        from src.config import settings

        base = (settings.base_url or "").rstrip("/")
    except Exception:
        base = ""
    return f"{base}{path}"


def _meta_head(
    *, title: str, description: str, path: str, og_type: str,
    published: datetime | None, jsonld: dict | None, noindex: bool,
) -> str:
    """Everything a crawler reads, in one place.

    The canonical URL is built from ACE_BASE_URL so a Railway preview and the
    real domain never compete in an index.
    """
    url = _canonical(path)
    robots = "noindex,nofollow" if noindex else "index,follow,max-image-preview:large"
    parts = [
        f'<link rel="canonical" href="{esc(url)}">',
        f'<meta name="robots" content="{robots}">',
        f'<meta property="og:site_name" content="{BRAND}">',
        f'<meta property="og:type" content="{og_type}">',
        f'<meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        f'<meta property="og:url" content="{esc(url)}">',
        '<meta property="og:locale" content="en_GB">',
        '<meta name="twitter:card" content="summary">',
        f'<meta name="twitter:title" content="{esc(title)}">',
        f'<meta name="twitter:description" content="{esc(description)}">',
    ]
    if published is not None:
        parts.append(
            f'<meta property="article:published_time" content="{published.isoformat()}">'
        )
    if jsonld is not None:
        payload = json.dumps(jsonld, ensure_ascii=False, separators=(",", ":"))
        # A data block, not a script: no execution, so the CSP is untouched.
        payload = payload.replace("</", "<\\/")
        parts.append(f'<script type="application/ld+json">{payload}</script>')
    return "".join(parts)


def shell(
    title: str,
    body: str,
    *,
    description: str = "",
    active: str = "",
    path: str = "/",
    og_type: str = "website",
    published: datetime | None = None,
    jsonld: dict | None = None,
    noindex: bool = False,
) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<meta name="theme-color" content="#07060a" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#efe8d8" media="(prefers-color-scheme: light)">
{_meta_head(title=title, description=description, path=path, og_type=og_type,
            published=published, jsonld=jsonld, noindex=noindex)}
{HEAD}
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
{_nav(active)}
{body}
{_FOOTER}
</body></html>"""


def _site_jsonld() -> dict:
    base = _canonical("/")
    return {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "WebSite",
                "@id": f"{base}#website",
                "url": base,
                "name": BRAND,
                "description": (
                    "A sourced explanation of what a work of art means: dated sources, "
                    "disagreements left standing, and the gaps nobody recorded."
                ),
                "inLanguage": "en",
            },
            {
                "@type": "Organization",
                "@id": f"{base}#publisher",
                "name": BRAND,
                "url": base,
            },
        ],
    }


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
    feedback_sent: bool = False,
    endorsements: int = 0,
    endorsed: bool = False,
    contributions: list | None = None,
    contributed: str = "",
) -> str:
    index = citation_index(answer)
    narrative = [
        b for b in answer.blocks
        if b.kind in (BlockKind.CLOSE_READING, BlockKind.CONTEXT, BlockKind.CLOSE)
    ]
    thesis_block = next((b for b in answer.blocks if b.kind == BlockKind.THESIS), None)
    thesis_html = (
        esc(thesis_block.text) + _pins(thesis_block, index)
        if thesis_block is not None else esc(answer.thesis)
    )
    minutes = max(1, round((answer.word_count() or 1) / 210))
    blocks = "".join(
        # A drop cap opens a document that has an argument. A thin document is a
        # record, and a record does not get a display cap.
        _block_html(
            b, answer, index, pack, sources,
            first=(b is narrative[0] and answer.sufficiency == Sufficiency.SUFFICIENT)
            if narrative else False,
        )
        for b in answer.blocks
        if b.kind != BlockKind.THESIS
    )
    path = f"/a/{answer.id}"

    body = f"""<main id="main"><div class="wrap doc-grid">
<article class="doc">
  <div class="margin-rule" aria-hidden="true"><span></span></div>
  <header class="doc-head">
    <p class="kicker">{_suff_chip(answer)} <span>{esc(_work_type(answer))}</span></p>
    <h1 class="doc-title">{esc(answer.question)}</h1>
    <p class="doc-work">{_work_line(pack, sources)}</p>
    <div class="doc-meta">
      <span>{_source_count(answer, pack)}</span>
      <span class="dot" aria-hidden="true"></span>
      <span>{minutes} min read</span><span class="dot" aria-hidden="true"></span>
      <time datetime="{answer.generated_at:%Y-%m-%d}">{answer.generated_at:%d %B %Y}</time>
      <button class="copy-link" type="button" data-copy aria-label="Copy a link to this document">
        {_COPY}<span>Copy link</span>
      </button>
    </div>
  </header>
  {_sufficiency_banner(answer)}
  <div class="prose">
    <p class="thesis">{thesis_html}</p>
    {blocks}
    {_children_html(children or [])}
  </div>
  {_consulted_html(answer, pack)}
  {_margin_html(answer, contributions or [], contributed)}
  {_share_html(answer, endorsements, endorsed)}
  {_feedback_html(answer, feedback_sent)}
  {_attribution_html(answer, index)}
</article>
<aside class="rail" aria-label="Context">
  {_timeline_html(answer.timeline)}
  {"" if any(b.kind == BlockKind.GAP for b in answer.blocks) else _gaps_html(pack.gaps)}
  {_sources_html(sources, index, answer)}
  <section>
    <h3>Ask a follow-up</h3>
    <form class="ask" method="post" action="{ask_action}">
      <input type="hidden" name="parent_id" value="{esc(answer.id)}">
      <input type="hidden" name="work_id" value="{esc(answer.work_id)}">
      <label class="sr-only" for="follow-{esc(answer.id)}">Your follow-up question</label>
      <input id="follow-{esc(answer.id)}" type="text" name="question"
             placeholder="Push further on…" required maxlength="500">
      <button type="submit"><span>Ask</span>{_ARROW}</button>
    </form>
  </section>
</aside>
</div></main>"""

    return shell(
        f"{answer.question} · {BRAND}",
        body,
        description=answer.thesis[:180],
        path=path,
        og_type="article",
        published=answer.generated_at,
        jsonld=_article_jsonld(answer, pack, sources, index),
    )


def _article_jsonld(
    answer: Answer, pack: ContextPack, sources: dict[str, Source],
    index: dict[str, int],
) -> dict:
    work = None
    try:
        from src.works import get_work

        work = get_work(answer.work_id)
    except Exception:
        work = None

    about: dict = {"@type": "CreativeWork", "name": work.title if work else answer.work_id}
    if work is not None:
        if work.primary_creator:
            about["creator"] = {"@type": "Person", "name": work.primary_creator.name}
        if work.date_created and work.date_created.display:
            about["dateCreated"] = work.date_created.display
        about["@type"] = _schema_type(work.type.value)

    citations = []
    for sid, source in sources.items():
        if sid not in index:
            continue
        entry: dict = {"@type": "CreativeWork", "name": source.title}
        if source.author:
            entry["author"] = {"@type": "Person", "name": source.author}
        if source.url:
            entry["url"] = source.url
        citations.append(entry)

    canonical = _canonical(f"/a/{answer.id}")
    return {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": answer.question,
        "description": answer.thesis[:250],
        "datePublished": answer.generated_at.isoformat(),
        "dateModified": answer.generated_at.isoformat(),
        "inLanguage": "en",
        "isAccessibleForFree": True,
        "about": about,
        "citation": citations[:12],
        "publisher": {"@type": "Organization", "name": BRAND, "url": _canonical("/")},
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        "url": canonical,
    }


def _schema_type(work_type: str) -> str:
    return {
        "MUSIC": "MusicComposition",
        "POETRY": "CreativeWork",
        "VISUAL_ART": "VisualArtwork",
        "LITERATURE": "Book",
        "FILM": "Movie",
        "THEATER": "TheaterEvent",
    }.get(work_type.upper(), "CreativeWork")


def _type_label(value: str) -> str:
    """VISUAL_ART is a constant, "Visual art" is a word."""
    return value.replace("_", " ").capitalize()


def _work_type(answer: Answer) -> str:
    try:
        from src.works import get_work

        work = get_work(answer.work_id)
        return _type_label(work.type.value) if work else "Document"
    except Exception:  # registry is data, never a reason to fail a page
        return "Document"


def _work_line(pack: ContextPack, sources: dict[str, Source]) -> str:
    """Creator — <em>title</em>, the line that anchors the document to an object."""
    try:
        from src.works import get_work

        work = get_work(pack.work_id)
    except Exception:
        work = None
    if work is None:
        primary = next((s for s in sources.values() if s.type.value == "PRIMARY"), None)
        return esc(primary.title) if primary else f"{len(sources)} sources"
    creator = work.primary_creator.name if work.primary_creator else ""
    date = f" · {esc(describe(work.date_created))}" if work.date_created else ""
    who = f"{esc(creator)} — " if creator else ""
    return f"{who}<em>{esc(work.title)}</em>{date}"


def _suff_chip(answer: Answer) -> str:
    labels = {
        Sufficiency.SUFFICIENT: ("chip-ok", "Sufficient evidence"),
        Sufficiency.THIN: ("chip-thin", "Thin evidence"),
        Sufficiency.INSUFFICIENT: ("chip-warn", "Insufficient evidence"),
    }
    klass, text = labels.get(answer.sufficiency, ("chip-thin", "Document"))
    return f'<span class="chip {klass}">{text}</span>'


def _sufficiency_banner(answer: Answer) -> str:
    if answer.sufficiency == Sufficiency.SUFFICIENT:
        return ""
    warn = " warn" if answer.sufficiency == Sufficiency.INSUFFICIENT else ""
    return (
        f'<div class="banner{warn}" role="note"><strong>{answer.sufficiency.value.title()}.</strong>'
        f'<span>{esc(answer.sufficiency_note or "")}</span></div>'
    )


def _consulted_html(answer: Answer, pack: ContextPack) -> str:
    """What was read when nothing could be quoted.

    A refusal that names no source is a dead end: the reader cannot check
    whether we looked, or follow the trail themselves. So the refused document
    lists the documents that were fetched — by title, date and link, with no
    text and no claim attached, because none of them earned one.
    """
    if answer.sufficiency is not Sufficiency.INSUFFICIENT:
        return ""
    rows = []
    for source in pack.sources:
        href = safe_url(source.url)
        title = esc(source.title)
        if href:
            title = f'<a href="{href}" target="_blank" rel="noopener noreferrer">{title} ↗</a>'
        rows.append(
            f'<li><span class="cons-title">{title}</span>'
            f'<span class="cons-meta">{esc(source.author)} · {esc(_date_of(source))} · '
            f"{esc(source.authority.value.capitalize())}</span></li>"
        )
    if not rows:
        return ""
    return f"""<section class="consulted">
  <h3>Read, and not enough</h3>
  <p>These documents were retrieved and read. None of them says enough about this
    work to support an interpretation, so they are listed rather than quoted.</p>
  <ul>{"".join(rows)}</ul>
</section>"""


def _margin_html(answer: Answer, notes: list, contributed: str) -> str:
    """The margin: what readers added, and what the check said about it.

    Accepted notes are shown beside the document, attributed and quoted. They
    are never merged into the thesis — a verified quotation proves the sentence
    exists on that page, not that it supports the reading attached to it.
    """
    flash = {
        "accepted": (
            "Accepted. Your quotation was found on the page you cited, and the "
            "note is now in the margin."
        ),
        "rejected": (
            "Not accepted. The sentence you quoted was not found on that page — "
            "copy it exactly, punctuation included."
        ),
        "pending": (
            "Saved, not yet checked. The page could not be fetched when you sent "
            "it; the note stays pending rather than counting as verified."
        ),
    }.get(contributed, "")

    rows = []
    for note in notes:
        host = ""
        try:
            host = urlparse(note.source_url).netloc
        except ValueError:
            host = ""
        rows.append(
            f'<li><p class="note-body">{esc(note.body)}</p>'
            f'<blockquote>“{esc(note.quote)}”</blockquote>'
            f'<p class="note-src">Verified on {esc(host)} · '
            f'<a href="{safe_url(note.source_url)}" target="_blank" rel="noopener noreferrer">'
            f"read it there ↗</a> · "
            f'<a href="/u/{esc(note.author)}">the contributor</a></p></li>'
        )

    # A refused document is not finished, it is unanswered. The margin is then
    # the page's main action, not an afterthought: nobody has published a
    # reading of this work that we could quote, and a reader who has one is the
    # difference between an empty page and a first reading.
    first_reading = answer.sufficiency is Sufficiency.INSUFFICIENT

    notes_html = (
        f'<ul class="notes">{"".join(rows)}</ul>' if rows
        else (
            '<p class="margin-invite">No one has written this reading yet. If you'
            " have read one — an interview, a review, the artist's own account —"
            " quote it and say what it shows.</p>"
            if first_reading else
            '<p class="meta">No verified margin notes yet. The first one can be yours.</p>'
        )
    )
    message = f'<p class="note-flash">{esc(flash)}</p>' if flash else ""
    heading = "The first reading" if first_reading else "Margin notes"
    summary = "Add a sourced reading" if first_reading else "Add a margin note"
    placeholder = (
        "What does it show that the record above does not?"
        if first_reading else ""
    )

    return f"""<section class="margin" id="margin">
  <h3>{heading}</h3>
  {message}
  {notes_html}
  <details class="add-note"{' open' if first_reading else ''}>
    <summary>{summary}</summary>
    <form method="post" action="/a/{esc(answer.id)}/contribute">
      <label for="note-kind">What are you adding?</label>
      <select id="note-kind" name="kind">
        <option value="context">A context the document missed</option>
        <option value="correction">A correction</option>
      </select>
      <label for="note-body">Your reading, in your own words</label>
      <textarea id="note-body" name="body" rows="3" maxlength="700" required
                {'placeholder="' + placeholder + '"' if placeholder else ''}></textarea>
      <label for="note-quote">The sentence that supports it, copied exactly</label>
      <textarea id="note-quote" name="quote" rows="3" maxlength="700" required
                placeholder="Paste it from the page; it is checked against the page, not judged."></textarea>
      <label for="note-url">The page it comes from</label>
      <input id="note-url" type="url" name="source_url" required
             placeholder="https://…">
      <button type="submit">Send for checking</button>
      <p class="note-hint">The page is fetched and the sentence is looked for in it.
        Found, and the note appears here; not found, and it does not. Nothing is
        judged, only checked.</p>
    </form>
  </details>
</section>"""


def _share_html(answer: Answer, count: int, endorsed: bool) -> str:
    """Pass it on, or put your name behind it.

    Sharing is plain links: an intent URL needs no script and no third-party
    frame, so the strict CSP holds. Endorsement is one per browser and stored
    with the document — the first step of the reputation the site needs, and
    the only social signal that means anything here.
    """
    from urllib.parse import quote

    url = _canonical(f"/a/{answer.id}")
    text = f"{answer.question} — {BRAND}"
    x = f"https://twitter.com/intent/tweet?text={quote(text)}&url={quote(url)}"
    wa = f"https://wa.me/?text={quote(text + ' ' + url)}"
    mail = f"mailto:?subject={quote(answer.question)}&body={quote(url)}"

    if endorsed:
        action = '<p class="endorsed">You stand behind this document.</p>'
    else:
        action = (
            f'<form method="post" action="/a/{esc(answer.id)}/endorse">'
            '<button type="submit">Stand behind it</button></form>'
        )
    tally = (
        f'<p class="tally"><b>{count}</b> {"reader stands" if count == 1 else "readers stand"} '
        "behind this document</p>" if count else
        '<p class="tally">No one has stood behind this yet</p>'
    )

    return f"""<section class="share-strip" id="share">
  <div>
    <h3>Pass it on</h3>
    <p class="share-links">
      <a href="{esc(x)}" target="_blank" rel="noopener noreferrer">X</a>
      <a href="{esc(wa)}" target="_blank" rel="noopener noreferrer">WhatsApp</a>
      <a href="{esc(mail)}">Email</a>
      <button class="copy-link" type="button" data-copy>{_COPY}<span>Copy link</span></button>
    </p>
  </div>
  <div class="endorse">
    {tally}
    {action}
  </div>
</section>"""


def _feedback_html(answer: Answer, sent: bool) -> str:
    """One question, three buttons, an optional line. No account, no modal."""
    if sent:
        return (
            '<section class="feedback" aria-live="polite"><p class="thanks">'
            "Noted — thank you. Feedback decides which works the curation opens next, "
            "and which conflicts need a second reading.</p></section>"
        )
    return f"""<section class="feedback">
  <h3>Did this document answer the question?</h3>
  <form method="post" action="/a/{esc(answer.id)}/feedback">
    <div class="verdicts">
      <button type="submit" name="verdict" value="useful">It did</button>
      <button type="submit" name="verdict" value="partial">Partly</button>
      <button type="submit" name="verdict" value="missed">It missed</button>
    </div>
    <label class="sr-only" for="fb-note">What was missing?</label>
    <input id="fb-note" type="text" name="note" maxlength="300"
           placeholder="What was missing? (optional)">
  </form>
</section>"""


# ═══════════════════════════════════════════════════════════════════════════
# BLOCKS
# ═══════════════════════════════════════════════════════════════════════════

def _pins(block: Block, index: dict[str, int]) -> str:
    nums = sorted({index[sid] for sid in block.source_ids if sid in index})
    if not nums:
        return ""
    links = "".join(f'<a href="#src-{n}" data-src="{n}">{n}</a>' for n in nums)
    return f'<sup class="pins">{links}</sup>'


def _block_html(
    block: Block, answer: Answer, index: dict[str, int],
    pack: ContextPack, sources: dict[str, Source],
    *, first: bool = False,
) -> str:
    if block.kind == BlockKind.QUOTE:
        return (
            f'<figure class="quote">{esc(block.text)}'
            f'<span class="src">Quoted verbatim {_pins(block, index)}</span></figure>'
        )

    if block.kind == BlockKind.CONFLICT:
        cards = "".join(
            _conflict_html(c, sources)
            for c in pack.conflicts if c.id in block.conflict_ids
        )
        return cards

    if block.kind == BlockKind.GAP:
        items = "".join(
            f"<li>{esc(g.description)}</li>"
            for g in pack.gaps if g.id in block.gap_ids
        )
        return f'<div class="gaps"><h3>What is missing</h3><ul>{items}</ul></div>'

    klass = "dropcap" if first and not block.kind == BlockKind.THESIS else ""
    attrs = f' class="{klass}"' if klass else ""
    deep = _deepen_button(block, answer)
    return (
        f'<div class="block"><p{attrs}>{esc(block.text)}{_pins(block, index)}</p>'
        f'{deep}<div class="deepening" id="deep-{esc(block.id)}" hidden></div></div>'
    )


def _deepen_button(block: Block, answer: Answer) -> str:
    if not block.claim_ids and block.kind != BlockKind.QUOTE:
        return ""
    return (
        f'<button class="deepen" type="button" data-block="{esc(block.id)}"'
        f' aria-expanded="false" aria-controls="deep-{esc(block.id)}"'
        f' data-url="/a/{esc(answer.id)}/b/{esc(block.id)}/deepen">'
        f'<span>Go deeper</span>{_CHEV}</button>'
    )


def _conflict_html(conflict: Conflict, sources: dict[str, Source]) -> str:
    readings = conflict.kind == ConflictKind.INTERPRETIVE
    klass = "conflict readings" if readings else "conflict"
    chip = "chip-verdigris" if readings else "chip-warn"
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
        quote = f"<blockquote>“{esc(shown)}”</blockquote>" if shown else ""
        items.append(
            '<div class="pos">'
            f'<p class="pos-value">{esc(position.value_text)}</p>'
            f'<p class="pos-src">{esc(position.authority.value)} · '
            f"{esc(position.source_title)} · {esc(_date_of(source))}</p>"
            f"{quote}</div>"
        )
    verdict = (
        "Unresolved. Both positions stand; neither is deleted."
        if conflict.resolution.value == "UNRESOLVED"
        else f"Preferred: {conflict.resolution.value}."
    )
    return (
        f'<figure class="{klass}"><div class="conflict-head">'
        f'<span class="chip {chip}">{header}</span>'
        f'<p class="conflict-q">{esc(conflict.question.capitalize())}</p></div>'
        f'<div class="positions">{"".join(items)}</div>'
        f'<p class="verdict">{esc(verdict)}</p></figure>'
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
        klass = ' class="undated"' if event.is_undated else ""
        when = event.when.display or "undated"
        detail = f"<em>{esc(event.description)}</em>" if event.description else ""
        rows.append(
            f"<li{klass}><span class=\"when\">{esc(when)}</span>"
            f'<span class="t-title">{esc(event.title)}</span> {detail}</li>'
        )
    return f'<section aria-labelledby="rail-timeline"><h3 id="rail-timeline">Timeline</h3><ol class="timeline">{"".join(rows)}</ol></section>'


def _gaps_html(gaps: list[Gap]) -> str:
    if not gaps:
        return ""
    horizon = [g for g in gaps if g.gap_type != "missing_layer"]
    if not horizon:
        return ""
    items = "".join(f"<li>{esc(g.description)}</li>" for g in horizon)
    return (
        '<section aria-labelledby="rail-gaps"><h3 id="rail-gaps">Open questions</h3>'
        f'<ul class="gaps-list">{items}</ul></section>'
    )


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
            f'<a href="{href}" target="_blank" rel="noopener noreferrer">Read at source ↗</a>'
            if href else ""
        )
        items.append(
            f'<li id="src-{number}" data-src-row="{number}">'
            f'<span class="n" aria-hidden="true">{number}</span>'
            f'<span class="who">{esc(source.author)} — <em>{esc(source.title)}</em>'
            f'<span class="when">{esc(_date_of(source))} · {esc(source.authority.value)}</span>'
            f"{link}</span></li>"
        )
    return (
        '<section aria-labelledby="rail-sources"><h3 id="rail-sources">Sources</h3>'
        f'<ol class="srcs">{"".join(items)}</ol></section>'
    )


def _attribution_html(answer: Answer, index: dict[str, int]) -> str:
    if not answer.attribution:
        return ""
    items = []
    for entry in answer.attribution:
        number = index.get(entry.source_id, 0)
        credit = f'<span class="credit">{esc(entry.attribution_text)}</span>' if entry.attribution_text else ""
        items.append(
            f"<li><strong>{number}</strong><span>{esc(entry.author)}, <em>{esc(entry.title)}</em>"
            f" ({esc(entry.date_display)})"
            + (f" · {esc(entry.license_id)}" if entry.license_id else "")
            + credit + "</span></li>"
        )
    return (
        '<div class="attribution"><h3>Attribution</h3>'
        f'<ul>{"".join(items)}</ul>'
        f"<p>Assembled by {esc(answer.model)} on {answer.generated_at:%Y-%m-%d}. "
        "Quotation limits were applied per source licence.</p></div>"
    )


def _children_html(children: list[Answer]) -> str:
    if not children:
        return ""
    items = "".join(
        f'<li><a href="/a/{esc(c.id)}">{esc(c.question)}</a></li>' for c in children
    )
    return (
        '<section class="followups"><h3>Follow-ups on this document</h3>'
        f"<ul>{items}</ul></section>"
    )


# ═══════════════════════════════════════════════════════════════════════════
# DEEPENING FRAGMENT
# ═══════════════════════════════════════════════════════════════════════════

def deepening_fragment(
    deepening: Deepening, sources: dict[str, Source]
) -> str:
    parts: list[str] = []
    for quote in deepening.quotes:
        parts.append(
            f'<figure class="quote">{esc(quote.text)}'
            f'<span class="src">{esc(quote.author)} — <em>{esc(quote.source_title)}</em>'
            f" ({esc(quote.date_display)}) · {esc(quote.authority.value)}</span></figure>"
        )
    if not parts:
        parts.append(
            '<p class="meta">No quotable text for this block — the sources behind it are '
            "link-only under their licences.</p>"
        )
    for conflict in deepening.conflicts:
        parts.append(_conflict_html(conflict, sources))
    for gap in deepening.gaps:
        parts.append(f'<p class="meta">Gap — {esc(gap.description)}</p>')
    if deepening.context:
        titles = ", ".join(esc(s.title) for s in deepening.context)
        parts.append(f'<p class="meta">Context sources — {titles}</p>')
    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# INDEX / CANDIDATES
# ═══════════════════════════════════════════════════════════════════════════

_PLATE_FALLBACK = {
    "question": "Why does the second movement of Op. 111 sound so plain?",
    "thesis": (
        "Late Beethoven strips the variation of its ornaments and leaves the theme "
        "almost bare; the plainness is the argument, not a failure of invention."
    ),
    "sources": ["Adorno, Beethoven · 1998", "Kinderman, Beethoven · 1995",
                "Tovey, Essays in Musical Analysis · 1935"],
    "work": "Beethoven — Piano Sonata No. 32, Op. 111",
}

#: The examples on the landing page are the product's claim about itself: every
#: kind of work is in scope, not just the concert hall.
_FORMS = [
    ("Rap &amp; hip-hop", "Booba, Paname"),
    ("Classical", "Beethoven, Op. 111"),
    ("Song", "Frank Ocean, Pyramids"),
    ("Poetry", "Le Bateau ivre"),
    ("Painting", "Las Meninas"),
    ("Film", "Mulholland Drive"),
]


def _plate(recent: list[Answer]) -> str:
    """The hero artefact: a real document, rendered small.

    Using an actual answer instead of lorem keeps the promise honest — the
    thing on the landing page is the thing the product makes.
    """
    # The newest document may be a thin one; the landing page should show the
    # thing the product is proud of, not the thing it apologises for.
    lead = next(
        (a for a in recent if a.sufficiency == Sufficiency.SUFFICIENT),
        recent[0] if recent else None,
    )
    if lead is not None:
        question = lead.question
        thesis = lead.thesis
        chip = _suff_chip(lead)
        href = f"/a/{esc(lead.id)}"
        n = len(lead.source_ids)
        title = _plate_title(lead)
        rows = "".join(
            f'<li><span class="n">{i}</span><span>{esc(ev.title)}'
            f'<span class="when"> · {esc(ev.when.display or "undated")}</span></span></li>'
            for i, ev in enumerate(lead.timeline[:3], start=1)
        )
        if not rows:
            rows = (
                '<li><span class="n">1</span><span>Dated sources stored with the document</span></li>'
            )
        foot = f"{n} source{'s' if n != 1 else ''} · {lead.generated_at:%b %Y}"
    else:
        question = _PLATE_FALLBACK["question"]
        thesis = _PLATE_FALLBACK["thesis"]
        chip = '<span class="chip chip-ok">Sufficient evidence</span>'
        href = "/"
        title = _PLATE_FALLBACK["work"]
        rows = "".join(
            f'<li><span class="n">{i}</span><span>{esc(t)}</span></li>'
            for i, t in enumerate(_PLATE_FALLBACK["sources"], start=1)
        )
        foot = "A document, not a summary"

    return f"""<div class="plate-wrap">
  <div class="plate" data-tilt="5">
    <div class="plate-glare" aria-hidden="true"></div>
    <div class="plate-head">{chip}<span class="label">Document</span></div>
    <a class="plate-link" href="{href}"><p class="plate-q">{esc(question)}</p></a>
    <p class="plate-thesis">{esc(thesis[:260])}{'…' if len(thesis) > 260 else ''}</p>
    <ul class="plate-srcs">{rows}</ul>
    <div class="plate-foot"><span>{esc(title)}</span><span>{esc(foot)}</span></div>
    <div class="plate-seal" aria-hidden="true"><span><b>S</b>sourced<br>not summarised</span></div>
  </div>
  <div class="plate-shadow" aria-hidden="true"></div>
</div>
<p class="plate-note">The sheet above is a real document, not a mock-up —
  <a href="{href}">open it</a>.</p>"""


def _plate_title(answer: Answer) -> str:
    try:
        from src.works import get_work

        work = get_work(answer.work_id)
        if work:
            creator = work.primary_creator.name if work.primary_creator else ""
            return f"{creator} — {work.title}" if creator else work.title
    except Exception:
        pass
    return BRAND


def _index_rows(recent: list[Answer]) -> str:
    """Recently written documents, as a table of contents under the field."""
    rows = []
    seen: set[str] = set()
    for answer in recent:
        if answer.work_id in seen:
            continue
        seen.add(answer.work_id)
        rows.append(
            f'<li><a class="index-link" href="/a/{esc(answer.id)}">'
            f'<span class="n">{len(rows) + 1:02d}</span>'
            f'<span class="t">{esc(answer.question)}'
            f'<span class="w">{esc(_plate_title(answer))}</span></span>'
            f'<span class="arrow" aria-hidden="true">→</span></a></li>'
        )
        if len(rows) == 4:
            break
    if not rows:
        return ""
    return (
        '<div class="index"><div class="index-head">'
        '<span class="label">Already written</span>'
        '<a href="#method">How a document is made</a></div>'
        f'<ol>{"".join(rows)}</ol></div>'
    )


def _forms_html(ask_action: str) -> str:
    items = "".join(
        f'<li><span class="form">{label}</span>'
        f'<form method="post" action="{ask_action}">'
        f'<input type="hidden" name="question" value="{esc(sample)}">'
        f'<button type="submit">{esc(sample)}</button></form></li>'
        for label, sample in _FORMS
    )
    return f'<ul class="forms">{items}</ul>'


def _ledger_sources(answer: Answer) -> str:
    """A refusal has no citations; "0 sources" would read as "nothing exists"."""
    if answer.sufficiency is Sufficiency.INSUFFICIENT:
        return "Unanswered"
    n = len(answer.source_ids)
    return f"{n} source{'' if n == 1 else 's'}"


def _ledger(recent: list[Answer]) -> str:
    rows = []
    seen: set[tuple[str, str]] = set()
    for a in recent:
        key = (a.work_id, a.question.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        if len(rows) == 8:
            break
        i = len(rows) + 1
        rows.append(
            f'<li><a class="doc-row" href="/a/{esc(a.id)}">'
            f'<span class="i">{i:02d}</span>'
            f'<span class="q">{esc(a.question)}</span>'
            f'<span class="w">{esc(_plate_title(a))}</span>'
            f'<time class="d" datetime="{a.generated_at:%Y-%m-%d}">'
            f"{a.generated_at:%d %b %Y}</time>"
            f'<span class="s">{_ledger_sources(a)}</span></a></li>'
        )
    if not rows:
        return (
            '<ul class="ledger"><li><div class="doc-empty">'
            '<span class="i">—</span><span>No documents yet. Ask the first question '
            "above; the document you get is what this page will list.</span></div></li></ul>"
        )
    return f'<ul class="ledger">{"".join(rows)}</ul>'


def index_page(recent: list[Answer], ask_action: str = "/ask") -> str:
    body = f"""<main id="main">
<div class="wrap"><section class="hero">
  <div class="hero-copy">
    <p class="kicker">Rap, sonata, poem, canvas — one method</p>
    <h1 class="display">
      <span class="line"><span>What a work</span></span>
      <span class="line"><span>means, <em>and who</em></span></span>
      <span class="line"><span><em>said so.</em></span></span>
    </h1>
    <p class="lede">Name a track, a sonata, a poem, a painting. You get a document,
      not a summary: what the dated sources claim, when they claimed it, where
      they disagree, and <b>what nobody ever recorded</b>.</p>
    <form class="ask" method="post" action="{ask_action}">
      <label class="sr-only" for="q">The work, and the question you have about it</label>
      <input id="q" type="text" name="question" required maxlength="500"
             autocomplete="off" placeholder="Booba, Paname — what is he saying about Paris?">
      <button type="submit"><span>Ask</span>{_ARROW}</button>
    </form>
    {_index_rows(recent)}
  </div>
  <div class="plate-col">{_plate(recent)}</div>
</section></div>

<section class="band"><div class="wrap"><div class="band-grid">
  <div class="band-head">
    <h2>Every kind of work has a context.</h2>
    <p>A rap verse carries a neighbourhood, a sample and a feud. A sonata carries
      a patron and a premiere. The method is the same; only the sources differ.</p>
  </div>
  {_forms_html(ask_action)}
</div></div></section>

<section class="band" id="method"><div class="wrap"><div class="band-grid">
  <div class="band-head">
    <h2>Not a chatbot. A document.</h2>
    <p>Answers are assembled from sources, not generated from memory. Every
      sentence carries the number of the source that supports it.</p>
  </div>
  <ol class="clauses">
    <li><h3>Say the work, not the vibe</h3>
      <p>The engine identifies the work — by name, by identifier, by a pasted
        Wikipedia or Wikidata link — before it says anything about it. If it
        cannot, it asks instead of guessing.</p></li>
    <li><h3>Read what was written, and when</h3>
      <p>Dated, ranked, licensed. Where a licence forbids quotation you get a
        link, never a paraphrase dressed as a quote.</p></li>
    <li><h3>Leave the disagreement standing</h3>
      <p>Two readings that contradict each other are both printed with their
        dates. Nothing is averaged into false consensus, and a gap is reported
        as a gap.</p></li>
  </ol>
</div></div></section>

<section class="band"><div class="wrap">
  <div class="sec-head"><h2>Recently written</h2><a href="/pricing">Plans &amp; limits →</a></div>
  {_ledger(recent)}
</div></section>

{_open_questions(recent)}
</main>"""

    return shell(
        f"{BRAND} — what a work of art means, and who said so",
        body,
        description=(
            "Name a track, a sonata, a poem or a painting and get a sourced document: "
            "dated evidence, disagreements left standing, and the gaps nobody recorded."
        ),
        path="/",
        jsonld=_site_jsonld(),
        active="ask",
    )


def _source_count(answer: Answer, pack: ContextPack) -> str:
    """"0 sources" on a refusal is a lie of omission: three were read.

    A document cites what it used. A refusal used nothing and read something,
    and the header should say which of the two it is.
    """
    if answer.sufficiency is Sufficiency.INSUFFICIENT:
        n = len(pack.sources)
        return f"{n} read, none usable" if n else "nothing found"
    n = len(answer.source_ids)
    return f"{n} source{'' if n == 1 else 's'}"


def _open_questions(recent: list[Answer]) -> str:
    """The works nobody has read yet, shown as an invitation rather than a gap.

    A refusal is the most useful thing the engine produces: it is a precise
    statement that a work exists and nobody has written about it in a form we
    can quote. Hiding those documents would waste the only signal we have about
    where the corpus should grow — and the engine's whole social premise is that
    a reader can supply the reading.
    """
    waiting: list[Answer] = []
    seen: set[tuple[str, str]] = set()
    for answer in recent:
        if answer.sufficiency is not Sufficiency.INSUFFICIENT:
            continue
        key = (answer.work_id, answer.question.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        waiting.append(answer)
        if len(waiting) == 5:
            break
    if not waiting:
        return ""
    rows = "".join(
        f'<li><a href="/a/{esc(a.id)}">'
        f'<span class="oq-q">{esc(a.question)}</span>'
        f'<span class="oq-m">{esc(_work_type(a))} · '
        f'{a.generated_at:%d %b %Y}</span></a></li>'
        for a in waiting
    )
    return f"""<section class="band" id="open-questions"><div class="wrap">
  <div class="band-grid">
    <div class="band-head">
      <h2>Nobody has explained these yet.</h2>
      <p>For each of these the engine found the work and read what exists, and
        nothing said what it means. One sourced reading is enough to start — an
        interview, a review, a line from the artist. It is checked against the
        page it comes from, then it stays.</p>
    </div>
    <ul class="oq">{rows}</ul>
  </div>
</div></section>"""


def _suff_word(answer: Answer) -> str:
    labels = {
        Sufficiency.SUFFICIENT: "Sufficient",
        Sufficiency.THIN: "Thin",
        Sufficiency.INSUFFICIENT: "Insufficient",
    }
    return labels.get(answer.sufficiency, "Document")


def candidates_page(question: str, candidates, ask_action: str = "/ask") -> str:
    def row(c) -> str:
        # "— unknown" is not information. A candidate with no recorded maker says
        # what it is and where it came from; the rest is left out.
        who = f" — {esc(c.creator)}" if c.creator and c.creator != "unknown" else ""
        when = f" · {esc(c.date_display)}" if c.date_display else ""
        return (
            f'<li><div><p class="c-title">{esc(c.title)}{who}</p>'
            f'<p class="why">{esc(_type_label(c.work_type.value))}{when} · {esc(c.matched_on)}</p></div>'
            f'<form method="post" action="{ask_action}">'
            f'<input type="hidden" name="question" value="{esc(question)}">'
            f'<input type="hidden" name="work_id" value="{esc(c.work_id)}">'
            f'<button type="submit">Use this work</button></form></li>'
        )

    items = "".join(row(c) for c in candidates[:6])
    if not items:
        items = (
            '<li><div><p class="c-title">Nothing matches that yet.</p>'
            '<p class="why">Name the work and its author, for example “Booba, Paname” '
            "or “Le Bateau ivre” — or paste a Wikipedia, Wikidata or Genius link.</p></div></li>"
        )

    body = f"""<main id="main"><div class="wrap">
<header class="page-head">
  <p class="kicker">Several works fit</p>
  <h2>Which work did you mean?</h2>
  <p>{esc(question)}</p>
</header>
<ul class="candidates">{items}</ul>
</div></main>"""
    return shell(
        f"Which work did you mean? · {BRAND}",
        body,
        description="Pick the work you meant; the engine will not guess.",
        path="/ask",
        noindex=True,
        active="ask",
    )


# ═══════════════════════════════════════════════════════════════════════════
# PROFILE — the margin, per reader
# ═══════════════════════════════════════════════════════════════════════════

def _reader_name(author: str) -> str:
    """A visitor id is not a name, and printing it raw is not a profile.

    An account has an opaque id too, so there is nothing to show until someone
    chooses a handle. Until then: who they are, plus enough of the id that two
    readers can be told apart.
    """
    if author.startswith("anon_"):
        return f"Anonymous reader · {author[5:9]}"
    if author.startswith("usr_"):
        return f"Reader · {author[4:8]}"
    return author[:18]


def profile_page(
    author: str,
    contributions: list,
    *,
    accepted: int,
    endorsements: int,
    questions: dict[str, str] | None = None,
) -> str:
    """A public margin: what this reader added, and what passed the check.

    Only accepted notes are printed. A rejected note is counted so the tally is
    honest, but publishing unverified text under someone's id would punish the
    contributor with the very claim we refused to print, and publishing a
    pending one would show a claim the site cannot stand behind.

    Badges are derived from the same counts on every render. There is no award
    event to go stale or to be gamed: one more accepted note *is* the badge.
    """
    from src.contributions import badges

    questions = questions or {}
    earned = badges(accepted, endorsements)
    pending = sum(1 for c in contributions if c.status.value == "PENDING")
    rejected = sum(1 for c in contributions if c.status.value == "REJECTED")
    passed = [c for c in contributions if c.status.value == "ACCEPTED"]

    def note_row(note) -> str:
        question = questions.get(note.answer_id, "")
        link = (
            f'<a href="/a/{esc(note.answer_id)}">{esc(question)}</a>'
            if question else f'<a href="/a/{esc(note.answer_id)}">the document</a>'
        )
        when = note.created_at.strftime("%d %b %Y") if note.created_at else ""
        return (
            '<li><p class="note-body">' + esc(note.body) + "</p>"
            f'<blockquote>“{esc(note.quote)}”</blockquote>'
            f'<p class="note-src">On {link} · {esc(when)}</p></li>'
        )

    notes_html = (
        f'<ul class="notes">{"".join(note_row(n) for n in passed)}</ul>'
        if passed else
        '<p class="profile-empty">No verified note yet. A note appears here once '
        "the sentence it quotes is found on the page it cites.</p>"
    )

    badge_html = (
        '<ul class="badges">'
        + "".join(f"<li>{esc(b)}</li>" for b in earned)
        + "</ul>"
        if earned else
        '<p class="profile-empty">No badge yet. The first is earned by one '
        "accepted note.</p>"
    )

    tally = "".join(
        f'<span><b>{n}</b> {label}</span>'
        for n, label in (
            (accepted, "accepted"),
            (len(passed), "shown"),
            (pending, "awaiting check"),
            (rejected, "refused"),
        )
    )
    body = f"""<main id="main"><div class="wrap">
<header class="page-head">
  <p class="kicker">The margin</p>
  <h2>{esc(_reader_name(author))}</h2>
  <p class="profile-tally">{tally}</p>
</header>
<div class="profile-grid">
  <section class="profile-side" aria-labelledby="badges-h">
    <h3 id="badges-h">Standing</h3>
    {badge_html}
    <p class="profile-note"><b>{endorsements}</b>
      endorsement{'' if endorsements == 1 else 's'} received on documents this
      reader contributed to.</p>
    <p class="profile-note">A note is accepted when the sentence it quotes is
      found, verbatim, on the page it cites. Nothing is judged; only checked.</p>
  </section>
  <section class="profile-notes" aria-labelledby="notes-h">
    <h3 id="notes-h">Notes in the margin</h3>
    {notes_html}
  </section>
</div>
</div></main>"""

    return shell(
        f"{_reader_name(author)} · {BRAND}",
        body,
        description=(
            "A reader's verified margin notes on works of art: what they added, "
            "and the sourced sentence that supports each note."
        ),
        path=f"/u/{author}",
        noindex=True,
        active="",
    )
