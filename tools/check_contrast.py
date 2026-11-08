#!/usr/bin/env python3
"""WCAG 2.x contrast audit for the figure palette (both themes)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chartkit import THEMES  # noqa: E402


def lum(color: str) -> float:
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a: str, b: str) -> float:
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


TEXT_PAIRS = [
    ("ink", "surface"), ("ink", "panel"), ("ink", "wash"),
    ("ink", "pi_soft"), ("ink", "jcode_soft"), ("ink", "hax_soft"),
    ("muted", "surface"), ("muted", "panel"),
    ("faint", "surface"), ("faint", "panel"),
    ("on_accent", "pi"), ("on_accent", "jcode"), ("on_accent", "hax"),
    ("on_accent", "pass"), ("on_accent", "fail"),
    ("on_soft", "pi_soft"), ("on_soft", "jcode_soft"), ("on_soft", "hax_soft"),
]
GRAPHIC_PAIRS = [
    ("pi", "surface"), ("jcode", "surface"), ("hax", "surface"),
    ("pass", "surface"), ("fail", "surface"), ("api", "surface"),
    ("pi", "pi_soft"), ("jcode", "jcode_soft"), ("hax", "hax_soft"),
]
EXEMPT = [("busy", "surface"), ("rule", "surface"), ("rule_strong", "surface")]


def main() -> int:
    failures = 0
    for theme, tokens in THEMES.items():
        print(theme)
        for fg, bg in TEXT_PAIRS:
            r = ratio(tokens[fg], tokens[bg])
            ok = r >= 4.5
            failures += 0 if ok else 1
            print(f"  text    {fg:10} on {bg:11} {r:5.2f}  {'ok' if ok else 'FAIL'}")
        for fg, bg in GRAPHIC_PAIRS:
            r = ratio(tokens[fg], tokens[bg])
            ok = r >= 3.0
            failures += 0 if ok else 1
            print(f"  graphic {fg:10} on {bg:11} {r:5.2f}  {'ok' if ok else 'FAIL'}")
        for fg, bg in EXEMPT:
            r = ratio(tokens[fg], tokens[bg])
            print(f"  exempt  {fg:10} on {bg:11} {r:5.2f}  (always accompanied by a direct label)")
    if failures:
        print(f"CONTRAST FAILED: {failures} pair(s)")
        return 1
    print("CONTRAST OK: all enforced pairs meet WCAG AA thresholds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
