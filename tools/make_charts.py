#!/usr/bin/env python3
"""Generate the six flagship figures, light and dark, from verified data.

    python3 tools/make_charts.py            # write assets/*.svg
    python3 tools/make_charts.py --check    # regenerate in memory and compare

Inputs: results/publication-data.json (built by tools/derive_data.py from the
sealed evidence). No clocks, no network, no randomness except fixed annotation
seeds: output is byte-stable.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chartkit as K  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "results" / "publication-data.json"
ASSETS = ROOT / "assets"
FIGURES = ["hero", "race", "safety", "timing", "economics", "resources"]


def load():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if data.get("schema") != "agent-harness-bench/public-data/1":
        raise SystemExit("unexpected data schema")
    if len(data.get("trials", [])) != 9:
        raise SystemExit("expected 9 trials")
    if data["experiment"]["agents"] != ["pi-bolt", "jcode", "hax"]:
        raise SystemExit("unexpected agent set")
    if data["experiment"]["tasks"] != ["t1_order_lifecycle", "t2_replay_perf", "t3_coordinator"]:
        raise SystemExit("unexpected task set")
    return data


def trials_for(data, task=None, agent=None):
    rows = data["trials"]
    if task:
        rows = [t for t in rows if t["task_id"] == task]
    if agent:
        rows = [t for t in rows if t["agent_id"] == agent]
    return rows


def trial(data, task, agent):
    return next(t for t in data["trials"] if t["task_id"] == task and t["agent_id"] == agent)


def label(agent):
    return K.AGENT_LABEL[agent]


def token(agent):
    return K.AGENT_TOKEN[agent]


def soft(agent):
    return K.AGENT_SOFT[agent]


def fmt_int(v):
    return "—" if v is None else f"{int(v):,}"


def fmt_s(v):
    return "—" if v is None else (f"{v:,.0f} s" if v >= 10 else f"{v:.1f} s")


def fmt_pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def fmt_usd(v, digits=4):
    return "—" if v is None else f"${v:.{digits}f}"


def mib(v):
    return "—" if v is None else f"{v / 1048576:,.1f} MiB"


def check_frame(bits, height):
    """Assert every text baseline sits inside the canvas."""
    for b in bits:
        if " y=" in b:
            import re
            m = re.search(r'y="([\d.]+)"', b)
            if m and float(m.group(1)) > height - 4:
                raise SystemExit(f"text beyond canvas: {b[:120]}")


# --------------------------------------------------------------------- hero
def hero(data, theme):
    ag = data["agents"]
    h = 540
    verdict = data["verdict"]
    aria = (
        "Hero. Nine trials of three coding agents on the same model and tasks. "
        f"Pi-Bolt {ag['pi-bolt']['verified_passes']} of 3 verified, quality "
        f"{ag['pi-bolt']['quality_points']:.2f}; JCode {ag['jcode']['verified_passes']} of 3, "
        f"{ag['jcode']['quality_points']:.2f}; Hax {ag['hax']['verified_passes']} of 3, "
        f"{ag['hax']['quality_points']:.2f}. Provisional verdict JCode, low confidence. "
        "The difference is one stale-callback safety test."
    )
    out = K.frame(
        "Three coding agents. Nine trials. One critical difference.",
        "Same model, same limits, same three engineering tasks — graded by independent hidden tests.",
        aria, h, theme,
        kicker="AGENT HARNESS BENCH  ·  RESEARCH PREVIEW  ·  2026-11")
    top = 148
    colw, gap = 288, 38
    x0 = K.PAD
    for i, agent in enumerate(["pi-bolt", "jcode", "hax"]):
        a = ag[agent]
        x = x0 + i * (colw + gap)
        out.append(K.rect(x, top, colw, 236, soft(agent), 14))
        out.append(K.marker(agent, x + 22, top + 24, 5, token(agent)))
        out.append(K.text(x + 38, top + 29, label(agent), K.T["subhead"], 700, token(agent)))
        out.append(K.text(x + 22, top + 92, f"{a['verified_passes']}/3", 54, 700, "ink",
                          font="num"))
        out.append(K.text(x + 22, top + 114, "verified completions", K.T["micro"], 400, "muted"))
        rows = [
            ("quality", f"{a['quality_points']:.2f} / 300"),
            ("mean time, successful", f"{a['mean_success_wall_s']:.1f} s"),
            ("cost per verified", fmt_usd(a["cost_per_verified_usd"], 4)),
            ("total nominal spend", fmt_usd(a["nominal_cost_usd"], 4)),
        ]
        for j, (k, v) in enumerate(rows):
            y = top + 146 + j * 22
            out.append(K.text(x + 22, y, k, K.T["micro"], 400, "muted"))
            out.append(K.text(x + colw - 22, y, v, K.T["caption"], 650, "ink", "end", font="num"))
    # the decisive mark: JCode 3/3
    jx = x0 + (colw + gap) + 22 + 30
    out.append(K.hand_circle(jx, top + 78, 46, 34, seed=31, amp=0.9))
    out.append(K.hand_arrow(jx + 70, top - 32, jx + 14, top - 4, seed=32, bend=0.10))
    out.append(K.text(jx + 76, top - 36, "decided by one safety case", K.T["micro"], 600, "muted"))
    out.append(K.text(jx + 76, top - 21, "h07 · stale callback", K.T["micro"], 400, "faint", font="num"))

    # verdict band
    by = 410
    out.append(K.line(K.PAD, by - 18, K.W - K.PAD, by - 18, "rule"))
    out.append(K.text(K.PAD, by + 8, "PROVISIONAL VERDICT", K.T["micro"], 700, "faint",
                      extra=' letter-spacing="1.4"'))
    vy = by + 52
    out.append(K.text(K.PAD, vy, "JCode", 46, 700, "ink", font="display"))
    out.append(K.hand_underline(K.PAD - 2, K.PAD + 150, vy + 10, seed=7, amp=1.2))
    out.append(K.text(K.PAD + 176, vy - 6, "3 of 3 verified completions", K.T["body"], 600, "ink"))
    out.append(K.text(K.PAD + 176, vy + 14, "298.00 of 300 quality points", K.T["caption"], 400, "muted"))
    out.append(K.text(K.PAD + 176, vy + 32, "lowest nominal cost per verified task", K.T["caption"], 400, "muted"))
    out.append(K.rect(690, by + 20, 280, 68, "wash", 12))
    out.append(K.text(706, by + 44, "CONFIDENCE: LOW", K.T["caption"], 700, "warn"))
    out.append(K.text(706, by + 64, "one trial per cell · 3 tasks", K.T["micro"], 400, "muted"))
    out.append(K.text(706, by + 80, "no universal ranking claimed", K.T["micro"], 400, "muted"))
    out.append(K.footer(h, "Sealed run run-20261107T233553Z · deepseek-v4.1-flash, low effort · 2,006 s measured · nominal cost, not subscription cash"))
    check_frame(out, h)
    out.append(K.close())
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------- race
def race(data, theme):
    h = 548
    tasks = ["t1_order_lifecycle", "t2_replay_perf", "t3_coordinator"]
    short = {"t1_order_lifecycle": "T1 · cross-module safety",
             "t2_replay_perf": "T2 · replay performance",
             "t3_coordinator": "T3 · crash-safe coordinator"}
    aria = ("Nine-trial race. Each trial's wall-clock seconds to completion, grouped by task, "
            "with verified pass or fail. T1: JCode 206 seconds pass; Pi-Bolt 366 fail; Hax 192 fail. "
            "T2: Pi-Bolt 439, JCode 645, Hax 978, all pass. T3: Pi-Bolt 262, JCode 260, Hax 377, all pass.")
    out = K.frame(
        "The nine-trial race",
        "Wall-clock seconds to completion. Solid bar: verified pass. Hollow bar: critical gate failed.",
        aria, h, theme, kicker="FIGURE 2  ·  EXECUTION")
    top = 132
    panel_w = (K.W - 2 * K.PAD - 2 * 24) / 3
    row_h = 46
    for ti, task in enumerate(tasks):
        px = K.PAD + ti * (panel_w + 24)
        out.append(K.text(px, top, short[task], K.T["caption"], 700, "ink"))
        rows = trials_for(data, task=task)
        vmax = max((t["elapsed_s"] or 0) for t in rows)
        vmax = math.ceil(vmax / 60) * 60 if vmax > 120 else math.ceil(vmax / 30) * 30
        # axis
        for tick in (0, vmax / 2, vmax):
            gx = K.x_scale(tick, vmax, px + 46, px + panel_w - 54)
            out.append(K.line(gx, top + 22, gx, top + 22 + row_h * 3, "rule"))
            out.append(K.text(gx, top + 22 + row_h * 3 + 16, f"{tick:.0f}s", K.T["micro"], 400,
                              "faint", "middle"))
        for ri, agent in enumerate(["pi-bolt", "jcode", "hax"]):
            t = trial(data, task, agent)
            y = top + 42 + ri * row_h
            bar_x0 = px + 46
            bar_x1 = px + panel_w - 54
            w = K.x_scale(t["elapsed_s"], vmax, 0, bar_x1 - bar_x0)
            out.append(K.text(px, y + 4, label(agent), K.T["micro"], 600, token(agent)))
            passed = t["verified_pass"]
            if passed:
                out.append(K.rect(bar_x0, y - 7, w, 14, token(agent), 3))
            else:
                out.append(K.rect(bar_x0, y - 7, w, 14, "surface", 3,
                                  ' stroke="{%s}" stroke-width="1.6"' % token(agent)))
            # API service portion (measured sum; may overlap) shown as a darker underlay
            api_w = K.x_scale(min(t["api_service_s"], t["elapsed_s"]), vmax, 0, bar_x1 - bar_x0)
            out.append(K.line(bar_x0, y + 12, bar_x0 + api_w, y + 12, "api", 2,
                              ' opacity="0.45"'))
            out.append(K.text(bar_x0 + w + 6, y + 4, f"{t['elapsed_s']:.0f}", K.T["micro"], 650,
                              "ink", font="num"))
            tag = "PASS" if passed else "FAIL"
            out.append(K.text(bar_x0, y + 27, tag, K.T["micro"], 700,
                              "pass" if passed else "fail"))
            if not passed:
                case = next((c for c in (t["cases"] or []) if c["id"] == "h07"), None)
                if case:
                    out.append(K.text(bar_x0 + 36, y + 27, "h07 stale callback", K.T["micro"], 400, "faint"))
    # annotation, placed under the T1 axis so it cannot collide with T2
    t1x = K.PAD
    jy = top + 42 + 1 * row_h
    out.append(K.hand_arrow(t1x + 150, top + 42 + 3 * row_h + 54, t1x + 130, jy + 12, seed=41, bend=0.10))
    out.append(K.text(t1x + 158, top + 42 + 3 * row_h + 58,
                      "only JCode rejected the stale version", K.T["micro"], 600, "muted"))
    # legend
    ly = top + 42 + row_h * 3 + 86
    out.append(K.rect(K.PAD, ly - 9, 22, 10, "api", 2))
    out.append(K.text(K.PAD + 30, ly, "API service time (sum of request durations; spans may overlap)",
                      K.T["micro"], 400, "faint"))
    out.append(K.text(K.PAD, ly + 24,
                      "JCode's CLI emitted no tool-level trace; tool activity is unknown and not inferred.",
                      K.T["micro"], 400, "faint"))
    out.append(K.footer(h, "Elapsed = barrier release to agent exit; fill encodes pass/fail in addition to the printed label."))
    check_frame(out, h)
    out.append(K.close())
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------- safety
def safety(data, theme):
    h = 620
    aria = ("Safety case h07. After a fill of 10 at 100 and an accepted stop acknowledgement, "
            "protection is ACTIVE at version 1. A stale callback at version 0 reports CANCELLED. "
            "JCode ignores it because the version is not newer; protection stays ACTIVE. "
            "Pi-Bolt and Hax apply it, leaving the open position unprotected, and fail the gate.")
    out = K.frame(
        "Why one stale callback decided the ranking",
        "Hidden case h07: a venue callback at version 0 arrives after an accepted stop at version 1.",
        aria, h, theme, kicker="FIGURE 3  ·  SAFETY CASE")
    top = 140
    lane_h = 106
    lanes = [
        ("jcode", "JCode", "keeps ACTIVE", "PASS", True),
        ("pi-bolt", "Pi-Bolt", "applies stale callback", "FAIL h07", False),
        ("hax", "Hax", "applies stale callback", "FAIL h07", False),
    ]
    step_x = [190, 360, 560, 760]
    headers = ["1 · fill 10 @ 100", "2 · stop ACK accepted (v1)", "3 · stale callback v0", "4 · state"]
    for sx, head in zip(step_x, headers):
        out.append(K.text(sx, top - 22, head, K.T["micro"], 650, "muted"))
    for li, (agent, name, detail, outcome, ok) in enumerate(lanes):
        y = top + li * lane_h
        out.append(K.rect(K.PAD, y - 18, K.W - 2 * K.PAD, lane_h - 18, soft(agent), 10))
        out.append(K.marker(agent, K.PAD + 18, y + 6, 5, token(agent)))
        out.append(K.text(K.PAD + 34, y + 11, name, K.T["subhead"], 700, token(agent)))
        out.append(K.text(K.PAD + 34, y + 31, detail, K.T["micro"], 400, "muted"))
        # step chips
        chips = ["position +10", "ACTIVE", "v0 CANCELLED", "ACTIVE · protected" if ok else "CANCELLED · unprotected"]
        for ci, (cx, chip) in enumerate(zip(step_x, chips)):
            fill = "wash" if ci < 3 else ("pass" if ok else "fail")
            stroke = "rule_strong" if ci < 3 else ("pass" if ok else "fail")
            color = "ink" if ci < 3 else "on_accent"
            w = 128 if ci < 3 else 168
            out.append(K.rect(cx, y - 8, w, 28, fill, 6, ' stroke="{%s}" stroke-width="1"' % stroke))
            out.append(K.text(cx + w / 2, y + 10, chip, K.T["micro"], 650, color, "middle"))
        out.append(K.text(step_x[3] + 172, y + 10, outcome, K.T["caption"], 750,
                          "pass" if ok else "fail"))
        # arrows
        for a, b in zip(step_x[:-1], step_x[1:]):
            out.append(K.hand_arrow(a + 130, y + 6, b - 6, y + 6, seed=60 + li, bend=0.05, amp=0.5))
    # invariant
    iy = top + 3 * lane_h + 16
    out.append(K.line(K.PAD, iy, K.W - K.PAD, iy, "rule"))
    out.append(K.text(K.PAD, iy + 30, "The invariant", K.T["subhead"], 700, "ink", font="display"))
    out.append(K.text(K.PAD, iy + 56,
                      "A position is protected only when an ACTIVE stop covers exactly the open quantity.",
                      K.T["body"], 400, "ink"))
    out.append(K.text(K.PAD, iy + 78,
                      "A stale callback is not evidence of loss. Applying it makes an unprotected position look protected.",
                      K.T["body"], 400, "ink"))
    out.append(K.text(K.PAD, iy + 104,
                      "Grader assertion: status ACTIVE, is_protected true, halted false. Pi-Bolt and Hax failed this case.",
                      K.T["micro"], 400, "faint"))
    out.append(K.footer(h, "Values 10 @ 100, stop 95, versions 0/1 and state CANCELLED are the literal fixtures of hidden case h07."))
    check_frame(out, h)
    out.append(K.close())
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------- timing
def timing(data, theme):
    h = 560
    aria = ("Time decomposition per trial. Measured API service time and the derived remainder of the "
            "agent's wall clock, with CPU seconds marked. T2 dominates elapsed time for all agents.")
    out = K.frame(
        "Where the time went — and what is actually measured",
        "Measured API service time plus the derived remainder of each trial's wall clock.",
        aria, h, theme, kicker="FIGURE 4  ·  EXECUTION TIME")
    top = 136
    tasks = ["t1_order_lifecycle", "t2_replay_perf", "t3_coordinator"]
    row_h = 40
    panel_w = (K.W - 2 * K.PAD - 2 * 26) / 3
    for ti, task in enumerate(tasks):
        px = K.PAD + ti * (panel_w + 26)
        out.append(K.text(px, top - 8, {"t1_order_lifecycle": "T1", "t2_replay_perf": "T2",
                                        "t3_coordinator": "T3"}[task], K.T["subhead"], 700, "ink",
                          font="display"))
        rows = trials_for(data, task=task)
        vmax = max(t["elapsed_s"] for t in rows)
        vmax = math.ceil(vmax / 60) * 60
        ax0, ax1 = px + 56, px + panel_w - 46
        for tick in (0, vmax / 2, vmax):
            gx = K.x_scale(tick, vmax, ax0, ax1)
            out.append(K.line(gx, top + 4, gx, top + 4 + row_h * 3, "rule"))
            out.append(K.text(gx, top + 4 + row_h * 3 + 14, f"{tick:.0f}s", K.T["micro"], 400,
                              "faint", "middle"))
        for ri, agent in enumerate(["pi-bolt", "jcode", "hax"]):
            t = trial(data, task, agent)
            y = top + 18 + ri * row_h
            out.append(K.text(px, y + 4, label(agent), K.T["micro"], 600, token(agent)))
            api = min(t["api_service_s"], t["elapsed_s"])
            w_api = K.x_scale(api, vmax, 0, ax1 - ax0)
            w_all = K.x_scale(t["elapsed_s"], vmax, 0, ax1 - ax0)
            out.append(K.rect(ax0, y - 6, w_api, 12, "api", 2))
            out.append(K.rect(ax0 + w_api, y - 6, max(0, w_all - w_api), 12, "busy", 2))
            cpu_x = K.x_scale(min(t["cpu_s"], t["elapsed_s"]), vmax, 0, ax1 - ax0)
            out.append(K.circle(ax0 + cpu_x, y, 3.2, "cpu", "surface", 1))
            out.append(K.text(ax0 + w_all + 5, y + 4, f"{t['elapsed_s']:.0f} s", K.T["micro"], 650,
                              "ink", font="num"))
    ly = top + 4 + row_h * 3 + 40
    out.append(K.rect(K.PAD, ly - 9, 22, 10, "api", 2))
    out.append(K.text(K.PAD + 30, ly, "API service time · exact sum of request durations (spans may overlap)",
                      K.T["micro"], 400, "faint"))
    out.append(K.rect(K.PAD + 430, ly - 9, 22, 10, "busy", 2))
    out.append(K.text(K.PAD + 460, ly, "remainder · derived as wall clock minus API sum", K.T["micro"], 400, "faint"))
    out.append(K.circle(K.PAD + 762, ly - 4, 3.4, "cpu"))
    out.append(K.text(K.PAD + 774, ly, "CPU seconds · cgroup accounting", K.T["micro"], 400, "faint"))
    out.append(K.text(K.PAD, ly + 26,
                      "The remainder is not a tool-level phase trace: JCode exposes no tool events, and API spans can overlap.",
                      K.T["micro"], 400, "faint"))
    out.append(K.footer(h, "No phase categories are invented. Exact values: results/publication-data.json · api_service_s, elapsed_s, cpu_s."))
    check_frame(out, h)
    out.append(K.close())
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- economics
def economics(data, theme):
    h = 540
    ag = data["agents"]
    total_tokens = sum(ag[a]["tokens"]["total"] for a in ag)
    aria = ("Token and cost comparison. Pi-Bolt 2.30 million tokens and 0.084 dollars nominal; "
            "JCode 4.32 million and 0.096; Hax 4.89 million and 0.109. Cost per verified task: "
            "JCode 0.0318, Pi-Bolt 0.0419, Hax 0.0543.")
    out = K.frame(
        "Token volume is not the same as verified value",
        "Provider-reported tokens and nominal list-price cost, with cost per verified completion.",
        aria, h, theme, kicker="FIGURE 5  ·  ECONOMICS")
    # left: tokens stacked
    lx, ly = K.PAD, 136
    out.append(K.text(lx, ly, "Total tokens per agent", K.T["subhead"], 700, "ink", font="display"))
    out.append(K.text(lx, ly + 18, "input + output; cached reads reported separately", K.T["micro"], 400, "faint"))
    max_tok = max(ag[a]["tokens"]["total"] for a in ag)
    barw = 250
    for i, a in enumerate(["pi-bolt", "jcode", "hax"]):
        y = ly + 52 + i * 62
        inp, outp = ag[a]["tokens"]["input"], ag[a]["tokens"]["output"]
        total = inp + outp
        wi = barw * inp / max_tok
        wo = barw * outp / max_tok
        out.append(K.text(lx, y + 10, label(a), K.T["caption"], 700, token(a)))
        out.append(K.rect(lx + 70, y, wi, 20, token(a), 2))
        out.append(K.rect(lx + 70 + wi, y, max(2, wo), 20, "wash", 2))
        out.append(K.text(lx + 78 + wi + wo, y + 15, f"{total/1e6:.2f}M", K.T["body"], 700, "ink", font="num"))
        out.append(K.text(lx + 70, y + 34, f"in {inp/1e6:.2f}M · out {outp/1e3:.0f}k · cached {ag[a]['tokens']['cached_read']/1e6:.2f}M",
                          K.T["micro"], 400, "faint"))
    # right: cost per verified
    rx = 520
    out.append(K.text(rx, ly, "Cost per verified completion", K.T["subhead"], 700, "ink", font="display"))
    out.append(K.text(rx, ly + 18, "nominal list price ÷ verified passes; lower is better", K.T["micro"], 400, "faint"))
    max_cost = max((ag[a]["cost_per_verified_usd"] or 0) for a in ag)
    for i, a in enumerate(["pi-bolt", "jcode", "hax"]):
        y = ly + 52 + i * 62
        val = ag[a]["cost_per_verified_usd"]
        w = (rx + 340 - rx - 110) * (val / max_cost) if max_cost else 0
        out.append(K.text(rx, y + 10, label(a), K.T["caption"], 700, token(a)))
        out.append(K.rect(rx + 78, y, w, 20, token(a), 2))
        out.append(K.text(rx + 84 + w, y + 15, fmt_usd(val, 4), K.T["body"], 700, "ink", font="num"))
        out.append(K.text(rx + 78, y + 34, f"total nominal {fmt_usd(ag[a]['nominal_cost_usd'], 4)} · allowance {fmt_usd(ag[a]['allowance_usd'], 4)}",
                          K.T["micro"], 400, "faint"))
    # annotation, below the three cost rows
    out.append(K.hand_underline(rx + 76, rx + 190, ly + 234, seed=71, amp=1.0))
    out.append(K.text(rx, ly + 256, "JCode spends less per verified task,", K.T["micro"], 600, "muted"))
    out.append(K.text(rx, ly + 272, "but Pi-Bolt has the lowest total spend: it completed less work.",
                      K.T["micro"], 400, "faint"))
    # quality per million tokens
    qy = ly + 310
    out.append(K.line(K.PAD, qy - 18, K.W - K.PAD, qy - 18, "rule"))
    out.append(K.text(K.PAD, qy + 8, "Quality / million tokens", K.T["caption"], 700, "muted"))
    for i, a in enumerate(["pi-bolt", "jcode", "hax"]):
        q = ag[a]["quality_per_million_tokens"]
        x = K.PAD + 240 + i * 220
        out.append(K.text(x, qy + 8, f"{q:.1f}", K.T["subhead"], 700, "ink", font="num"))
        out.append(K.text(x, qy + 26, label(a), K.T["micro"], 400, token(a)))
    out.append(K.footer(h, f"Total {total_tokens/1e6:.2f}M tokens. Nominal cost is list-price accounting on a subscription, not incremental cash."))
    check_frame(out, h)
    out.append(K.close())
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- resources
def resources(data, theme):
    h = 576
    ag = data["agents"]
    aria = ("Resource comparison. Peak cgroup memory per trial stays far below the 3 GiB ceiling. "
            "CPU seconds range from 202 for Pi-Bolt to 668 for Hax. All nine trials completed with no "
            "timeouts, OOM kills or throttling.")
    out = K.frame(
        "Resource use and reliability",
        "cgroup v2 peak memory per trial and total CPU seconds; the ceiling was shared fairly.",
        aria, h, theme, kicker="FIGURE 6  ·  COMPUTE")
    # left: memory peaks per trial (axis 0-120 MiB; the 3 GiB ceiling is off-scale)
    lx, ly = K.PAD, 138
    out.append(K.text(lx, ly, "Peak memory per trial", K.T["subhead"], 700, "ink", font="display"))
    out.append(K.text(lx, ly + 18, "cgroup memory.peak in MiB; 3 GiB ceiling is off-scale (≈25–40× headroom)",
                      K.T["micro"], 400, "faint"))
    tasks = ["t1_order_lifecycle", "t2_replay_perf", "t3_coordinator"]
    ax0, ax1 = lx + 66, lx + 400
    max_mib = 120
    for tick in (0, 40, 80, 120):
        gx = K.x_scale(tick, max_mib, ax0, ax1)
        out.append(K.line(gx, ly + 40, gx, ly + 300, "rule"))
        out.append(K.text(gx, ly + 316, f"{tick}", K.T["micro"], 400, "faint", "middle"))
    for i, agent in enumerate(["pi-bolt", "jcode", "hax"]):
        out.append(K.text(lx, ly + 46 + i * 92, label(agent), K.T["caption"], 600, token(agent)))
        for j, task in enumerate(tasks):
            t = trial(data, task, agent)
            m = t["peak_mem_bytes"] / 1048576
            y = ly + 66 + i * 92 + j * 18
            w = K.x_scale(m, max_mib, 0, ax1 - ax0)
            out.append(K.rect(ax0, y - 5, w, 10, token(agent), 2, ' opacity="0.85"'))
            out.append(K.text(ax0 + w + 5, y + 4, f"{m:,.1f}", K.T["micro"], 600, "ink", font="num"))
            out.append(K.text(lx + 66, y + 4, task[1:3].upper(), K.T["micro"], 400, "faint", "end"))
    out.append(K.text(lx, ly + 350, "bars: T1 / T2 / T3 per agent. Mean peaks: "
                      + " · ".join(f"{label(a)} {ag[a]['mean_peak_mem_bytes']/1048576:.1f}" for a in ['pi-bolt','jcode','hax'])
                      + " MiB", K.T["micro"], 400, "faint"))
    # right: CPU
    rx = 540
    out.append(K.text(rx, ly, "CPU seconds, all three trials", K.T["subhead"], 700, "ink", font="display"))
    out.append(K.text(rx, ly + 18, "cgroup cpu.stat; total across three trials", K.T["micro"], 400, "faint"))
    max_cpu = max(ag[a]["total_cpu_s"] for a in ag)
    for i, agent in enumerate(["pi-bolt", "jcode", "hax"]):
        y = ly + 60 + i * 44
        val = ag[agent]["total_cpu_s"]
        w = 300 * val / max_cpu
        out.append(K.text(rx, y + 4, label(agent), K.T["caption"], 600, token(agent)))
        out.append(K.rect(rx + 74, y - 8, w, 16, token(agent), 3))
        out.append(K.text(rx + 80 + w, y + 4, f"{val:.1f} s", K.T["micro"], 650, "ink", font="num"))
    out.append(K.text(rx, ly + 210, "Reliability", K.T["caption"], 700, "muted"))
    stats = [
        ("trials completed", "9 / 9"),
        ("timeouts", "0"),
        ("OOM kills", "0"),
        ("CPU throttled", "0.000 s"),
    ]
    for i, (k, v) in enumerate(stats):
        y = ly + 232 + i * 20
        out.append(K.text(rx, y, k, K.T["micro"], 400, "faint"))
        out.append(K.text(rx + 300, y, v, K.T["micro"], 650, "ink", "end", font="num"))
    out.append(K.text(K.PAD, ly + 374,
                      "Memory accounting is cgroup-level and charges children and subagents; it is not process RSS.",
                      K.T["micro"], 400, "faint"))
    out.append(K.footer(h, "Values from cgroup_final.txt and resource samples. Limits: MemoryMax=3 GiB, CPUQuota=200%."))
    check_frame(out, h)
    out.append(K.close())
    return "\n".join(out) + "\n"


GENERATORS = {
    "hero": hero, "race": race, "safety": safety,
    "timing": timing, "economics": economics, "resources": resources,
}


def build(theme):
    data = load()
    out = {}
    for name in FIGURES:
        svg = GENERATORS[name](data, theme)
        svg = K.paint(svg, theme)
        if "{" in svg.split("</svg>")[0].replace("{", "", 0):
            # unresolved token placeholders would render as literal braces
            import re
            leftovers = re.findall(r"\{[a-z_]+\}", svg)
            if leftovers:
                raise SystemExit(f"{name}-{theme}: unresolved tokens {set(leftovers)}")
        out[f"{name}-{theme}.svg"] = svg
    return out


def main(argv):
    check = "--check" in argv
    built = {}
    for theme in ("light", "dark"):
        built.update(build(theme))
    if check:
        failures = []
        for name, content in sorted(built.items()):
            path = ASSETS / name
            if not path.exists():
                failures.append(f"missing {name}")
            elif path.read_text(encoding="utf-8") != content:
                failures.append(f"differs {name}")
        if failures:
            print("CHART CHECK FAILED")
            for f in failures:
                print("  ", f)
            return 1
        print(f"CHART CHECK OK: {len(built)} SVGs byte-identical")
        return 0
    ASSETS.mkdir(parents=True, exist_ok=True)
    for name, content in sorted(built.items()):
        (ASSETS / name).write_text(content, encoding="utf-8")
    print(f"wrote {len(built)} SVGs to {ASSETS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
