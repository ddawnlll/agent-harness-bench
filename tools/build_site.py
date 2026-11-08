#!/usr/bin/env python3
"""Build the static, self-contained research site into site/.

    python3 tools/build_site.py            # write site/
    python3 tools/build_site.py --check    # verify committed output is current

The page embeds both theme variants of the six SVG figures, the normalized
dataset, and all CSS/JS. No CDN, no webfonts, no runtime backend.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
DATA = ROOT / "results" / "publication-data.json"
SITE = ROOT / "site"
REPORT = ROOT / "reports" / "AGENT_BENCHMARK_FORENSIC_REPORT.html"

FIGURES = [
    ("hero", "The result in one frame",
     "Three agents, nine trials, one decisive safety case."),
    ("race", "The nine-trial race",
     "Wall-clock time and verified outcome for every trial."),
    ("safety", "The safety case that decided it",
     "A stale venue callback must never downgrade active protection."),
    ("timing", "Where the time went",
     "Measured API service time and the derived remainder of each trial."),
    ("economics", "Token volume is not verified value",
     "Tokens, nominal list-price cost, and cost per verified completion."),
    ("resources", "Compute and reliability",
     "cgroup memory peaks and CPU seconds, far below the enforced ceilings."),
]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fmt_s(v):
    return f"{v:,.0f} s" if v >= 10 else f"{v:.1f} s"


def usd(v, d=4):
    return "—" if v is None else f"${v:.{d}f}"


def build(data: dict, assets: dict, report: bytes) -> dict:
    ag = data["agents"]
    verdict = data["verdict"]
    trials = data["trials"]
    figs = {}
    for name, title, desc in FIGURES:
        light = assets[f"{name}-light.svg"]
        dark = assets[f"{name}-dark.svg"]
        figs[name] = f'''<figure class="figure" id="fig-{name}">
  <figcaption><span class="fig-title">{title}</span><span class="fig-desc">{desc}</span></figcaption>
  <div class="fig-light">{light}</div>
  <div class="fig-dark">{dark}</div>
  <div class="fig-tools"><a href="assets/{name}-light.svg" download>light SVG</a><a href="assets/{name}-dark.svg" download>dark SVG</a></div>
  <div class="scroll-hint">Chart labels stay legible: scroll the figure horizontally on narrow screens.</div>
</figure>'''

    rows = []
    for t in trials:
        rows.append(f'''<tr data-task="{t['task_id']}" data-agent="{t['agent_id']}" data-pass="{str(t['verified_pass']).lower()}">
  <td>{t['task']}</td><td><span class="agent {t['agent_id']}">{t['agent_id']}</span></td>
  <td><span class="status {'pass' if t['verified_pass'] else 'fail'}">{'PASS' if t['verified_pass'] else 'FAIL'}</span></td>
  <td class="num">{t['score']:.2f}</td><td class="num">{fmt_s(t['elapsed_s'])}</td>
  <td class="num">{t['cpu_s']:.1f} s</td><td class="num">{t['peak_mem_bytes']/1048576:.1f} MiB</td>
  <td class="num">{t['tokens']['total']:,}</td><td class="num">{usd(t['tokens']['list_cost_usd'],5)}</td>
  <td class="num">{t['requests']}</td></tr>''')

    cards = []
    for agent in ["pi-bolt", "jcode", "hax"]:
        a = ag[agent]
        cards.append(f'''<div class="agent-card {agent}">
  <div class="agent-name"><span class="mark {agent}"></span>{agent}</div>
  <div class="big">{a['verified_passes']}<span class="den">/3</span></div>
  <div class="card-sub">verified completions</div>
  <dl>
    <dt>quality</dt><dd>{a['quality_points']:.2f} / 300</dd>
    <dt>mean successful wall</dt><dd>{a['mean_success_wall_s']:.1f} s</dd>
    <dt>cost / verified</dt><dd>{usd(a['cost_per_verified_usd'])}</dd>
    <dt>CPU seconds</dt><dd>{a['total_cpu_s']:.1f}</dd>
  </dl></div>''')

    table_json = json.dumps(data, separators=(",", ":"))
    html = TEMPLATE
    replacements = {
        "{{FIG_HERO}}": figs["hero"], "{{FIG_RACE}}": figs["race"],
        "{{FIG_SAFETY}}": figs["safety"], "{{FIG_TIMING}}": figs["timing"],
        "{{FIG_ECON}}": figs["economics"], "{{FIG_RES}}": figs["resources"],
        "{{AGENT_CARDS}}": "\n".join(cards), "{{TRIAL_ROWS}}": "\n".join(rows),
        "{{VERDICT}}": verdict["recommendation"].replace("_", " ").title(),
        "{{HEADLINE}}": verdict["headline"],
        "{{RUN_ID}}": data["experiment"]["run_id"],
        "{{WALL}}": f"{data['experiment']['measured_wall_s']:,.0f}",
        "{{MODEL}}": data["experiment"]["model"],
        "{{EFFORT}}": data["experiment"]["reasoning_effort"],
        "{{DATA_JSON}}": table_json,
        "{{REPORT_HASH}}": sha(report)[:16],
        "{{FIG_COUNT}}": str(len(FIGURES)),
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    return {"html": html, "report": report, "data": data}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    data = json.loads(DATA.read_text(encoding="utf-8"))
    assets = {p.name: p.read_text(encoding="utf-8") for p in sorted(ASSETS.glob("*.svg"))}
    if len(assets) != 12:
        raise SystemExit(f"expected 12 SVG assets, found {len(assets)}")
    report = REPORT.read_bytes()
    built = build(data, assets, report)
    outputs = {
        SITE / "index.html": built["html"].encode("utf-8"),
        SITE / "report.html": report,
        SITE / "data" / "publication-data.json": (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    }
    for name, content in assets.items():
        outputs[SITE / "assets" / name] = content.encode("utf-8")
    if args.check:
        failures = []
        for path, content in outputs.items():
            if not path.exists() or path.read_bytes() != content:
                failures.append(str(path.relative_to(ROOT)))
        if failures:
            print("SITE CHECK FAILED:", ", ".join(failures))
            return 1
        print(f"SITE CHECK OK: {len(outputs)} files byte-identical")
        return 0
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    print(f"wrote site with {len(outputs)} files ({len(built['html'])/1024:.0f} KiB HTML)")
    return 0


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="auto">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent Harness Bench — three agents, nine trials, one critical difference</title>
<meta name="description" content="An evidence-first benchmark of Pi-Bolt, JCode and Hax on the same model and three engineering tasks. Independent hidden graders, cgroup telemetry, exact costs and a safety case study.">
<style>
:root{
  --surface:#fbfaf7; --panel:#f3f0e9; --ink:#14171a; --muted:#46505c; --faint:#5c6675;
  --rule:#e4dfd4; --rule2:#cec7b8; --pi:#2456c9; --jcode:#a94f05; --hax:#6d28a8;
  --pass:#0f6b60; --fail:#a5261c; --warn:#8a5a00; --wash:#f0ece3; --onaccent:#fff;
}
@media (prefers-color-scheme: dark){
  :root{--surface:#0f1216; --panel:#171c23; --ink:#f2f0ea; --muted:#bcc5d1; --faint:#8d98a7;
    --rule:#29313c; --rule2:#3b4552; --pi:#8fb3ff; --jcode:#f6b45b; --hax:#c9a6ff;
    --pass:#5eead4; --fail:#ff9a91; --warn:#f6c177; --wash:#151a21; --onaccent:#0f1216;}
}
html[data-theme="light"]{--surface:#fbfaf7; --panel:#f3f0e9; --ink:#14171a; --muted:#46505c; --faint:#5c6675;
  --rule:#e4dfd4; --rule2:#cec7b8; --pi:#2456c9; --jcode:#a94f05; --hax:#6d28a8;
  --pass:#0f6b60; --fail:#a5261c; --warn:#8a5a00; --wash:#f0ece3; --onaccent:#fff;}
html[data-theme="dark"]{--surface:#0f1216; --panel:#171c23; --ink:#f2f0ea; --muted:#bcc5d1; --faint:#8d98a7;
  --rule:#29313c; --rule2:#3b4552; --pi:#8fb3ff; --jcode:#f6b45b; --hax:#c9a6ff;
  --pass:#5eead4; --fail:#ff9a91; --warn:#f6c177; --wash:#151a21; --onaccent:#0f1216;}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--surface);color:var(--ink);
  font:16px/1.6 ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased}
h1,h2,h3{font-family:ui-serif,"Iowan Old Style","Palatino Linotype",Palatino,Georgia,"Times New Roman",serif;
  font-weight:650;letter-spacing:-.01em;margin:0}
h1{font-size:clamp(2rem,4.6vw,3.5rem);line-height:1.08}
h2{font-size:clamp(1.4rem,2.4vw,2rem);line-height:1.15}
h3{font-size:1.1rem}
p{margin:0 0 1em}
a{color:var(--pi);text-decoration:none}
a:hover{text-decoration:underline}
.mono{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  font-variant-numeric:tabular-nums}
nav{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--surface) 92%,transparent);
  border-bottom:1px solid var(--rule);backdrop-filter:blur(8px)}
.nav-in{max-width:1180px;margin:0 auto;padding:.65rem 1.25rem;display:flex;gap:1rem;align-items:center}
.nav-brand{font-weight:700;letter-spacing:.08em;font-size:.78rem;text-transform:uppercase}
.nav-links{display:flex;gap:1rem;flex-wrap:wrap;margin-left:auto;font-size:.88rem}
.nav-links a{color:var(--muted)}
.nav-links a:hover{color:var(--ink)}
button{background:var(--panel);color:var(--ink);border:1px solid var(--rule2);border-radius:999px;
  padding:.4rem .8rem;font:inherit;font-size:.82rem;cursor:pointer}
button:hover{border-color:var(--ink)}
button.on{border-color:var(--pi);color:var(--pi)}
main{max-width:1180px;margin:0 auto;padding:2rem 1.25rem 4rem}
.kicker{font-size:.78rem;letter-spacing:.14em;text-transform:uppercase;color:var(--faint);font-weight:700;margin-bottom:1rem}
.lede{font-size:clamp(1.05rem,1.5vw,1.25rem);color:var(--muted);max-width:60ch}
.hero{display:grid;grid-template-columns:1.25fr .9fr;gap:2.5rem;align-items:end;padding:2.5rem 0 1.5rem;border-bottom:1px solid var(--rule)}
.hero .verdict-box{background:var(--panel);border:1px solid var(--rule);border-radius:16px;padding:1.25rem 1.4rem}
.verdict-word{font-family:ui-serif,Georgia,serif;font-size:2.4rem;font-weight:700;line-height:1}
.chip{display:inline-block;border:1px solid var(--rule2);border-radius:999px;padding:.15rem .6rem;font-size:.74rem;
  letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-right:.35rem}
.chip.warn{color:var(--warn);border-color:var(--warn)}
.tldr{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin:1.8rem 0}
.tldr div{background:var(--panel);border:1px solid var(--rule);border-radius:12px;padding:1rem}
.tldr dt{font-size:.74rem;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);margin-bottom:.25rem}
.tldr dd{margin:0;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:1.35rem;font-weight:650;font-variant-numeric:tabular-nums}
section{padding:3rem 0;border-bottom:1px solid var(--rule);scroll-margin-top:4.5rem}
section:last-child{border-bottom:0}
.section-head{display:flex;align-items:baseline;gap:1rem;margin-bottom:1.4rem;flex-wrap:wrap}
.section-head .num{font-family:ui-monospace,Menlo,monospace;color:var(--faint);font-size:.8rem}
.figure{margin:0 0 2rem;background:var(--panel);border:1px solid var(--rule);border-radius:16px;padding:.9rem}
figcaption{display:flex;gap:.75rem;align-items:baseline;flex-wrap:wrap;padding:.3rem .4rem .7rem;font-size:.85rem;color:var(--faint)}
.fig-title{color:var(--ink);font-weight:650}
.figure svg{width:100%;height:auto;display:block;border-radius:10px}
.fig-light{display:block;overflow-x:auto}.fig-dark{display:none;overflow-x:auto}
.fig-light svg,.fig-dark svg{min-width:0}
.scroll-hint{display:none;color:var(--faint);font-size:.75rem;padding:.4rem .4rem 0}
html[data-theme="dark"] .fig-light{display:none}html[data-theme="dark"] .fig-dark{display:block}
@media (prefers-color-scheme: dark){html[data-theme="auto"] .fig-light{display:none}
  html[data-theme="auto"] .fig-dark{display:block}}
.fig-tools{padding:.5rem .4rem 0;display:flex;gap:1rem;font-size:.78rem}
.agent-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem;margin:1rem 0 1.5rem}
.agent-card{background:var(--panel);border:1px solid var(--rule);border-radius:14px;padding:1.1rem}
.agent-card .big{font-family:ui-monospace,Menlo,monospace;font-size:3rem;font-weight:700;line-height:1.1}
.agent-card .den{font-size:1.3rem;color:var(--faint)}
.agent-name{display:flex;align-items:center;gap:.5rem;font-weight:700}
.mark{width:11px;height:11px;display:inline-block;border-radius:50%}
.mark.pi-bolt{background:var(--pi)}.mark.jcode{background:var(--jcode);border-radius:2px}
.mark.hax{background:var(--hax);clip-path:polygon(50% 0,100% 100%,0 100%)}
.agent.pi-bolt{color:var(--pi);font-weight:700}.agent.jcode{color:var(--jcode);font-weight:700}
.agent.hax{color:var(--hax);font-weight:700}
.agent-card.pi-bolt{border-left:4px solid var(--pi)}.agent-card.jcode{border-left:4px solid var(--jcode)}
.agent-card.hax{border-left:4px solid var(--hax)}
.card-sub{color:var(--faint);font-size:.8rem;margin-bottom:.6rem}
dl{display:grid;grid-template-columns:1fr auto;gap:.3rem 1rem;margin:.6rem 0 0;font-size:.88rem}
dt{color:var(--muted)}dd{margin:0;text-align:right;font-family:ui-monospace,Menlo,monospace}
.toolbar{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin:0 0 .8rem}
.toolbar .spacer{flex:1}
.table-wrap{overflow-x:auto;border:1px solid var(--rule);border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:.9rem;min-width:820px}
th,td{padding:.6rem .7rem;border-bottom:1px solid var(--rule);text-align:right;white-space:nowrap}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2),th:nth-child(3),td:nth-child(3){text-align:left}
th{background:var(--panel);font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:var(--faint)}
tr:last-child td{border-bottom:0}
.status{font-size:.72rem;font-weight:750;letter-spacing:.06em;padding:.12rem .45rem;border-radius:4px}
.status.pass{color:var(--pass);border:1px solid var(--pass)}
.status.fail{color:var(--fail);border:1px solid var(--fail)}
.count{color:var(--faint);font-size:.82rem}
.safety-grid{display:grid;grid-template-columns:1fr 1fr;gap:1.25rem}
.box{background:var(--panel);border:1px solid var(--rule);border-radius:14px;padding:1.2rem}
.box h3{margin-bottom:.5rem}
.box ul{margin:.4rem 0 0;padding-left:1.1rem;color:var(--muted)}
.box li{margin-bottom:.35rem}
.muted{color:var(--muted)}.faint{color:var(--faint);font-size:.85rem}
.evidence-list{display:grid;grid-template-columns:repeat(2,1fr);gap:.75rem}
.evidence-list a{display:block;background:var(--panel);border:1px solid var(--rule);border-radius:10px;
  padding:.8rem 1rem;color:var(--ink)}
.evidence-list a span{display:block;color:var(--faint);font-size:.8rem;margin-top:.2rem}
.evidence-list a:hover{border-color:var(--pi);text-decoration:none}
details{border:1px solid var(--rule);border-radius:10px;padding:.7rem 1rem;margin:.5rem 0;background:var(--panel)}
summary{cursor:pointer;font-weight:600}
details p{margin:.6rem 0 0;color:var(--muted);font-size:.92rem}
footer{padding:2rem 0 3rem;color:var(--faint);font-size:.85rem}
footer .row{display:flex;gap:1rem;flex-wrap:wrap;justify-content:space-between}
.hero-figure{margin-top:1.5rem}
@media (max-width:900px){
  .hero{grid-template-columns:1fr;gap:1.5rem;align-items:start}
  .tldr{grid-template-columns:repeat(2,1fr)}
  .agent-grid{grid-template-columns:1fr}
  .safety-grid,.evidence-list{grid-template-columns:1fr}
  .nav-in{flex-wrap:wrap;gap:.5rem}
  .nav-links{order:3;width:100%;overflow-x:auto;white-space:nowrap;gap:.9rem;padding-bottom:.15rem;flex-wrap:nowrap}
  .nav-links a{font-size:.8rem}
}
@media (max-width:700px){
  .fig-light svg,.fig-dark svg{min-width:900px}
  .fig-scroll-hint{display:block}
}
@media (max-width:480px){
  main{padding:1.25rem .9rem 3rem}
  .tldr{grid-template-columns:1fr}
  .figure{padding:.6rem}
  figcaption{font-size:.8rem}
}
@media print{nav,.fig-tools,.toolbar,button{display:none}body{background:#fff;color:#000}}
</style>
</head>
<body>
<nav aria-label="Section navigation">
  <div class="nav-in">
    <div class="nav-brand">Agent Harness Bench</div>
    <div class="nav-links">
      <a href="#result">Result</a><a href="#race">Race</a><a href="#safety">Safety</a>
      <a href="#time">Time</a><a href="#cost">Cost</a><a href="#compute">Compute</a>
      <a href="#method">Method</a><a href="#limits">Limits</a><a href="#evidence">Evidence</a>
    </div>
    <button id="theme" aria-label="Toggle colour theme">Theme</button>
  </div>
</nav>
<main>
  <header class="hero">
    <div>
      <div class="kicker">Research preview · {{RUN_ID}} · {{MODEL}} · effort {{EFFORT}}</div>
      <h1>Three coding agents.<br>Nine trials.<br>One critical difference.</h1>
      <p class="lede" style="margin-top:1.25rem">{{HEADLINE}} This is a screening exercise with one trial per cell — not a universal ranking.</p>
      <div style="margin-top:1rem">
        <span class="chip">3 agents</span><span class="chip">3 tasks</span><span class="chip">9 trials</span>
        <span class="chip warn">n = 1 · low confidence</span>
      </div>
    </div>
    <div class="verdict-box">
      <div class="kicker" style="margin-bottom:.5rem">Provisional verdict</div>
      <div class="verdict-word">{{VERDICT}}</div>
      <p class="muted" style="margin:.6rem 0 0">3 of 3 verified completions · 298.00 of 300 quality points · lowest nominal cost per verified task.</p>
      <p class="faint" style="margin:.6rem 0 0">One trial per agent-task. Confidence: LOW. The result is driven by a single safety invariant.</p>
    </div>
  </header>

  <div class="tldr">
    <div><dt>Verified passes</dt><dd>7 / 9</dd></div>
    <div><dt>JCode verified</dt><dd>3 / 3</dd></div>
    <div><dt>Measured wall clock</dt><dd>{{WALL}} s</dd></div>
    <div><dt>Chat requests</dt><dd>261</dd></div>
  </div>

  <section id="result" class="hero-figure">
    {{FIG_HERO}}
  </section>

  <section id="trials">
    <div class="section-head"><span class="num">09 trials</span><h2>The result, trial by trial</h2></div>
    <div class="toolbar" role="group" aria-label="Filter trials">
      <span class="faint">Task</span>
      <button data-filter="task" data-value="" class="on">All</button>
      <button data-filter="task" data-value="t1_order_lifecycle">T1 safety</button>
      <button data-filter="task" data-value="t2_replay_perf">T2 performance</button>
      <button data-filter="task" data-value="t3_coordinator">T3 coordinator</button>
      <span class="spacer"></span>
      <span class="faint">Agent</span>
      <button data-filter="agent" data-value="" class="on">All</button>
      <button data-filter="agent" data-value="pi-bolt">Pi-Bolt</button>
      <button data-filter="agent" data-value="jcode">JCode</button>
      <button data-filter="agent" data-value="hax">Hax</button>
      <span class="count" id="count">9 trials</span>
    </div>
    <div class="table-wrap"><table id="trials">
      <thead><tr><th>Task</th><th>Agent</th><th>Result</th><th>Score</th><th>Wall</th><th>CPU</th><th>Peak mem</th><th>Tokens</th><th>Nominal</th><th>Requests</th></tr></thead>
      <tbody>{{TRIAL_ROWS}}</tbody>
    </table></div>
    <p class="faint" style="margin-top:.8rem">PASS requires every P0 gate and a score ≥ 80/100. Pi-Bolt and Hax scored 97.58 but failed one critical safety gate, so their T1 work is partial progress, not a verified completion.</p>
  </section>

  <section id="race">
    <div class="section-head"><span class="num">Figure 2</span><h2>The nine-trial race</h2></div>
    {{FIG_RACE}}
  </section>

  <section id="safety">
    <div class="section-head"><span class="num">Figure 3</span><h2>Why one safety case decided it</h2></div>
    {{FIG_SAFETY}}
    <div class="safety-grid">
      <div class="box"><h3>The invariant</h3>
        <p class="muted">A position is protected only when an ACTIVE protective stop covers exactly the open quantity. A stale callback is not evidence that protection was lost; applying it makes an unprotected position look protected.</p></div>
      <div class="box"><h3>What was observed</h3>
        <ul><li>After an accepted stop acknowledgement at version 1, protection was ACTIVE.</li>
        <li>A callback at version 0 reported CANCELLED.</li>
        <li>JCode rejected the stale version; Pi-Bolt and Hax applied it and failed hidden case h07.</li>
        <li>The P0 gate overrides the numeric score: 97.58 is not a verified pass.</li></ul></div>
    </div>
  </section>

  <section id="time">
    <div class="section-head"><span class="num">Figure 4</span><h2>Where the time went</h2></div>
    {{FIG_TIMING}}
  </section>

  <section id="cost">
    <div class="section-head"><span class="num">Figure 5</span><h2>Token volume is not verified value</h2></div>
    {{FIG_ECON}}
    <p class="faint">Costs are provider-reported nominal list prices on an OpenCode Go subscription, not incremental cash spend. Cost per verified task is undefined when no task passes, and is never shown as zero.</p>
  </section>

  <section id="compute">
    <div class="section-head"><span class="num">Figure 6</span><h2>Compute and reliability</h2></div>
    {{FIG_RES}}
  </section>

  <section id="method">
    <div class="section-head"><span class="num">Method</span><h2>How the experiment was controlled</h2></div>
    <div class="safety-grid">
      <div class="box"><h3>Measurement</h3>
        <ul>
          <li>Same provider, model and <span class="mono">reasoning_effort=low</span> for every request and subagent, pinned at a loopback relay.</li>
          <li>One shared start barrier per round; wall clock measured from barrier release to process exit.</li>
          <li>Each trial in a systemd user scope: MemoryMax 3 GiB, CPUQuota 200%, TasksMax 4096, 1 s cgroup sampling.</li>
          <li>CPU affinity rotated across three groups of two cores per task.</li>
          <li>Independent hidden graders; oracle and negative controls validated before the run.</li>
        </ul></div>
      <div class="box"><h3>Accounting</h3>
        <ul>
          <li>Every chat request passed through a per-trial relay; 261/261 returned provider-reported usage.</li>
          <li>Input, output, reasoning and cache-read tokens recorded separately; cost derived from provider list price.</li>
          <li>CPU seconds and peak memory from cgroup v2 counters, charging children and subagents.</li>
          <li>Patches frozen and hashed before grading; the report's evidence store is sealed with SHA-256.</li>
        </ul></div>
    </div>
    <div style="margin-top:1.25rem">
      <details><summary>What is measured, derived and unknown</summary>
        <p><b>Measured:</b> wall clock, provider token usage, request latency, cgroup CPU and memory, grader outcomes, diff sizes.
        <b>Derived:</b> cost from provider list prices; token/quality ratios; the non-API remainder of wall time; aggregate means.
        <b>Unknown:</b> JCode's tool-level activity (its CLI emitted only a final result); true incremental subscription cost; behaviour beyond these three tasks.</p></details>
      <details><summary>Fairness and confounders</summary>
        <p>All agents shared one VPS, one provider account and one Unix user. Isolation was cgroup/process and filesystem based, not a security boundary. Provider contention is recorded but cannot be removed. The benchmark was built by the Pi-Bolt author; graders and controls were independently validated, but authorship remains a confounder. One trial per cell means no statistical inference is possible.</p></details>
    </div>
  </section>

  <section id="limits">
    <div class="section-head"><span class="num">Limits</span><h2>What this experiment does not establish</h2></div>
    <div class="safety-grid">
      <div class="box"><h3>Scope</h3>
        <ul><li>Three tasks, one attempt per agent-task.</li><li>Task selection is a design choice and may favour one agent's strengths.</li><li>No confidence intervals, no universal ranking, no “best agent” claim.</li></ul></div>
      <div class="box"><h3>Measurement</h3>
        <ul><li>Nominal list price is not subscription cash.</li><li>JCode's tool trace is unavailable, so tool efficiency cannot be compared.</li><li>cgroup memory is not process RSS; short-lived children may be under-sampled.</li><li>Shared provider capacity can reorder the time ranking.</li></ul></div>
    </div>
    <p class="muted" style="margin-top:1rem">The defensible conclusion: <b>JCode completed all three tasks; Pi-Bolt and Hax completed two. The difference came from one critical stale-callback safety test.</b> Further replications are required before using this as a general selection signal.</p>
  </section>

  <section id="evidence">
    <div class="section-head"><span class="num">Evidence</span><h2>Inspect the record</h2></div>
    <div class="evidence-list">
      <a href="report.html">Full forensic report<span>Single-file offline audit · sanitized derivative · hash {{REPORT_HASH}}…</span></a>
      <a href="data/publication-data.json">Normalized dataset<span>Every published number, with provenance and reconciliation status</span></a>
      <a href="assets/hero-light.svg">Figure assets<span>{{FIG_COUNT}} compositions × light/dark, generated from the dataset</span></a>
      <a href="https://github.com/ddawnlll/agent-harness-bench">Repository<span>Controller, graders, task fixtures, docs and checks</span></a>
    </div>
    <p class="faint" style="margin-top:1rem">The original sealed benchmark evidence is preserved outside the public repository. Publication derivatives are produced by a documented sanitization and derivation pipeline; see the repository's security and reproduction documents.</p>
  </section>

  <footer>
    <div class="row"><span>Agent Harness Bench · research preview v0.1.0 · {{MODEL}} · effort {{EFFORT}}</span>
      <span>Charts and site generated deterministically · no external assets</span></div>
    <div class="row" style="margin-top:.6rem"><span>Sanitized publication dataset · nominal costs, not subscription cash · n = 1 per cell</span>
      <span class="mono" id="build-stamp">static build</span></div>
  </footer>
</main>
<script type="application/json" id="bench-data">{{DATA_JSON}}</script>
<script>
(function(){
  "use strict";
  var root=document.documentElement;
  var modes=["auto","light","dark"], idx=0;
  var btn=document.getElementById("theme");
  function apply(){root.setAttribute("data-theme",modes[idx]);btn.textContent="Theme: "+modes[idx];}
  btn.addEventListener("click",function(){idx=(idx+1)%modes.length;apply();});
  apply();
  var filters={task:"",agent:""};
  var rows=Array.prototype.slice.call(document.querySelectorAll("#trials tbody tr"));
  var count=document.getElementById("count");
  function refresh(){
    var shown=0;
    rows.forEach(function(r){
      var ok=(!filters.task||r.dataset.task===filters.task)&&(!filters.agent||r.dataset.agent===filters.agent);
      r.style.display=ok?"":"none"; if(ok)shown++;
    });
    count.textContent=shown+" trial"+(shown===1?"":"s");
  }
  Array.prototype.forEach.call(document.querySelectorAll("[data-filter]"),function(b){
    b.addEventListener("click",function(){
      var f=b.dataset.filter; filters[f]=b.dataset.value;
      Array.prototype.forEach.call(document.querySelectorAll('[data-filter="'+f+'"]'),function(x){x.classList.remove("on");});
      b.classList.add("on"); refresh();
    });
  });
  document.getElementById("build-stamp").textContent=document.getElementById("bench-data").textContent.length.toLocaleString()+" bytes of embedded data";
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
