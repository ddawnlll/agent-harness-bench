#!/usr/bin/env python3
"""Design tokens and SVG primitives for the Agent Harness Bench figures.

Adapted from the chart system in ddawnlll/goal-extension-benchmark (same
philosophy: editorial data journalism, direct labels, deterministic hand-drawn
annotations, system fonts only, no external assets). This is a new design, not a
copy of that palette or layout.

Rules honoured here:
  * system font stacks only (GitHub renders SVG as an <img>: no webfonts);
  * data marks are exact, hand-drawn marks are annotations with seeded jitter;
  * every document carries title/desc/aria-label and a text alternative;
  * output is byte-deterministic: no clocks, no randomness outside fixed seeds.
"""
from __future__ import annotations

import math
import random
from xml.sax.saxutils import escape

FONT_DISPLAY = ("ui-serif, 'Iowan Old Style', 'Palatino Linotype', Palatino, "
                "Georgia, 'Times New Roman', serif")
FONT_TEXT = ("ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', "
             "Helvetica, Arial, sans-serif")
FONT_NUM = ("ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
            "'Liberation Mono', monospace")

T = {"micro": 11, "caption": 12.5, "body": 14, "subhead": 17,
     "headline": 24, "hero": 50, "mega": 64}

THEMES = {
    "light": {
        "surface": "#fbfaf7",
        "panel": "#f3f0e9",
        "ink": "#14171a",
        "muted": "#46505c",
        "faint": "#5c6675",
        "rule": "#e4dfd4",
        "rule_strong": "#cec7b8",
        "pi": "#2456c9",
        "pi_soft": "#e3e9fb",
        "jcode": "#a94f05",
        "jcode_soft": "#fbeed9",
        "hax": "#6d28a8",
        "hax_soft": "#efe6fa",
        "pass": "#0f6b60",
        "fail": "#a5261c",
        "warn": "#8a5a00",
        "wash": "#f0ece3",
        "on_accent": "#ffffff",
        "on_soft": "#14171a",
        "api": "#3a4553",
        "busy": "#c9c2b4",
        "cpu": "#0f6b60",
    },
    "dark": {
        "surface": "#0f1216",
        "panel": "#171c23",
        "ink": "#f2f0ea",
        "muted": "#bcc5d1",
        "faint": "#8d98a7",
        "rule": "#29313c",
        "rule_strong": "#3b4552",
        "pi": "#8fb3ff",
        "pi_soft": "#1b2745",
        "jcode": "#f6b45b",
        "jcode_soft": "#3a2a12",
        "hax": "#c9a6ff",
        "hax_soft": "#2a1f3d",
        "pass": "#5eead4",
        "fail": "#ff9a91",
        "warn": "#f6c177",
        "wash": "#151a21",
        "on_accent": "#0f1216",
        "on_soft": "#f2f0ea",
        "api": "#c2cbd8",
        "busy": "#44505f",
        "cpu": "#5eead4",
    },
}

AGENT_LABEL = {"pi-bolt": "Pi-Bolt", "jcode": "JCode", "hax": "Hax"}
AGENT_TOKEN = {"pi-bolt": "pi", "jcode": "jcode", "hax": "hax"}
AGENT_SOFT = {"pi-bolt": "pi_soft", "jcode": "jcode_soft", "hax": "hax_soft"}
AGENT_MARK = {"pi-bolt": "circle", "jcode": "square", "hax": "triangle"}

W = 1000
PAD = 30


def esc(s) -> str:
    return escape(str(s))


def paint(svg: str, theme: str) -> str:
    for key, value in THEMES[theme].items():
        svg = svg.replace("{%s}" % key, value)
    return svg


def text(x, y, s, size=T["body"], weight=400, fill="ink", anchor="start",
         font="text", extra="") -> str:
    family = {"display": FONT_DISPLAY, "text": FONT_TEXT, "num": FONT_NUM}[font]
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{{{fill}}}" text-anchor="{anchor}"{extra}>'
            f'{esc(s)}</text>')


def rect(x, y, w, h, fill, rx=0, extra="") -> str:
    r = f' rx="{rx}"' if rx else ""
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{max(h, 0):.1f}" '
            f'fill="{{{fill}}}"{r}{extra}/>')


def line(x1, y1, x2, y2, stroke="rule", width=1, extra="") -> str:
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{{{stroke}}}" stroke-width="{width}"{extra}/>')


def _stroke(stroke, width):
    if width and stroke != "none":
        return f' stroke="{{{stroke}}}" stroke-width="{width}"'
    return ' stroke="none"' if stroke == "none" else ""


def circle(cx, cy, r, fill="ink", stroke="none", width=0) -> str:
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{{{fill}}}"{_stroke(stroke, width)}/>'


def square(cx, cy, size, fill="ink", stroke="none", width=0) -> str:
    return (f'<rect x="{cx - size / 2:.1f}" y="{cy - size / 2:.1f}" width="{size:.1f}" '
            f'height="{size:.1f}" fill="{{{fill}}}"{_stroke(stroke, width)}/>')


def triangle(cx, cy, size, fill="ink", stroke="none", width=0) -> str:
    h = size * 0.9
    d = f"M{cx:.1f},{cy - h:.1f} L{cx + size:.1f},{cy + h * 0.7:.1f} L{cx - size:.1f},{cy + h * 0.7:.1f} Z"
    return f'<path d="{d}" fill="{{{fill}}}"{_stroke(stroke, width)}/>'


def marker(agent, cx, cy, size, fill, stroke="none", width=0):
    kind = AGENT_MARK[agent]
    if kind == "circle":
        return circle(cx, cy, size, fill, stroke, width)
    if kind == "square":
        return square(cx, cy, size * 1.8, fill, stroke, width)
    return triangle(cx, cy, size * 1.1, fill, stroke, width)


def path(d, stroke="ink", fill="none", width=1.4, extra="") -> str:
    fill_attr = ' fill="none"' if fill == "none" else f' fill="{{{fill}}}"'
    return (f'<path d="{d}"{fill_attr} stroke="{{{stroke}}}" stroke-width="{width}" '
            f'stroke-linecap="round" stroke-linejoin="round"{extra}/>')


# --------------------------------------------------------------- hand marks
def _wobble(rng, amp):
    return (rng.random() - 0.5) * 2 * amp


def scribble(points, seed, amp=1.0, samples=6, width=1.5, stroke="ink", opacity=0.85):
    rng = random.Random(seed)
    d = []
    for i, (x, y) in enumerate(points):
        d.append(("M" if i == 0 else "L") + f"{x + _wobble(rng, amp):.1f},{y + _wobble(rng, amp):.1f}")
        if i < len(points) - 1:
            nx, ny = points[i + 1]
            for k in range(1, samples + 1):
                t = k / (samples + 1)
                d.append(f"L{x + (nx - x) * t + _wobble(rng, amp):.1f},"
                         f"{y + (ny - y) * t + _wobble(rng, amp):.1f}")
    return path(" ".join(d), stroke=stroke, width=width, extra=f' opacity="{opacity}"')


def hand_underline(x1, x2, y, seed, amp=1.1):
    rng = random.Random(seed)
    lift = rng.uniform(1.0, 2.2)
    pts = [(x1, y), ((x1 + x2) / 2, y + lift), (x2, y - lift * 0.4)]
    return scribble(pts, seed + 1, amp=amp, samples=7)


def hand_circle(cx, cy, rx, ry, seed, amp=1.0):
    rng = random.Random(seed)
    d = []
    steps = 48
    for i in range(steps + 1):
        a = 2 * math.pi * i / steps
        wob = _wobble(rng, amp)
        x = cx + (rx + wob) * math.cos(a)
        y = cy + (ry + wob) * 0.58 * math.sin(a)
        d.append(("M" if i == 0 else "L") + f"{x:.1f},{y:.1f}")
    return path(" ".join(d), width=1.4, extra=' opacity="0.9"')


def hand_arrow(x1, y1, x2, y2, seed, bend=0.16, amp=0.8):
    rng = random.Random(seed)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    dx, dy = x2 - x1, y2 - y1
    cx, cy = mx - dy * bend + _wobble(rng, amp), my + dx * bend + _wobble(rng, amp)
    d = f"M{x1:.1f},{y1:.1f} Q{cx:.1f},{cy:.1f} {x2:.1f},{y2:.1f}"
    ang = math.atan2(y2 - cy, x2 - cx)
    for side in (+1, -1):
        a = ang + side * 0.40
        d += (f" M{x2:.1f},{y2:.1f} L{x2 - 9 * math.cos(a):.1f},"
              f"{y2 - 9 * math.sin(a):.1f}")
    return path(d, width=1.4, extra=' opacity="0.9"')


def frame(title, subtitle, aria, height, theme, meta="", kicker=None):
    """Common preamble: background, kicker, title, subtitle, hairline rule."""
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" '
        f'viewBox="0 0 {W} {height}" role="img" aria-label="{esc(aria)}">',
        f"<title>{esc(title)}</title>",
        f"<desc>{esc(aria)}</desc>",
        rect(0, 0, W, height, "surface"),
    ]
    y = PAD + 8
    if kicker:
        out.append(text(PAD, y, kicker, T["micro"], 700, "faint", extra=' letter-spacing="1.6"'))
        y += 26
    out.append(text(PAD, y, title, T["headline"], 650, "ink", font="display"))
    out.append(text(PAD, y + 22, subtitle, T["caption"], 400, "muted"))
    out.append(line(PAD, y + 40, W - PAD, y + 40, "rule"))
    if meta:
        out.append(text(W - PAD, height - 16, meta, T["micro"], 400, "faint", "end"))
    return out


def footer(height, s):
    return text(PAD, height - 16, s, T["micro"], 400, "faint")


def close():
    return "</svg>"


def x_scale(v, vmax, x0, x1):
    if vmax <= 0:
        return x0
    return x0 + (x1 - x0) * (v / vmax)


def ticks(vmax, n=4):
    return [vmax * i / n for i in range(n + 1)]


def collapsed_tick(vmax, n=4):
    if vmax >= 100:
        raw = vmax / n
        mag = 10 ** max(0, len(str(int(raw))) - 1)
        step = math.ceil(raw / mag) * mag
    else:
        step = vmax / n
    vals = []
    v = 0
    while v <= vmax + step * 0.001:
        vals.append(v)
        v += step
    return vals
