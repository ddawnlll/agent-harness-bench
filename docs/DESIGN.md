# Design system

The figures are the argument, so they are designed, not decorated: editorial data
journalism crossed with dashboard restraint. Every figure comes from
`results/publication-data.json`; hand-drawn marks are annotations, never data.
This defines what `tools/chartkit.py` and `tools/make_charts.py` implement; if
they disagree, the code and `--check` win.

## Constraints

| Constraint | Consequence |
|---|---|
| GitHub renders SVG inside `<img>` | System fonts only; no CSS, JS, webfont or network fetch |
| Figures derive from committed data | Every mark reproducible; `--check` stays byte-identical |
| Light and dark themes | Two files per figure, identical geometry, chosen by `<picture>` |
| Neutrality | Equal visual weight and annotation treatment; no ranking cues |
| One reading per figure | One hand annotation maximum, restating a proven fact |

## Typography

| Role | Stack | Sizes (px) |
|---|---|---|
| Display | `ui-serif, 'Iowan Old Style', 'Palatino Linotype', Palatino, Georgia, 'Times New Roman', serif` | 64, 50, 24, 17 |
| Text | `ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif` | 14, 12.5, 11 |
| Numerals | `ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace` | 64, 50, 24, 14, 11 |

Serif for titles, sans for labels, mono for measured values. Nothing below 11 px.

## Colour tokens

| Token | Light | Dark | Role |
|---|---|---|---|
| `surface` | `#fbfaf7` | `#0f1216` | background |
| `panel` | `#f3f0e9` | `#171c23` | grouping surface |
| `ink` | `#14171a` | `#f2f0ea` | primary type |
| `muted` | `#46505c` | `#bcc5d1` | secondary type |
| `faint` | `#5c6675` | `#8d98a7` | captions, axis labels |
| `rule` | `#e4dfd4` | `#29313c` | hairlines, grid |
| `wash` | `#f0ece3` | `#151a21` | neutral wash |
| `rule_strong` | `#cec7b8` | `#3b4552` | emphasis rules |
| `pi` / `pi_soft` | `#2456c9` / `#e3e9fb` | `#8fb3ff` / `#1b2745` | Pi-Bolt accent, wash |
| `jcode` / `jcode_soft` | `#a94f05` / `#fbeed9` | `#f6b45b` / `#3a2a12` | JCode accent, wash |
| `hax` / `hax_soft` | `#6d28a8` / `#efe6fa` | `#c9a6ff` / `#2a1f3d` | Hax accent, wash |
| `pass` | `#0f6b60` | `#5eead4` | verified pass |
| `fail` | `#a5261c` | `#ff9a91` | verified fail |
| `warn` | `#8a5a00` | `#f6c177` | caveat, provisional |
| `api` | `#3a4553` | `#c2cbd8` | API service time |
| `busy` | `#c9c2b4` | `#44505f` | non-API wall time |
| `cpu` | `#0f6b60` | `#5eead4` | CPU seconds |
| `on_accent` / `on_soft` | `#ffffff` / `#14171a` | `#0f1216` / `#f2f0ea` | type on accent, wash |

`tools/check_contrast.py` measures every enforced pair with the WCAG
relative-luminance formula and exits non-zero below AA. Reported ratios:
`ink` on `surface` 17.24:1 light / 16.48:1 dark; `muted` 7.85 / 10.77; `faint`
5.57 / 6.42 (5.11 / 5.85 on `panel`); agent hues 5.28–7.99 / 8.99–10.37;
`pass`/`fail` 6.11/6.95 light and 12.69/9.19 dark. All pass. The pale `*_soft`
lanes are below 3:1 by design; labels inside them use `ink` or the agent hue,
and the checker lists `busy`, `rule` and `rule_strong` as exempt fills that are
always accompanied by a direct label.

## Agent mapping

| Agent | Token | Marker |
|---|---|---|
| Pi-Bolt | `pi` | circle |
| JCode | `jcode` | square |
| Hax | `hax` | triangle |

Fixed in `chartkit.AGENT_TOKEN` and `AGENT_MARK`; markers are optically equal in
area and always appear beside the printed name, so identity never depends on
colour.

## Layout

Canvas 1000 px wide, 30 px padding, heights 540 (hero, economics), 548 (race),
560 (timing), 576 (resources), 620 (safety). Spacing follows an 8 px rhythm:
24–26 px panel gaps, 38 px column gaps, 40–46 px rows, 106 px timeline lanes.
Header: 11 px kicker, 24 px display title, 12.5 px muted subtitle, 1 px `rule`.
Source and units sit at 11 px `faint`. No boxes, shadows or gradients. Geometry
is identical across themes; only token values change.

## Marks and annotation

- **Data marks are exact**, snapped to 0.1 px from the dataset.
- **Hand marks are annotations.** `scribble`, `hand_underline`, `hand_circle`,
  `hand_arrow` add 0.8–1.5 px of seeded jitter; seeds are constants (7, 31, 32,
  41, 71), so regeneration is byte-identical.
- **One annotation per figure**, restating what the figure proves; never calling
  one agent better or pointing at a competitor as worse.
- **Direct labels** for every series, value and unit; the legend is a
  convenience. No decorative ink.

## Accessibility

WCAG 2.2 AA: 4.5:1 for text and 3:1 for graphical objects in both themes,
enforced by `tools/check_contrast.py`. No colour-only encoding: every agent also has a
marker shape, a text label and a fixed lane. Every SVG has `<title>`, `<desc>`,
`role="img"` and an `aria-label` reading the headline numbers. No text below
11 px; pass/fail is printed as a word as well as a colour. Annotations duplicate
printed facts, so skipping them loses nothing.

## Export and responsive rules

One self-contained `<svg>` per figure per theme: no `<image>`, script, external
font or CSS import; the XML namespace is the only URL and is an identifier, not a
fetch. `--check` compares bytes and text baselines are asserted inside the canvas
at build time. The viewBox scales with the container and reads best at 700–1000
px, the GitHub content width. Nothing reflows, because reflow would move
annotations and break byte identity; on narrow screens the fallback is the table
in `RESULTS.md`.

## Evolution and attribution

The philosophy — direct labels, editorial type, deterministic hand annotations,
system fonts — is adapted from
[`ddawnlll/goal-extension-benchmark`](https://github.com/ddawnlll/goal-extension-benchmark),
whose `chartkit.py` established the primitives and light/dark token mechanism.
This is a new design: canvas 920 → 1000 px, palette from two-hue indigo/amber to
three-agent blue/orange/violet, extended type scale, and three agents across
three tasks instead of two extensions across two pairs. The previous repository
was not modified. See [ATTRIBUTION.md](ATTRIBUTION.md).
