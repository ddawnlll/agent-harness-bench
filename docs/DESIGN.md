# Design system

The figures are the argument of this repository, so they are designed rather
than decorated. The language is editorial data journalism — precision, direct
labels, one annotation that says something — crossed with product-dashboard
restraint. Every figure is generated from `results/publication-data.json`.
Hand-drawn marks are annotations, never data.

This document defines the tokens and rules implemented by
`tools/chartkit.py` and `tools/make_charts.py`. If they disagree, the code and
`make_charts.py --check` win.

## Constraints

| Constraint | Consequence |
|---|---|
| GitHub renders SVG inside `<img>` | System font stacks only; no CSS, JS, webfont or network fetch |
| Figures derive from committed data | Every mark reproducible; `--check` must stay byte-identical |
| Light and dark themes | Two files per figure, identical geometry, selected with `<picture>` |
| Neutrality | Equal visual weight and annotation treatment; no ranking cues |
| One reading per figure | One hand annotation maximum, restating a proven fact |

## Typography

| Role | Stack | Sizes (px) |
|---|---|---|
| Display | `ui-serif, 'Iowan Old Style', 'Palatino Linotype', Palatino, Georgia, 'Times New Roman', serif` | 64, 50, 24, 17 |
| Text | `ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif` | 14, 12.5, 11 |
| Numerals | `ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace` | 64, 50, 24, 14, 11 |

The serif is the editorial voice, the sans carries labels, and the mono is
reserved for measured values so digits keep their width across themes. Nothing
is smaller than 11 px at 1×.

## Colour tokens

| Token | Light | Dark | Role |
|---|---|---|---|
| `surface` | `#fbfaf7` | `#0f1216` | figure background |
| `panel` | `#f3f0e9` | `#171c23` | grouping surface |
| `ink` | `#14171a` | `#f2f0ea` | primary type |
| `muted` | `#46505c` | `#bcc5d1` | secondary type |
| `faint` | `#657080` | `#8d98a7` | captions, axis labels |
| `rule` | `#e4dfd4` | `#29313c` | hairlines and grid |
| `rule_strong` | `#cec7b8` | `#3b4552` | emphasis rules |
| `pi` / `pi_soft` | `#2456c9` / `#e3e9fb` | `#8fb3ff` / `#1b2745` | Pi-Bolt accent and wash |
| `jcode` / `jcode_soft` | `#a94f05` / `#fbeed9` | `#f6b45b` / `#3a2a12` | JCode accent and wash |
| `hax` / `hax_soft` | `#6d28a8` / `#efe6fa` | `#c9a6ff` / `#2a1f3d` | Hax accent and wash |
| `pass` | `#0f6b60` | `#5eead4` | verified pass |
| `fail` | `#a5261c` | `#ff9a91` | verified fail |
| `warn` | `#8a5a00` | `#f6c177` | caveat, provisional |
| `api` | `#3a4553` | `#c2cbd8` | API service time |
| `busy` | `#c9c2b4` | `#44505f` | remainder, non-API wall time |
| `cpu` | `#0f6b60` | `#5eead4` | CPU seconds |
| `on_accent` / `on_soft` | `#ffffff` / `#14171a` | `#0f1216` / `#f2f0ea` | type on accent or wash |

Agent hues differ in lightness so lanes separate in greyscale. Contrast,
measured with the WCAG relative-luminance formula on the pairs used for text:

| Pair | Light | Dark |
|---|---:|---:|
| `ink` on `surface` | 17.24:1 | 16.48:1 |
| `muted` on `surface` | 7.85:1 | 10.77:1 |
| `faint` on `surface` | 4.81:1 | 6.42:1 |
| `pi` / `jcode` / `hax` on `surface` | 6.19 / 5.28 / 7.99:1 | 8.99 / 10.37 / 9.28:1 |
| `pass` / `fail` on `surface` | 6.11 / 6.95:1 | 12.69 / 9.19:1 |

One margin case: `faint` on `panel` is **4.41:1** in light, just below AA for
normal text, so captions sit on `surface`, never on `panel`. The pale `*_soft`
lanes are below 3:1 by design; they are grouping backgrounds and every label
inside them uses `ink` or the full-strength agent hue.

## Agent mapping

| Agent | Label | Token | Marker |
|---|---|---|---|
| Pi-Bolt | Pi-Bolt | `pi` | circle |
| JCode | JCode | `jcode` | square |
| Hax | Hax | `hax` | triangle |

Markers are optically equal in area, fixed in `chartkit.AGENT_MARK`. Lanes print
the name in the agent hue beside the marker, so identity never depends on colour.

## Layout

Canvas 1000 × 540–620 px, 30 px padding. Heights: hero 540, race 548, safety
620, timing 560, economics 540, resources 576. Spacing follows an 8 px rhythm:
24–26 px panel gaps, 38 px column gaps, 40–46 px row pitch, 106 px timeline
lanes. Header: 11 px kicker, 24 px display title, 12.5 px muted subtitle, then a
1 px `rule`. Source and units sit at 11 px `faint`. No boxes, shadows or
gradients. Geometry is identical across themes; only token values change.

## Marks and annotation

- **Data marks are exact**, snapped to 0.1 px from the dataset.
- **Hand marks are annotations.** `scribble`, `hand_underline`, `hand_circle`
  and `hand_arrow` add 0.8–1.5 px of seeded jitter; seeds are integer constants
  (7, 31, 32, 41, 71), so regeneration is byte-identical.
- **One annotation per figure**, restating what the figure proves. It never
  calls one agent better or points at a competitor as worse.
- **Direct labels** for every series, value and unit; the legend is a
  convenience. No decorative ink.

## Accessibility

1. WCAG 2.2 AA: 4.5:1 for text, 3:1 for graphical objects, both themes (one
   documented exception above).
2. No colour-only encoding: each agent has a marker shape, a text label and a
   fixed lane position.
3. Every SVG carries `<title>`, `<desc>`, `role="img"` and an `aria-label` that
   reads the headline numbers.
4. No text below 11 px; pass/fail is printed as a word as well as a colour.
5. Annotations duplicate printed facts, so skipping them loses nothing.

## Export and responsive rules

One self-contained `<svg>` per figure per theme: no `<image>`, script, external
font or CSS import. The XML namespace is the only URL and is an identifier, not
a fetch. Units are printed in labels; `--check` compares bytes; text baselines
are asserted inside the canvas at build time. The viewBox scales with the
container, so figures read best at 700–1000 px, the GitHub content width.
Nothing reflows, because reflow would move annotations and break byte identity;
on narrow screens the fallback is the table in `RESULTS.md`.

## Evolution of the previous design system

The philosophy — direct labels, editorial type, deterministic hand annotations,
system fonts only — is adapted from the author's previous benchmark,
[`ddawnlll/goal-extension-benchmark`](https://github.com/ddawnlll/goal-extension-benchmark),
whose `chartkit.py` established the primitives and the light/dark token
mechanism. This is a new design: canvas widened from 920 to 1000 px, palette
moved from two-hue indigo/amber to three-agent blue/orange/violet on warm paper,
type scale gained hero/mega sizes, and the figures compare three agents across
three tasks instead of two extensions across two trial pairs. The previous
repository was not modified. See [ATTRIBUTION.md](ATTRIBUTION.md).
