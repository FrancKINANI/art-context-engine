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

/* ── Tokens: the museum at night ─────────────────────────────────────── */
:root {
  color-scheme: dark;
  --ink: #08070a;
  --ink-1: #0b0910;
  --ink-2: #12101a;
  --ink-3: #1a1622;
  --text: #efe6d4;
  --dim: #a79b86;
  --faint: #6d6355;
  --gold: #d3a75f;
  --gold-hi: #f2d394;
  --gold-soft: rgba(211, 167, 95, .14);
  --rule: rgba(211, 167, 95, .16);
  --rule-soft: rgba(239, 230, 212, .08);
  --oxblood: #a63a4a;
  --oxblood-hi: #d97684;
  --verdigris: #6aa895;
  --card: rgba(255, 247, 232, .035);
  --card-hi: rgba(255, 247, 232, .07);
  --paper: #f6eedd;
  --paper-2: #eadfc8;
  --paper-ink: #1b1417;
  --shadow-1: 0 1px 2px rgba(0, 0, 0, .5), 0 14px 34px -18px rgba(0, 0, 0, .9);
  --shadow-2: 0 2px 4px rgba(0, 0, 0, .45), 0 40px 80px -40px rgba(0, 0, 0, .95);
  --nav-h: 70px;
  --wrap: 1240px;
  --ease: cubic-bezier(.2, .7, .2, 1);
  --font-display: "Fraunces", ui-serif, Georgia, "Times New Roman", serif;
  --font-body: "Newsreader", ui-serif, Georgia, serif;
  --font-mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  --btn-bg: #d3a75f;
  --btn-fg: #150e07;
  --r: 16px;
}

/* Light theme. Kept in sync with the media-query block below: the attribute
   wins when the reader has chosen, the OS preference is the default. */
:root[data-theme="light"] {
  color-scheme: light;
  --ink: #f2ebdc;
  --ink-1: #f7f1e4;
  --ink-2: #fffdf7;
  --ink-3: #ece1cc;
  --text: #1a1519;
  --dim: #5f564a;
  --faint: #8b8175;
  --gold: #7d5719;
  --gold-hi: #a67c2c;
  --gold-soft: rgba(125, 87, 25, .1);
  --rule: rgba(60, 42, 20, .18);
  --rule-soft: rgba(26, 21, 25, .08);
  --oxblood: #8d2b3b;
  --oxblood-hi: #a63a4a;
  --verdigris: #2f6b5c;
  --card: #fffdf7;
  --card-hi: #ffffff;
  --shadow-1: 0 1px 2px rgba(60, 45, 25, .14), 0 12px 26px -16px rgba(60, 45, 25, .4);
  --shadow-2: 0 2px 6px rgba(60, 45, 25, .16), 0 40px 80px -40px rgba(60, 45, 25, .5);
  --btn-bg: #241d20;
  --btn-fg: #f7f1e4;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) {
    color-scheme: light;
    --ink: #f2ebdc;
    --ink-1: #f7f1e4;
    --ink-2: #fffdf7;
    --ink-3: #ece1cc;
    --text: #1a1519;
    --dim: #5f564a;
    --faint: #8b8175;
    --gold: #7d5719;
    --gold-hi: #a67c2c;
    --gold-soft: rgba(125, 87, 25, .1);
    --rule: rgba(60, 42, 20, .18);
    --rule-soft: rgba(26, 21, 25, .08);
    --oxblood: #8d2b3b;
    --oxblood-hi: #a63a4a;
    --verdigris: #2f6b5c;
    --card: #fffdf7;
    --card-hi: #ffffff;
    --shadow-1: 0 1px 2px rgba(60, 45, 25, .14), 0 12px 26px -16px rgba(60, 45, 25, .4);
    --shadow-2: 0 2px 6px rgba(60, 45, 25, .16), 0 40px 80px -40px rgba(60, 45, 25, .5);
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
  line-height: 1.65;
  font-optical-sizing: auto;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
  overflow-x: hidden;
}
img, svg { max-width: 100%; }
h1, h2, h3, h4 {
  font-family: var(--font-display);
  font-weight: 400;
  font-variation-settings: "SOFT" 28, "WONK" 1;
  letter-spacing: -.02em;
  line-height: 1.1;
  margin: 0;
  text-wrap: balance;
}
p { margin: 0 0 1.1em; text-wrap: pretty; }
a { color: var(--gold); text-decoration-color: color-mix(in srgb, var(--gold) 40%, transparent); text-underline-offset: .18em; }
a:hover { color: var(--gold-hi); }
::selection { background: var(--gold); color: #120d07; }
:focus-visible { outline: 2px solid var(--gold); outline-offset: 3px; border-radius: 3px; }
button { font: inherit; color: inherit; }
[hidden] { display: none !important; }

/* ── Atmosphere: grain, light, a single continuous canvas ─────────────── */
body::before {
  content: "";
  position: fixed;
  inset: -20% -10% -10%;
  z-index: 0;
  pointer-events: none;
  background:
    radial-gradient(58% 42% at 18% 4%, rgba(211, 167, 95, .13), transparent 68%),
    radial-gradient(48% 38% at 88% 12%, rgba(166, 58, 74, .12), transparent 70%),
    radial-gradient(70% 60% at 50% 108%, rgba(106, 168, 149, .07), transparent 70%);
}
:root[data-theme="light"] body::before {
  background:
    radial-gradient(58% 42% at 18% 4%, rgba(211, 167, 95, .3), transparent 68%),
    radial-gradient(48% 38% at 88% 12%, rgba(166, 58, 74, .14), transparent 70%),
    radial-gradient(70% 60% at 50% 108%, rgba(106, 168, 149, .1), transparent 70%);
}
.grain {
  position: fixed;
  inset: 0;
  z-index: 40;
  pointer-events: none;
  opacity: .038;
  mix-blend-mode: overlay;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='180' height='180' filter='url(%23n)'/%3E%3C/svg%3E");
}

/* ── Layout ────────────────────────────────────────────────────────────── */
.wrap {
  width: 100%;
  max-width: var(--wrap);
  margin-inline: auto;
  padding-inline: clamp(18px, 4vw, 44px);
}
main { position: relative; z-index: 1; }
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
}
.skip {
  position: fixed; top: -100px; left: 16px; z-index: 100;
  background: var(--btn-bg); color: var(--btn-fg); padding: 10px 16px;
  border-radius: 6px; font: 600 13px/1 var(--font-mono); letter-spacing: .04em;
}
.skip:focus { top: 12px; }

/* ── Small type: labels, eyebrows, chips ──────────────────────────────── */
.eyebrow, .label, .kicker {
  font-family: var(--font-mono);
  font-size: 10.5px;
  letter-spacing: .18em;
  text-transform: uppercase;
  color: var(--dim);
}
.eyebrow { display: flex; align-items: center; gap: 12px; margin: 0 0 18px; }
.eyebrow::before {
  content: ""; width: 34px; height: 1px; flex: none;
  background: linear-gradient(90deg, var(--gold), transparent);
}
.chip {
  display: inline-flex; align-items: center; gap: 7px;
  font-family: var(--font-mono); font-size: 10px; letter-spacing: .14em;
  text-transform: uppercase; padding: 5px 10px 4px; border-radius: 999px;
  border: 1px solid var(--rule); color: var(--dim); background: var(--card);
  white-space: nowrap;
}
.chip::before {
  content: ""; width: 5px; height: 5px; border-radius: 50%;
  background: currentColor; flex: none;
}
.chip-ok { color: var(--gold); border-color: color-mix(in srgb, var(--gold) 45%, transparent); }
.chip-thin { color: var(--dim); }
.chip-warn { color: var(--oxblood-hi); border-color: color-mix(in srgb, var(--oxblood) 55%, transparent); }
.chip-verdigris { color: var(--verdigris); border-color: color-mix(in srgb, var(--verdigris) 45%, transparent); }

/* ── Progress ─────────────────────────────────────────────────────────── */
.progress {
  position: fixed; inset: 0 0 auto; height: 2px; z-index: 60;
  background: transparent; pointer-events: none;
}
.progress span {
  display: block; height: 100%; transform: scaleX(0); transform-origin: 0 50%;
  background: linear-gradient(90deg, var(--oxblood), var(--gold) 55%, var(--gold-hi));
  box-shadow: 0 0 12px rgba(211, 167, 95, .5);
}

/* ── Nav ──────────────────────────────────────────────────────────────── */
.nav {
  position: sticky; top: 0; z-index: 50; height: var(--nav-h);
  display: flex; align-items: center;
  background: rgba(8, 7, 10, .74);
  -webkit-backdrop-filter: blur(16px) saturate(140%);
  backdrop-filter: blur(16px) saturate(140%);
  border-bottom: 1px solid var(--rule-soft);
}
:root[data-theme="light"] .nav { background: rgba(247, 241, 228, .8); }
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) .nav { background: rgba(247, 241, 228, .8); }
}
.nav-in {
  width: 100%; max-width: var(--wrap); margin-inline: auto;
  padding-inline: clamp(18px, 4vw, 44px);
  display: flex; align-items: center; gap: 22px;
}
.wordmark {
  display: inline-flex; align-items: center; gap: 11px;
  text-decoration: none; color: var(--text); flex: none;
}
.mark {
  width: 31px; height: 31px; border-radius: 50%;
  display: grid; place-items: center;
  font-family: var(--font-display); font-size: 17px; font-weight: 600;
  color: #170f07;
  background: radial-gradient(circle at 32% 24%, #f7e0b2, #cfa457 52%, #8a6322);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .55), 0 4px 14px -4px rgba(211, 167, 95, .6);
  padding-bottom: 1px;
}
.wordmark .word {
  font-family: var(--font-display); font-size: 16px; letter-spacing: -.01em;
  font-variation-settings: "SOFT" 20;
}
.nav-links { display: flex; align-items: center; gap: 4px; margin-left: auto; }
.nav-links a {
  position: relative; padding: 8px 12px; border-radius: 8px;
  font-family: var(--font-mono); font-size: 10.5px; letter-spacing: .14em;
  text-transform: uppercase; text-decoration: none; color: var(--dim);
  transition: color .2s var(--ease);
}
.nav-links a::after {
  content: ""; position: absolute; left: 12px; right: 12px; bottom: 4px; height: 1px;
  background: var(--gold); transform: scaleX(0); transform-origin: 0 50%;
  transition: transform .3s var(--ease);
}
.nav-links a:hover, .nav-links a[aria-current="page"] { color: var(--text); }
.nav-links a:hover::after, .nav-links a[aria-current="page"]::after { transform: scaleX(1); }
.theme-toggle {
  width: 34px; height: 34px; flex: none; display: grid; place-items: center;
  border-radius: 50%; border: 1px solid var(--rule); background: var(--card);
  color: var(--dim); cursor: pointer;
  transition: color .2s var(--ease), border-color .2s var(--ease), transform .3s var(--ease);
}
.theme-toggle:hover { color: var(--gold); border-color: var(--gold); transform: rotate(18deg); }
.theme-toggle .glyph { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; }
.theme-toggle .moon { display: none; }
:root[data-theme="light"] .theme-toggle .sun { display: none; }
:root[data-theme="light"] .theme-toggle .moon { display: block; }
:root[data-theme="dark"] .theme-toggle .sun { display: block; }
:root[data-theme="dark"] .theme-toggle .moon { display: none; }
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) .theme-toggle .sun { display: none; }
  :root:not([data-theme="dark"]) .theme-toggle .moon { display: block; }
}
@media (max-width: 620px) {
  .wordmark .word { display: none; }
  .nav-in { gap: 12px; }
}

/* ── Buttons and forms ────────────────────────────────────────────────── */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 9px;
  padding: 12px 20px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--btn-bg); background: var(--btn-bg); color: var(--btn-fg);
  font-family: var(--font-mono); font-size: 10.5px; letter-spacing: .14em;
  text-transform: uppercase; text-decoration: none; font-weight: 600;
  transition: transform .25s var(--ease), box-shadow .25s var(--ease), background .25s var(--ease);
  box-shadow: 0 8px 24px -12px rgba(211, 167, 95, .8);
}
.btn:hover { color: var(--btn-fg); transform: translateY(-1px); box-shadow: 0 14px 30px -12px rgba(211, 167, 95, .9); }
.btn:active { transform: translateY(0); }
.btn-ghost {
  background: transparent; color: var(--text); border-color: var(--rule);
  box-shadow: none; font-weight: 500;
}
.btn-ghost:hover { color: var(--gold); border-color: var(--gold); background: var(--gold-soft); box-shadow: none; }

.ask { display: flex; gap: 10px; align-items: stretch; }
.ask input[type="text"], .ask input[type="email"], .plan input[type="email"] {
  flex: 1; min-width: 0;
  padding: 15px 18px;
  border-radius: 999px;
  border: 1px solid var(--rule);
  background: var(--card);
  color: var(--text);
  font-family: var(--font-body);
  font-size: 1.0625rem;
  transition: border-color .2s var(--ease), background .2s var(--ease), box-shadow .2s var(--ease);
}
.ask input::placeholder { color: var(--faint); font-style: italic; }
.ask input:focus {
  outline: none; border-color: var(--gold);
  background: var(--card-hi);
  box-shadow: 0 0 0 3px var(--gold-soft);
}
.ask button {
  flex: none; display: inline-flex; align-items: center; gap: 10px;
  padding: 0 26px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--btn-bg); background: var(--btn-bg); color: var(--btn-fg);
  font-family: var(--font-mono); font-size: 10.5px; letter-spacing: .16em;
  text-transform: uppercase; font-weight: 600;
  transition: transform .25s var(--ease), box-shadow .25s var(--ease);
  box-shadow: 0 10px 26px -14px rgba(211, 167, 95, .9);
}
.ask button:hover { transform: translateY(-1px); box-shadow: 0 16px 34px -14px rgba(211, 167, 95, 1); }
.ask button svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.8; }
@media (max-width: 560px) {
  .ask { flex-direction: column; }
  .ask button { padding: 14px 22px; justify-content: center; }
}

/* ── Hero ─────────────────────────────────────────────────────────────── */
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.02fr) minmax(0, .98fr);
  gap: clamp(36px, 6vw, 84px);
  align-items: center;
  padding-block: clamp(56px, 9vw, 118px) clamp(48px, 7vw, 96px);
}
.display {
  font-size: clamp(2.85rem, 6.4vw, 5.4rem);
  line-height: .96;
  letter-spacing: -.035em;
  font-variation-settings: "SOFT" 46, "WONK" 1;
  margin: 0 0 26px;
  text-wrap: balance;
}
.display em {
  font-style: italic;
  color: var(--gold-hi);
  font-variation-settings: "SOFT" 90, "WONK" 1;
}
.lede {
  font-size: clamp(1.06rem, .95rem + .38vw, 1.24rem);
  line-height: 1.66;
  color: var(--dim);
  max-width: 46ch;
  margin-bottom: 30px;
}
.hero-copy { min-width: 0; }
.picks {
  list-style: none; margin: 30px 0 0; padding: 22px 0 0;
  border-top: 1px solid var(--rule-soft);
  display: flex; flex-wrap: wrap; gap: 10px 10px;
}
.picks li { margin: 0; }
.pick {
  display: inline-flex; align-items: baseline; gap: 9px;
  padding: 8px 14px 8px 12px; border-radius: 999px;
  border: 1px solid var(--rule); background: var(--card);
  color: var(--dim); cursor: pointer;
  transition: border-color .2s var(--ease), color .2s var(--ease), transform .2s var(--ease);
  max-width: 100%;
}
.pick:hover { color: var(--text); border-color: var(--gold); transform: translateY(-2px); }
.pick .k {
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .16em;
  text-transform: uppercase; color: var(--gold); flex: none;
}
.pick .t {
  font-family: var(--font-display); font-size: 14.5px; letter-spacing: -.01em;
  min-width: 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.picks-note { margin: 10px 0 0; font-size: 13px; color: var(--faint); }
@media (max-width: 620px) {
  .pick { max-width: 100%; align-items: flex-start; }
  .pick .t { white-space: normal; text-align: left; overflow: visible; }
}

/* ── The plate: a real document, floating ─────────────────────────────── */
.plate-wrap {
  position: relative;
  perspective: 1500px;
  padding: 10px 14px 26px;
}
/* Two sheets underneath: the object is on a desk, not on a screen. */
.plate-wrap::before,
.plate-wrap::after {
  content: ""; position: absolute; pointer-events: none;
  border-radius: 6px;
  background: linear-gradient(158deg, #e8dabd, #cbb994);
  box-shadow: 0 22px 44px -22px rgba(0, 0, 0, .85);
}
.plate-wrap::before {
  inset: 26px 40px 34px 26px; transform: rotate(-3.6deg); opacity: .5;
}
.plate-wrap::after {
  inset: 40px 26px 30px 44px; transform: rotate(2.4deg); opacity: .26;
}
:root[data-theme="light"] .plate-wrap::before,
:root[data-theme="light"] .plate-wrap::after { opacity: .32; }
.plate {
  position: relative;
  z-index: 1;
  transform-style: preserve-3d;
  transform: rotateX(var(--rx, 6deg)) rotateY(var(--ry, -13deg)) rotateZ(var(--rz, -1.4deg));
  transition: transform .7s var(--ease), box-shadow .7s var(--ease);
  background:
    linear-gradient(158deg, #fdf6e7 0%, var(--paper) 42%, var(--paper-2) 100%);
  color: var(--paper-ink);
  border-radius: 4px 12px 12px 4px;
  padding: clamp(22px, 2.6vw, 34px) clamp(20px, 2.6vw, 34px) clamp(24px, 3vw, 34px);
  box-shadow: var(--shadow-2);
  will-change: transform;
}
.plate::before {
  content: ""; position: absolute; inset: 9px; border-radius: 2px 8px 8px 2px;
  border: 1px solid rgba(122, 86, 30, .3); pointer-events: none;
}
.plate::after {
  content: ""; position: absolute; inset: 0 auto 0 0; width: 7px;
  border-radius: 4px 0 0 4px;
  background: linear-gradient(90deg, rgba(90, 62, 20, .22), transparent);
}
.plate a { color: #7a2b22; text-decoration-color: rgba(122, 43, 34, .35); }
.plate-head {
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
  padding-bottom: 14px; margin-bottom: 20px;
  border-bottom: 1px solid rgba(122, 86, 30, .26);
}
.plate-head .label { color: #7b6544; }
.plate-link { text-decoration: none; display: block; }
.plate-link:hover .plate-q { text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 4px; }
.plate-q {
  font-family: var(--font-display); font-size: clamp(1.2rem, 1.6vw, 1.55rem);
  line-height: 1.16; letter-spacing: -.02em; margin: 0 0 14px;
  font-variation-settings: "SOFT" 40, "WONK" 1;
  color: #1b1417;
}
.plate-thesis {
  font-family: var(--font-body); font-size: 15.5px; line-height: 1.55;
  color: #34292a; margin: 0 0 20px;
}
.plate-srcs { list-style: none; margin: 0 0 18px; padding: 0; }
.plate-srcs li {
  display: grid; grid-template-columns: 18px 1fr; gap: 10px;
  padding: 8px 0; border-top: 1px solid rgba(122, 86, 30, .16);
  font-size: 13.5px; line-height: 1.35; color: #4a3d38;
}
.plate-srcs li:last-child { border-bottom: 1px solid rgba(122, 86, 30, .16); }
.plate-srcs .n {
  font-family: var(--font-mono); font-size: 10px; color: #8a6a2c; padding-top: 2px;
}
.plate-foot {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding-right: 46px;
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .14em;
  text-transform: uppercase; color: #836f4e;
}
.plate-seal {
  position: absolute; right: -18px; bottom: -26px;
  width: 84px; height: 84px; border-radius: 50%;
  display: grid; place-items: center;
  transform: rotate(-13deg) translateZ(40px);
  background: radial-gradient(circle at 34% 28%, #b8402f, #7d1f18 62%, #5e1411);
  color: #f7e3c4; text-align: center;
  font-family: var(--font-mono); font-size: 8px; letter-spacing: .14em;
  text-transform: uppercase; line-height: 1.5;
  box-shadow: 0 12px 26px -10px rgba(0, 0, 0, .8), inset 0 2px 6px rgba(255, 255, 255, .22);
}
.plate-seal::before {
  content: ""; position: absolute; inset: 7px; border-radius: 50%;
  border: 1px dashed rgba(247, 227, 196, .45);
}
.plate-seal b { display: block; font-family: var(--font-display); font-size: 17px; letter-spacing: .02em; }
.plate-shadow {
  position: absolute; left: 12%; right: 12%; bottom: 4px; height: 32px;
  filter: blur(22px); background: rgba(0, 0, 0, .55); border-radius: 50%;
  z-index: -1;
}
:root[data-theme="light"] .plate-shadow { background: rgba(60, 42, 20, .3); }

/* ── Bands of content ─────────────────────────────────────────────────── */
.band { padding-block: clamp(44px, 7vw, 88px); border-top: 1px solid var(--rule-soft); }
.band-head { max-width: 54ch; margin-bottom: 42px; }
.band-head h2 { font-size: clamp(1.7rem, 2.6vw, 2.5rem); margin-bottom: 12px; }
.band-head p { color: var(--dim); margin: 0; }
.steps {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: clamp(20px, 3vw, 40px);
}
.step { position: relative; padding-top: 20px; border-top: 1px solid var(--rule); }
.step-n {
  display: block; font-family: var(--font-display); font-size: clamp(2.6rem, 4vw, 3.6rem);
  line-height: 1; color: transparent; -webkit-text-stroke: 1px var(--gold);
  opacity: .7; margin-bottom: 14px; letter-spacing: -.04em;
}
.step h3 { font-size: 1.12rem; margin-bottom: 8px; }
.step p { color: var(--dim); font-size: .97rem; margin: 0; }
@media (max-width: 820px) {
  .steps { grid-template-columns: 1fr; }
  .step-n { font-size: 2.2rem; }
}

.sec-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 18px; margin-bottom: 26px;
}
.sec-head h2 { font-size: clamp(1.5rem, 2.2vw, 2rem); }
.sec-head a {
  font-family: var(--font-mono); font-size: 10.5px; letter-spacing: .14em;
  text-transform: uppercase; text-decoration: none; white-space: nowrap;
}
.sec-head a:hover { text-decoration: underline; }

/* ── Document cards ───────────────────────────────────────────────────── */
.stack { list-style: none; margin: 0; padding: 0; display: grid; gap: 1px; background: var(--rule-soft); border-block: 1px solid var(--rule-soft); }
.stack li { margin: 0; background: var(--ink); }
.doc-card {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr) auto;
  gap: clamp(14px, 2.4vw, 30px);
  align-items: center;
  padding: clamp(18px, 2.4vw, 26px) clamp(4px, 1vw, 16px);
  text-decoration: none; color: var(--text);
  transition: background .25s var(--ease), padding-left .25s var(--ease);
}
.doc-card:hover { background: var(--card); padding-left: clamp(10px, 2vw, 26px); color: var(--text); }
.doc-card .idx {
  font-family: var(--font-display); font-size: 15px; color: var(--gold);
  letter-spacing: .02em; opacity: .8;
}
.doc-card .q {
  font-family: var(--font-display); font-size: clamp(1.05rem, 1.7vw, 1.4rem);
  line-height: 1.25; letter-spacing: -.01em; margin: 0 0 6px;
}
.doc-card .meta {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px;
  font-family: var(--font-mono); font-size: 10.5px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--dim);
}
.doc-card .arrow {
  width: 30px; height: 30px; border-radius: 50%; display: grid; place-items: center;
  border: 1px solid var(--rule); color: var(--dim);
  transition: transform .3s var(--ease), border-color .3s var(--ease), color .3s var(--ease);
}
.doc-card .arrow svg { width: 13px; height: 13px; fill: none; stroke: currentColor; stroke-width: 1.7; }
.doc-card:hover .arrow { transform: translateX(4px); border-color: var(--gold); color: var(--gold); }
@media (max-width: 620px) {
  .doc-card { grid-template-columns: 34px minmax(0, 1fr); }
  .doc-card .arrow { display: none; }
}

/* ── Document page ────────────────────────────────────────────────────── */
.doc-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 342px;
  gap: clamp(34px, 5vw, 76px);
  align-items: start;
  padding-block: clamp(34px, 5vw, 64px) clamp(60px, 8vw, 110px);
}
.doc { min-width: 0; }
.doc-head { margin-bottom: clamp(30px, 4vw, 46px); }
.doc-title {
  font-size: clamp(2rem, 1.35rem + 2.5vw, 3.35rem);
  line-height: 1.04;
  letter-spacing: -.028em;
  font-variation-settings: "SOFT" 40, "WONK" 1;
  margin: 14px 0 16px;
}
.doc-work {
  font-family: var(--font-display); font-size: 1.02rem; color: var(--dim);
  margin: 0 0 18px; letter-spacing: 0;
}
.doc-work em { color: var(--text); font-style: italic; }
.doc-meta {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px 18px;
  padding-top: 16px; border-top: 1px solid var(--rule);
  font-family: var(--font-mono); font-size: 10.5px; letter-spacing: .12em;
  text-transform: uppercase; color: var(--dim);
}
.doc-meta .dot { width: 3px; height: 3px; border-radius: 50%; background: var(--faint); }
.copy-link {
  margin-left: auto; display: inline-flex; align-items: center; gap: 7px;
  background: none; border: 0; padding: 4px 0; cursor: pointer;
  font-family: var(--font-mono); font-size: 10px; letter-spacing: .12em;
  text-transform: uppercase; color: var(--dim);
}
.copy-link:hover { color: var(--gold); }
.copy-link svg { width: 12px; height: 12px; fill: none; stroke: currentColor; stroke-width: 1.7; }

/* Thesis: the sentence the whole document stands on */
.thesis {
  position: relative;
  font-family: var(--font-display);
  font-size: clamp(1.28rem, 1.02rem + .78vw, 1.66rem);
  line-height: 1.42;
  letter-spacing: -.012em;
  font-variation-settings: "SOFT" 50, "WONK" 1;
  padding: 6px 0 6px clamp(20px, 3vw, 30px);
  margin: 0 0 clamp(32px, 4vw, 48px);
  border-left: 1px solid var(--gold);
  color: var(--text);
}
.thesis::before {
  content: "“";
  position: absolute; left: clamp(4px, 1.4vw, 12px); top: -.28em;
  font-family: var(--font-display); font-size: 2.6em; line-height: 1;
  color: var(--gold); opacity: .28;
}
.thesis .pins { font-size: 10px; vertical-align: baseline; margin-left: 6px; }

.prose { font-size: clamp(1.04rem, .96rem + .28vw, 1.16rem); }

.prose > .block > p { line-height: 1.72; color: var(--text); max-width: 34em; margin-bottom: 6px; }
.prose > .block { margin-bottom: 30px; }
.prose .dropcap::first-letter {
  float: left; font-family: var(--font-display); font-size: 3.6em; line-height: .82;
  padding: .06em .12em 0 0; color: var(--gold); font-weight: 500;
}

.pins {
  font-family: var(--font-mono); font-size: 9px; letter-spacing: .04em;
  vertical-align: super; margin-left: 4px; line-height: 0;
}
.pins a {
  display: inline-block; padding: 1px 3px; margin-right: 1px; border-radius: 3px;
  color: var(--gold); text-decoration: none;
  transition: background .2s var(--ease), color .2s var(--ease);
}
.pins a:hover { background: var(--gold); color: #150e07; }

/* Quotes */
.quote {
  position: relative; margin: 0 0 26px; padding: 24px 26px 20px clamp(40px, 5vw, 58px);
  background: var(--card); border: 1px solid var(--rule); border-radius: var(--r);
  font-style: italic; font-size: 1.1rem; line-height: 1.6;
  color: var(--text); white-space: pre-wrap;
}
.quote::before {
  content: "“"; position: absolute; left: 14px; top: 4px;
  font-family: var(--font-display); font-size: 46px; line-height: 1;
  color: var(--gold); opacity: .55; font-style: normal;
}
.quote .src {
  display: block; margin-top: 14px; padding-top: 12px;
  border-top: 1px solid var(--rule-soft);
  font-family: var(--font-mono); font-style: normal; font-size: 10px;
  letter-spacing: .1em; text-transform: uppercase; color: var(--dim);
}
.quote-note {
  margin: -18px 0 30px; font-family: var(--font-mono); font-size: 9.5px;
  letter-spacing: .14em; text-transform: uppercase; color: var(--faint);
}

/* Conflicts: two positions, neither deleted */
.conflict {
  margin: 0 0 30px; border: 1px solid var(--rule); border-radius: var(--r);
  overflow: hidden; background: var(--card);
}
.conflict-head {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 16px 20px; border-bottom: 1px solid var(--rule);
  background: linear-gradient(180deg, rgba(166, 58, 74, .07), transparent);
}
.conflict-q {
  font-family: var(--font-display); font-size: 1.02rem; color: var(--text);
  margin: 0; flex: 1 1 220px; line-height: 1.3;
}
.positions { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1px; background: var(--rule); }
.pos { background: var(--ink-2); padding: 20px; min-width: 0; }
.pos-value {
  font-family: var(--font-display); font-size: 1.02rem; line-height: 1.35;
  margin: 0 0 10px; color: var(--text);
}
.pos-src {
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--faint); margin: 0 0 10px;
}
.pos blockquote {
  margin: 0; padding-left: 12px; border-left: 1px solid var(--rule);
  font-style: italic; font-size: .93rem; line-height: 1.55; color: var(--dim);
}
.verdict {
  margin: 0; padding: 13px 20px; border-top: 1px solid var(--rule);
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .12em;
  text-transform: uppercase; color: var(--dim);
}
.conflict.readings .conflict-head { background: linear-gradient(180deg, rgba(106, 168, 149, .07), transparent); }
.conflict.readings .chip { color: var(--verdigris); }

/* Gaps */
.gaps {
  margin: 0 0 30px; padding: 22px 24px; border-radius: var(--r);
  border: 1px dashed var(--rule); background: transparent;
}
.gaps h3 {
  font-family: var(--font-mono); font-size: 10px; letter-spacing: .18em;
  text-transform: uppercase; color: var(--dim); margin-bottom: 12px;
}
.gaps ul { margin: 0; padding: 0; list-style: none; }
.gaps li {
  position: relative; padding-left: 18px; margin-bottom: 8px;
  font-size: .98rem; line-height: 1.5; color: var(--dim);
}
.gaps li::before { content: "—"; position: absolute; left: 0; color: var(--gold); }
.gaps li:last-child { margin-bottom: 0; }

/* Deepening */
.deepen {
  display: inline-flex; align-items: center; gap: 8px; margin-top: 4px;
  padding: 6px 0; background: none; border: 0; cursor: pointer;
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .16em;
  text-transform: uppercase; color: var(--faint);
  transition: color .2s var(--ease);
}
.deepen:hover { color: var(--gold); }
.deepen .chev { width: 11px; height: 11px; fill: none; stroke: currentColor; stroke-width: 2; transition: transform .35s var(--ease); }
.deepen[aria-expanded="true"] { color: var(--gold); }
.deepen[aria-expanded="true"] .chev { transform: rotate(180deg); }
.deepen[disabled] { opacity: .5; cursor: progress; }
.deepening:empty { display: none; }
.deepening {
  margin-top: 16px; padding-left: clamp(14px, 2.4vw, 26px);
  border-left: 1px solid var(--rule);
  animation: unfold .45s var(--ease) both;
}
.deepening .meta {
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .12em;
  text-transform: uppercase; color: var(--faint); margin: 0 0 10px;
}
@keyframes unfold {
  from { opacity: 0; transform: translateY(-6px); }
  to { opacity: 1; transform: none; }
}

/* Sufficiency banner */
.banner {
  display: flex; gap: 14px; align-items: flex-start;
  padding: 18px 20px; margin: 0 0 34px; border-radius: var(--r);
  border: 1px solid var(--rule); background: var(--card);
  font-size: .98rem; line-height: 1.55; color: var(--dim);
}
.banner strong { color: var(--text); font-weight: 600; }
.banner.warn { border-color: color-mix(in srgb, var(--oxblood) 50%, transparent); }
.banner.warn strong { color: var(--oxblood-hi); }

/* Attribution */
.attribution {
  margin-top: clamp(40px, 6vw, 64px); padding-top: 26px;
  border-top: 1px solid var(--rule);
}
.attribution h3 {
  font-family: var(--font-mono); font-size: 10px; letter-spacing: .18em;
  text-transform: uppercase; color: var(--dim); margin-bottom: 16px;
}
.attribution ul { list-style: none; margin: 0 0 18px; padding: 0; }
.attribution li {
  display: grid; grid-template-columns: 26px 1fr; gap: 8px;
  padding: 9px 0; border-top: 1px solid var(--rule-soft);
  font-size: .88rem; line-height: 1.5; color: var(--dim);
}
.attribution li strong { color: var(--gold); font-family: var(--font-mono); font-size: 10px; font-weight: 400; padding-top: 3px; }
.attribution .credit { display: block; margin-top: 4px; color: var(--faint); font-size: .82rem; }
.attribution > p { font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .1em; text-transform: uppercase; color: var(--faint); margin: 0; }

/* ── The rail ─────────────────────────────────────────────────────────── */
.rail { display: grid; gap: 34px; }
@media (min-width: 1041px) {
  .rail {
    position: sticky; top: calc(var(--nav-h) + 28px);
    max-height: calc(100vh - var(--nav-h) - 56px);
    overflow-y: auto; overscroll-behavior: contain;
    padding-right: 6px; scrollbar-width: thin;
  }
}
.rail section { min-width: 0; }
.rail h3 {
  display: flex; align-items: center; gap: 10px;
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .2em;
  text-transform: uppercase; color: var(--dim); margin: 0 0 16px;
}
.rail h3::after { content: ""; flex: 1; height: 1px; background: var(--rule-soft); }

.timeline { list-style: none; margin: 0; padding: 0 0 0 2px; position: relative; }
.timeline::before {
  content: ""; position: absolute; left: 3px; top: 6px; bottom: 12px; width: 1px;
  background: linear-gradient(180deg, var(--gold), rgba(211, 167, 95, .28));
}
.timeline li {
  position: relative; padding: 0 0 18px 20px; margin: 0;
  font-size: .9rem; line-height: 1.45; color: var(--dim);
}
.timeline li::before {
  content: ""; position: absolute; left: -1px; top: 7px; width: 9px; height: 9px;
  border-radius: 50%; background: var(--ink);
  border: 1px solid var(--gold); box-shadow: 0 0 0 3px var(--ink);
}
.timeline li:last-child { padding-bottom: 0; }
.timeline .when {
  display: block; font-family: var(--font-mono); font-size: 9.5px;
  letter-spacing: .12em; text-transform: uppercase; color: var(--gold);
  margin-bottom: 3px;
}
.timeline .t-title { color: var(--text); }
.timeline li.undated::before { border-color: var(--faint); }
.timeline li.undated .when { color: var(--faint); }

.gaps-list { list-style: none; margin: 0; padding: 0; }
.gaps-list li {
  position: relative; padding: 0 0 12px 16px; margin: 0;
  font-size: .9rem; line-height: 1.5; color: var(--dim);
}
.gaps-list li::before { content: "—"; position: absolute; left: 0; color: var(--gold); }

.srcs { list-style: none; margin: 0; padding: 0; counter-reset: src; }
.srcs li {
  display: grid; grid-template-columns: 26px 1fr; gap: 4px 8px;
  padding: 12px 0; margin: 0;
  border-top: 1px solid var(--rule-soft);
  scroll-margin-top: calc(var(--nav-h) + 16px);
}
.srcs li:first-child { border-top: 0; padding-top: 0; }
.srcs .n {
  font-family: var(--font-display); font-size: 15px; color: var(--gold);
  line-height: 1.2; padding-top: 1px;
}
.srcs .who { font-size: .9rem; line-height: 1.4; color: var(--text); }
.srcs .who em { font-style: italic; }
.srcs .when {
  display: block; font-family: var(--font-mono); font-size: 9px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--faint); margin-top: 4px;
}
.srcs .who a {
  display: inline-block; margin-top: 6px; font-family: var(--font-mono);
  font-size: 9px; letter-spacing: .12em; text-transform: uppercase;
  text-decoration: none; color: var(--gold);
}
.srcs .who a:hover { text-decoration: underline; }
.srcs li:target { background: var(--gold-soft); box-shadow: 0 0 0 8px var(--gold-soft); border-radius: 4px; }

.rail .ask { flex-direction: column; }
.rail .ask input[type="text"] { font-size: .95rem; padding: 12px 16px; }
.rail .ask button { padding: 12px 20px; justify-content: center; }

/* ── Index: candidates page ───────────────────────────────────────────── */
.page-head { padding-block: clamp(40px, 6vw, 72px) 0; }
.page-head h2 { font-size: clamp(1.9rem, 3.4vw, 2.9rem); margin-bottom: 12px; }
.page-head p { color: var(--dim); max-width: 58ch; }
.candidates { list-style: none; margin: 34px 0 0; padding: 0; display: grid; gap: 14px; }
.candidates li {
  display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 14px 22px;
  align-items: center; padding: 20px 22px; margin: 0;
  border: 1px solid var(--rule); border-radius: var(--r); background: var(--card);
  transition: border-color .25s var(--ease), transform .25s var(--ease);
}
.candidates li:hover { border-color: var(--gold); transform: translateY(-2px); }
.candidates .c-title { font-family: var(--font-display); font-size: 1.14rem; margin: 0 0 6px; }
.candidates .why {
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--faint);
}
.candidates form { margin: 0; }
.candidates button {
  padding: 10px 18px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--rule); background: transparent; color: var(--text);
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .14em;
  text-transform: uppercase;
  transition: border-color .2s var(--ease), color .2s var(--ease), background .2s var(--ease);
}
.candidates button:hover { border-color: var(--gold); color: var(--gold); background: var(--gold-soft); }
@media (max-width: 620px) {
  .candidates li { grid-template-columns: 1fr; }
}

/* ── Pricing ──────────────────────────────────────────────────────────── */
.plans {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(270px, 1fr));
  gap: clamp(16px, 2.4vw, 26px); margin: 46px 0 28px;
  perspective: 1400px;
}
.plan {
  position: relative; display: flex; flex-direction: column;
  padding: 30px 28px 28px; border-radius: var(--r);
  border: 1px solid var(--rule); background: var(--card);
  transform-style: preserve-3d;
  transform: rotateX(var(--rx, 0deg)) rotateY(var(--ry, 0deg));
  transition: transform .5s var(--ease), border-color .3s var(--ease), box-shadow .3s var(--ease);
}
.plan:hover { border-color: color-mix(in srgb, var(--gold) 50%, transparent); box-shadow: var(--shadow-1); }
.plan h3 {
  font-family: var(--font-mono); font-size: 10px; letter-spacing: .2em;
  text-transform: uppercase; color: var(--dim); margin-bottom: 12px;
}
.plan .price {
  font-family: var(--font-display); font-size: 2.5rem; line-height: 1;
  letter-spacing: -.03em; margin-bottom: 6px;
}
.plan .price small { font-family: var(--font-mono); font-size: 10px; letter-spacing: .1em; color: var(--faint); }
.plan .pitch { font-size: .93rem; color: var(--dim); margin: 10px 0 22px; line-height: 1.55; }
.plan ul { list-style: none; margin: 0 0 26px; padding: 0; flex: 1; }
.plan li {
  position: relative; padding: 9px 0 9px 20px; font-size: .93rem; line-height: 1.45;
  color: var(--dim); border-top: 1px solid var(--rule-soft);
}
.plan li::before {
  content: ""; position: absolute; left: 2px; top: 15px;
  width: 5px; height: 5px; background: var(--gold);
  transform: rotate(45deg);
}
.plan form { display: flex; flex-direction: column; gap: 10px; }
.plan input[type="email"] { padding: 12px 16px; border-radius: 999px; font-size: .95rem; }
.plan button {
  padding: 13px 20px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--btn-bg); background: var(--btn-bg); color: var(--btn-fg);
  font-family: var(--font-mono); font-size: 10px; letter-spacing: .14em;
  text-transform: uppercase; font-weight: 600;
  transition: transform .2s var(--ease), box-shadow .25s var(--ease);
}
.plan button:hover { transform: translateY(-1px); box-shadow: 0 14px 28px -14px rgba(211, 167, 95, .9); }
.plan.featured {
  background: linear-gradient(158deg, #fdf6e7, var(--paper) 45%, var(--paper-2));
  color: var(--paper-ink); border-color: rgba(122, 86, 30, .4);
  box-shadow: var(--shadow-2); transform: rotateX(var(--rx, 0deg)) rotateY(var(--ry, 0deg)) translateZ(18px);
}
.plan.featured h3 { color: #7b6544; }
.plan.featured .pitch, .plan.featured li { color: #4a3d38; }
.plan.featured li { border-top-color: rgba(122, 86, 30, .2); }
.plan.featured li::before { background: #a67c2c; }
.plan.featured .price { color: #1b1417; }
.plan.featured .price small { color: #8a6a2c; }
.plan.featured button { background: #1b1417; border-color: #1b1417; color: var(--paper); }
.plan.featured input[type="email"] {
  background: rgba(255, 255, 255, .55); border-color: rgba(122, 86, 30, .35); color: #1b1417;
}
.plan.featured input[type="email"]::placeholder { color: #8a7550; }
.plan .ribbon {
  position: absolute; top: 18px; right: 18px;
  font-family: var(--font-mono); font-size: 8.5px; letter-spacing: .16em;
  text-transform: uppercase; color: #7a2b22;
  border: 1px solid rgba(122, 43, 34, .4); border-radius: 999px; padding: 4px 9px 3px;
}
.note {
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .1em;
  text-transform: uppercase; color: var(--faint); line-height: 1.7;
}
.plan .note { margin: 12px 0 0; }

/* ── Account ──────────────────────────────────────────────────────────── */
.account {
  max-width: 620px; margin: clamp(40px, 7vw, 84px) auto clamp(60px, 9vw, 110px);
  padding: clamp(28px, 4vw, 44px);
  border: 1px solid var(--rule); border-radius: var(--r);
  background: var(--card); box-shadow: var(--shadow-1);
}
.account h2 { font-size: clamp(1.7rem, 3vw, 2.4rem); margin-bottom: 8px; }
.account .who { color: var(--dim); margin: 0 0 28px; }
.account .rows { display: grid; gap: 1px; background: var(--rule-soft); border-block: 1px solid var(--rule-soft); margin-bottom: 26px; }
.account .row {
  display: flex; justify-content: space-between; gap: 20px; align-items: baseline;
  background: var(--ink); padding: 14px 2px;
}
.account .row dt {
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .14em;
  text-transform: uppercase; color: var(--dim);
}
.account .row dd { margin: 0; font-family: var(--font-display); font-size: 1.02rem; }
progress.meter {
  appearance: none; -webkit-appearance: none;
  width: 100%; height: 6px; border: 0; border-radius: 999px;
  background: var(--rule-soft); overflow: hidden; margin: 6px 0 10px;
}
progress.meter::-webkit-progress-bar { background: var(--rule-soft); border-radius: 999px; }
progress.meter::-webkit-progress-value { background: linear-gradient(90deg, var(--oxblood), var(--gold)); border-radius: 999px; }
progress.meter::-moz-progress-bar { background: linear-gradient(90deg, var(--oxblood), var(--gold)); border-radius: 999px; }
.account-actions { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-top: 26px; }
.account-actions form { margin: 0; }

/* ── Message pages ────────────────────────────────────────────────────── */
.message {
  max-width: 560px; margin: clamp(48px, 9vw, 110px) auto clamp(60px, 9vw, 120px);
  text-align: center;
}
.message .mark-lg {
  width: 54px; height: 54px; border-radius: 50%; margin: 0 auto 26px;
  display: grid; place-items: center; font-family: var(--font-display); font-size: 26px;
  color: #170f07; background: radial-gradient(circle at 32% 24%, #f7e0b2, #cfa457 52%, #8a6322);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .55), 0 8px 26px -8px rgba(211, 167, 95, .6);
}
.message h2 { font-size: clamp(1.9rem, 3.6vw, 2.7rem); margin-bottom: 14px; line-height: 1.06; }
.message .question { color: var(--dim); font-size: 1.05rem; margin-bottom: 30px; }
.message .ask { text-align: left; }

/* ── Footer ───────────────────────────────────────────────────────────── */
.colophon {
  position: relative; z-index: 1;
  border-top: 1px solid var(--rule);
  padding-block: clamp(30px, 4vw, 46px);
  margin-top: 20px;
}
.colophon-in {
  display: flex; flex-wrap: wrap; gap: 22px; align-items: baseline;
  justify-content: space-between;
}
.colophon .mark-word {
  font-family: var(--font-display); font-size: 1.05rem; color: var(--text);
  display: flex; align-items: center; gap: 10px;
}
.colophon p {
  margin: 6px 0 0; font-size: .84rem; line-height: 1.5; color: var(--faint);
  max-width: 46ch;
}
.colophon nav { display: flex; gap: 18px; flex-wrap: wrap; }
.colophon nav a {
  font-family: var(--font-mono); font-size: 9.5px; letter-spacing: .14em;
  text-transform: uppercase; text-decoration: none; color: var(--dim);
}
.colophon nav a:hover { color: var(--gold); }

/* ── Motion ───────────────────────────────────────────────────────────── */
.reveal { opacity: 0; transform: translateY(24px); transition: opacity .8s var(--ease), transform .8s var(--ease); }
.reveal.in { opacity: 1; transform: none; }
[data-tilt] { transform-style: preserve-3d; will-change: transform; }

@keyframes rise {
  from { opacity: 0; transform: translateY(26px); }
  to { opacity: 1; transform: none; }
}
.hero-copy > * { animation: rise .85s var(--ease) both; }
.hero-copy > *:nth-child(1) { animation-delay: .02s; }
.hero-copy > *:nth-child(2) { animation-delay: .08s; }
.hero-copy > *:nth-child(3) { animation-delay: .16s; }
.hero-copy > *:nth-child(4) { animation-delay: .24s; }
.hero-copy > *:nth-child(5) { animation-delay: .32s; }
.plate-wrap { animation: rise 1.1s var(--ease) .18s both; }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .001ms !important; animation-delay: 0s !important;
    transition-duration: .001ms !important; scroll-behavior: auto !important;
  }
  .reveal { opacity: 1; transform: none; }
  /* The resting tilt is a composition, not motion: keep it, drop the movement. */
}

/* ── Responsive ───────────────────────────────────────────────────────── */
@media (max-width: 1040px) {
  .doc-grid { grid-template-columns: 1fr; }
  .rail { gap: 30px; }
}
@media (max-width: 960px) {
  .hero { grid-template-columns: 1fr; gap: 46px; padding-block: 46px 60px; }
  .plate-wrap { max-width: 520px; margin-inline: auto; width: 100%; }
  .display { font-size: clamp(2.7rem, 11vw, 4.2rem); }
}
@media (max-width: 620px) {
  .nav { --nav-h: 60px; }
  .doc-title { letter-spacing: -.02em; }
  /* A narrow column cannot afford a deep rotation: the sheet would leave the
     viewport. The resting angle flattens; the pointer tilt still wins because
     it sets --rx/--ry inline. */
  .plate { --rx: 4deg; --ry: -6deg; }
  .plate-wrap { padding: 10px 20px 26px; }
  .plate-seal { width: 68px; height: 68px; right: -6px; bottom: -16px; }
  .plate-seal b { font-size: 14px; }
  .copy-link { margin-left: 0; }
  /* The seal sits over the foot at this width; stack the foot and keep it clear. */
  .plate-foot { display: block; padding-right: 58px; line-height: 1.7; }
  .plate-foot > span { display: block; }
  .colophon-in { flex-direction: column; }
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

  // ── Theme ────────────────────────────────────────────────────────────
  // The OS preference is the default (the CSS media query decides); an
  // explicit choice is remembered and wins.
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
  try {
    var stored = localStorage.getItem('ace-theme');
    if (stored === 'light' || stored === 'dark') root.setAttribute('data-theme', stored);
  } catch (e) {}
  syncToggle();
  if (toggle) {
    toggle.addEventListener('click', function () {
      var next = effectiveTheme() === 'light' ? 'dark' : 'light';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('ace-theme', next); } catch (e) {}
      syncToggle();
    });
  }

  // ── Reading progress ─────────────────────────────────────────────────
  var bar = document.querySelector('#progress span');
  if (bar) {
    var ticking = false;
    var paint = function () {
      var max = document.documentElement.scrollHeight - window.innerHeight;
      var p = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
      bar.style.transform = 'scaleX(' + p.toFixed(4) + ')';
      ticking = false;
    };
    window.addEventListener('scroll', function () {
      if (!ticking) { ticking = true; window.requestAnimationFrame(paint); }
    }, { passive: true });
    window.addEventListener('resize', paint);
    paint();
  }

  // ── Reveal ───────────────────────────────────────────────────────────
  // Content is visible by default; this only animates what has not been seen
  // yet. A browser without IntersectionObserver never hides anything.
  var targets = document.querySelectorAll('[data-reveal]');
  if ('IntersectionObserver' in window && !reduce && targets.length) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('in');
        io.unobserve(entry.target);
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 });

    Array.prototype.forEach.call(targets, function (el, i) {
      var box = el.getBoundingClientRect();
      if (box.top < window.innerHeight * 0.92) { return; }
      el.classList.add('reveal');
      el.style.transitionDelay = Math.min(i % 5, 4) * 70 + 'ms';
      io.observe(el);
    });
  }

  // ── Tilt: the plate and the plan cards are physical objects ──────────
  var fine = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  if (fine && !reduce) {
    Array.prototype.forEach.call(document.querySelectorAll('[data-tilt]'), function (el) {
      var max = parseFloat(el.getAttribute('data-tilt')) || 7;
      var frame = null;
      function apply(x, y) {
        el.style.setProperty('--ry', (x * max).toFixed(2) + 'deg');
        el.style.setProperty('--rx', (-y * max).toFixed(2) + 'deg');
      }
      el.addEventListener('pointermove', function (e) {
        if (frame) return;
        frame = window.requestAnimationFrame(function () {
          var r = el.getBoundingClientRect();
          apply((e.clientX - r.left) / r.width - 0.5, (e.clientY - r.top) / r.height - 0.5);
          frame = null;
        });
      });
      el.addEventListener('pointerleave', function () {
        el.style.setProperty('--ry', '0deg');
        el.style.setProperty('--rx', '0deg');
      });
    });
  }

  // ── Deepening: expand a block in place, under the reader's cursor ────
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
        var old = copy.getAttribute('data-label') || copy.textContent;
        copy.textContent = 'Copied';
        window.setTimeout(function () { copy.textContent = old; }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(done, function () {});
      } else {
        done();
      }
    });
  }

  // ── The question field cycles through real questions ─────────────────
  var field = document.getElementById('q');
  if (field && !reduce && !window.matchMedia('(pointer: coarse)').matches) {
    var prompts = [
      'Why does the Arietta of Op. 111 sound so plain?',
      'What is Le Bateau ivre actually about?',
      'How was To Pimp a Butterfly received?',
      'What did Beethoven change after 1820?'
    ];
    var n = 0;
    var cycle = window.setInterval(function () {
      if (document.activeElement === field || field.value) return;
      n = (n + 1) % prompts.length;
      field.setAttribute('placeholder', prompts[n]);
    }, 4200);
    field.addEventListener('focus', function () { window.clearInterval(cycle); }, { once: true });
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
        '<header class="nav"><div class="nav-in">'
        '<a class="wordmark" href="/">'
        '<span class="mark" aria-hidden="true">A</span>'
        '<span class="word">Art Context Engine</span></a>'
        '<nav class="nav-links" aria-label="Primary">'
        + link("/", "Ask", "ask")
        + link("/pricing", "Plans", "pricing")
        + link("/account", "Account", "account")
        + "</nav>"
        '<button class="theme-toggle" id="theme-toggle" type="button"'
        ' aria-label="Switch to light theme" aria-pressed="false">'
        + _SUN + _MOON +
        "</button></div></header>"
    )


_FOOTER = (
    '<footer class="colophon"><div class="wrap colophon-in"><div>'
    '<p class="mark-word"><span class="mark" aria-hidden="true">A</span>'
    "Art Context Engine</p>"
    "<p>Every sentence is tied to a dated source. Disagreements and gaps are shown, "
    "not smoothed over. Quoted text appears only where the licence allows it.</p>"
    "</div><nav aria-label=\"Footer\">"
    '<a href="/">Ask</a><a href="/pricing">Plans</a><a href="/account">Account</a>'
    '<a href="/api/docs">API</a>'
    "</nav></div></footer>"
)


def shell(title: str, body: str, *, description: str = "", active: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<meta name="theme-color" content="#08070a" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#f2ebdc" media="(prefers-color-scheme: light)">
{HEAD}
</head>
<body>
<div class="grain" aria-hidden="true"></div>
<a class="skip" href="#main">Skip to content</a>
<div class="progress" aria-hidden="true"><span></span></div>
{_nav(active)}
{body}
{_FOOTER}
</body></html>"""


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
        _block_html(b, answer, index, pack, sources, first=b is narrative[0] if narrative else False)
        for b in answer.blocks
        if b.kind != BlockKind.THESIS
    )

    body = f"""<main id="main"><div class="wrap doc-grid">
<article class="doc">
  <header class="doc-head" data-reveal>
    <p class="eyebrow">{_suff_chip(answer)}<span>{esc(_work_type(answer))}</span></p>
    <h1 class="doc-title">{esc(answer.question)}</h1>
    <p class="doc-work">{_work_line(pack, sources)}</p>
    <div class="doc-meta">
      <span>{len(answer.source_ids)} sources</span><span class="dot" aria-hidden="true"></span>
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
  {_attribution_html(answer, index)}
</article>
<aside class="rail" aria-label="Context">
  {_timeline_html(answer.timeline)}
  {_gaps_html(pack.gaps)}
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
        f"{answer.question} — Art Context Engine",
        body,
        description=answer.thesis[:180],
    )


def _work_type(answer: Answer) -> str:
    try:
        from src.registry import get_work

        work = get_work(answer.work_id)
        return work.type.value.title() if work else "Document"
    except Exception:  # registry is data, never a reason to fail a page
        return "Document"


def _work_line(pack: ContextPack, sources: dict[str, Source]) -> str:
    """Creator — <em>title</em>, the line that anchors the document to an object."""
    try:
        from src.registry import get_work

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


# ═══════════════════════════════════════════════════════════════════════════
# BLOCKS
# ═══════════════════════════════════════════════════════════════════════════

def _pins(block: Block, index: dict[str, int]) -> str:
    nums = sorted({index[sid] for sid in block.source_ids if sid in index})
    if not nums:
        return ""
    links = "".join(f'<a href="#src-{n}">{n}</a>' for n in nums)
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
            f'<li id="src-{number}"><span class="n" aria-hidden="true">{number}</span>'
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
    "sufficiency": "Sufficient evidence",
    "sources": ["Adorno, Beethoven · 1998", "Kinderman, Beethoven · 1995",
                "Tovey, Essays in Musical Analysis · 1935"],
    "work": "Beethoven — Piano Sonata No. 32, Op. 111",
}


def _plate(recent: list[Answer]) -> str:
    """The hero artefact: a real document, rendered small.

    Using an actual answer instead of lorem keeps the promise honest — the
    thing on the landing page is the thing the product makes.
    """
    lead = recent[0] if recent else None
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
        foot = f"{n} sources · {lead.generated_at:%b %Y}"
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
  <div class="plate" data-tilt="8">
    <div class="plate-head">{chip}<span class="label">Document</span></div>
    <a class="plate-link" href="{href}"><p class="plate-q">{esc(question)}</p></a>
    <p class="plate-thesis">{esc(thesis[:260])}{'…' if len(thesis) > 260 else ''}</p>
    <ul class="plate-srcs">{rows}</ul>
    <div class="plate-foot"><span>{esc(title)}</span><span>{esc(foot)}</span></div>
    <div class="plate-seal" aria-hidden="true"><span><b>ACE</b>sourced<br>not summarised</span></div>
  </div>
  <div class="plate-shadow" aria-hidden="true"></div>
</div>"""


def _plate_title(answer: Answer) -> str:
    try:
        from src.registry import get_work

        work = get_work(answer.work_id)
        if work:
            creator = work.primary_creator.name if work.primary_creator else ""
            return f"{creator} — {work.title}"
    except Exception:
        pass
    return "Art Context Engine"


def _pick(answer: Answer) -> str:
    kind = _work_type(answer)
    title = _plate_title(answer)
    if " — " in title:
        creator, work = title.split(" — ", 1)
        title = f"{creator.split()[-1]}, {work}"
    return (
        '<li><form method="post" action="/ask">'
        f'<input type="hidden" name="question" value="{esc(answer.question)}">'
        f'<input type="hidden" name="work_id" value="{esc(answer.work_id)}">'
        f'<button class="pick" type="submit"><span class="k">{esc(kind)}</span>'
        f'<span class="t">{esc(title)}</span></button></form></li>'
    )


def index_page(recent: list[Answer], ask_action: str = "/ask") -> str:
    seen: set[str] = set()
    picks = []
    for answer in recent:
        if answer.work_id in seen:
            continue
        seen.add(answer.work_id)
        picks.append(_pick(answer))
        if len(picks) == 3:
            break

    cards = "".join(
        f'<li><a class="doc-card" href="/a/{esc(a.id)}">'
        f'<span class="idx">{i:02d}</span>'
        f'<span><span class="q">{esc(a.question)}</span>'
        f'<span class="meta">{_suff_word(a)}<span>{esc(_plate_title(a))}</span>'
        f"<time datetime=\"{a.generated_at:%Y-%m-%d}\">{a.generated_at:%d %b %Y}</time>"
        f'<span>{len(a.source_ids)} sources</span></span></span>'
        f'<span class="arrow" aria-hidden="true">{_ARROW}</span></a></li>'
        for i, a in enumerate(recent[:8], start=1)
    )
    if not cards:
        cards = (
            '<li><div class="doc-card"><span class="idx">—</span>'
            '<span><span class="q">No documents yet.</span>'
            '<span class="meta"><span>Ask the first question above</span></span></span></div></li>'
        )

    picks_html = ""
    if picks:
        picks_html = (
            '<p class="picks-note">Documents already in the collection</p>'
            f'<ul class="picks">{"".join(picks)}</ul>'
        )

    body = f"""<main id="main">
<div class="wrap"><section class="hero">
  <div class="hero-copy">
    <p class="eyebrow">A research instrument for art</p>
    <h1 class="display">Understand a work<br><em>in its world.</em></h1>
    <p class="lede">Ask what a work means — why this sonata sounds so plain,
      why this poem drifts at sea. You get a document, not a summary: what the
      dated sources say, when they said it, where they disagree, and what nobody
      ever recorded.</p>
    <form class="ask" method="post" action="{ask_action}">
      <label class="sr-only" for="q">Your question about a work</label>
      <input id="q" type="text" name="question" required maxlength="500"
             autocomplete="off" placeholder="Why does the Arietta sound so plain?">
      <button type="submit"><span>Ask</span>{_ARROW}</button>
    </form>
    <div>{picks_html}</div>
  </div>
  {_plate(recent)}
</section></div>

<section class="band"><div class="wrap">
  <div class="band-head">
    <h2>Not a chatbot. A document.</h2>
    <p>The answer is assembled from sources, not generated from memory. Every
      sentence carries the number of the source that supports it.</p>
  </div>
  <div class="steps">
    <div class="step"><span class="step-n" aria-hidden="true">01</span>
      <h3>Ask about a work</h3>
      <p>A sonata, a poem, a record. The engine identifies the work before it
        says anything about it — and refuses when it cannot.</p></div>
    <div class="step"><span class="step-n" aria-hidden="true">02</span>
      <h3>Read the sources</h3>
      <p>Dated, ranked, licensed. Where the licence forbids quotation you get a
        link, never a paraphrase dressed as a quote.</p></div>
    <div class="step"><span class="step-n" aria-hidden="true">03</span>
      <h3>See the disagreement</h3>
      <p>Two readings that contradict each other are both printed, with their
        dates. Nothing is averaged into false consensus.</p></div>
  </div>
</div></section>

<section class="band"><div class="wrap">
  <div class="sec-head"><h2>Recently asked</h2><a href="/pricing">Plans &amp; limits →</a></div>
  <ul class="stack" data-reveal>{cards}</ul>
</div></section>
</main>"""

    return shell(
        "Art Context Engine — understand a work in its world",
        body,
        description=(
            "Ask why a work of art moves the way it does and get a sourced "
            "document: dated evidence, disagreements, and what nobody recorded."
        ),
        active="ask",
    )


def _suff_word(answer: Answer) -> str:
    labels = {
        Sufficiency.SUFFICIENT: "Sufficient",
        Sufficiency.THIN: "Thin",
        Sufficiency.INSUFFICIENT: "Insufficient",
    }
    return labels.get(answer.sufficiency, "Document")


def candidates_page(question: str, candidates, ask_action: str = "/ask") -> str:
    items = "".join(
        f'<li><div><p class="c-title">{esc(c.title)} — {esc(c.creator)}</p>'
        f'<p class="why">{esc(c.work_type.value.title())} · {esc(c.date_display)}'
        f' · match {c.confidence:.2f} ({esc(c.matched_on)})</p></div>'
        f'<form method="post" action="{ask_action}">'
        f'<input type="hidden" name="question" value="{esc(question)}">'
        f'<input type="hidden" name="work_id" value="{esc(c.work_id)}">'
        f'<button type="submit">Use this work</button></form></li>'
        for c in candidates
    )
    if not items:
        items = (
            '<li><div><p class="c-title">Nothing in the collection matches that.</p>'
            '<p class="why">Try a work by Beethoven, Rimbaud or Kendrick Lamar — '
            "or paste a Wikipedia or Wikidata link.</p></div></li>"
        )

    body = f"""<main id="main"><div class="wrap">
<header class="page-head">
  <p class="eyebrow">Several works fit</p>
  <h2>Which work did you mean?</h2>
  <p>{esc(question)}</p>
</header>
<ul class="candidates" data-reveal>{items}</ul>
</div></main>"""
    return shell("Which work did you mean? — Art Context Engine", body, active="ask")
