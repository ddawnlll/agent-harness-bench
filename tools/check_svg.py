#!/usr/bin/env python3
"""Structural and accessibility checks for every published SVG."""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
NS = "{http://www.w3.org/2000/svg}"
PAIRS = ["hero", "race", "safety", "timing", "economics", "resources"]


def fail(msg, failures):
    failures.append(msg)


def strip_style(elems):
    """Tag/geometry signature so light and dark variants must match exactly."""
    out = []
    for el in elems:
        tag = el.tag.replace(NS, "")
        attrs = {k: v for k, v in el.attrib.items()
                 if k not in {"fill", "stroke", "opacity", "style", "flood-color",
                              "stop-color", "stroke-opacity", "fill-opacity"}}
        out.append((tag, tuple(sorted(attrs.items())), (el.text or "").strip()))
    return out


def main():
    failures = []
    if not ASSETS.is_dir():
        print("no assets directory", file=sys.stderr)
        return 1
    files = sorted(ASSETS.glob("*.svg"))
    expected = {f"{name}-{theme}.svg" for name in PAIRS for theme in ("light", "dark")}
    present = {p.name for p in files}
    for missing in sorted(expected - present):
        fail(f"missing figure {missing}", failures)
    for extra in sorted(present - expected):
        fail(f"unexpected svg {extra}", failures)

    for p in files:
        try:
            tree = ET.parse(p)
        except ET.ParseError as exc:
            fail(f"{p.name}: XML parse error: {exc}", failures)
            continue
        root = tree.getroot()
        if root.tag != NS + "svg":
            fail(f"{p.name}: root is {root.tag}", failures)
            continue
        w, h = root.attrib.get("width"), root.attrib.get("height")
        if w != "1000" or not h:
            fail(f"{p.name}: unexpected size {w}x{h}", failures)
        if not root.attrib.get("role") == "img" or not root.attrib.get("aria-label"):
            fail(f"{p.name}: missing role=img or aria-label", failures)
        title = root.find(NS + "title")
        desc = root.find(NS + "desc")
        if title is None or not (title.text or "").strip():
            fail(f"{p.name}: missing <title>", failures)
        if desc is None or len((desc.text or "").strip()) < 40:
            fail(f"{p.name}: missing/short <desc>", failures)
        # no scripts, no external resources
        for el in root.iter():
            tag = el.tag.replace(NS, "")
            if tag == "script":
                fail(f"{p.name}: contains <script>", failures)
            if tag == "image":
                fail(f"{p.name}: contains <image>", failures)
            for key, val in el.attrib.items():
                if key in {"href", "xlink:href"} and "://" in val:
                    fail(f"{p.name}: external reference {val}", failures)
            if tag in {"text", "tspan"}:
                size = float(el.attrib.get("font-size", "0") or 0)
                if size and size < 11:
                    fail(f"{p.name}: text below 11px ({size})", failures)
        text_nodes = [e for e in root.iter() if e.tag == NS + "text"]
        if len(text_nodes) < 15:
            fail(f"{p.name}: suspiciously few text labels ({len(text_nodes)})", failures)
        raw = p.read_text(encoding="utf-8")
        if re.search(r"\{[a-z_]+\}", raw):
            fail(f"{p.name}: unresolved colour token", failures)
        if "http://" in raw and "www.w3.org/2000/svg" not in raw:
            fail(f"{p.name}: unexpected external URL", failures)
        if "http://" in raw.replace("http://www.w3.org/2000/svg", ""):
            fail(f"{p.name}: unexpected external URL", failures)

    for name in PAIRS:
        lp, dp = ASSETS / f"{name}-light.svg", ASSETS / f"{name}-dark.svg"
        if not (lp.exists() and dp.exists()):
            continue
        try:
            lroot = ET.parse(lp).getroot()
            droot = ET.parse(dp).getroot()
        except ET.ParseError:
            continue
        if lroot.attrib.get("height") != droot.attrib.get("height"):
            fail(f"{name}: light/dark heights differ", failures)
        if strip_style(lroot) != strip_style(droot):
            fail(f"{name}: light/dark structure differs beyond colour", failures)
        if len(lp.read_bytes()) == 0:
            fail(f"{name}: empty light file", failures)

    if failures:
        print("SVG CHECK FAILED")
        for f in failures:
            print("  -", f)
        return 1
    print(f"SVG CHECK OK: {len(files)} files, 6 light/dark pairs, structural parity verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
