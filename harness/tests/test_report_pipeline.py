#!/usr/bin/env python3
"""Acceptance tests for the reporting pipeline (offline, deterministic)."""
import json, os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "scripts", "build_report.py")
SYNTH = os.path.join(ROOT, "tests", "make_synthetic.py")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def check(cond, msg, failures):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


def build(run_dir, out):
    p = run([sys.executable, BUILD, "--run-dir", run_dir, "--out", out])
    return p


def analyze_html(path):
    html = open(path, encoding="utf-8").read()
    external = re.findall(r'<(?:script|link|img)[^>]+(?:src|href)=["\'](https?:)?//', html, re.I)
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    payload = re.search(r'<script id="bench-data-gz" type="text/plain">(.+?)</script>', html, re.S)
    import base64, gzip
    data = json.loads(gzip.decompress(base64.b64decode(payload.group(1)))) if payload else None
    return html, external, scripts, data


def main():
    failures = []
    tmp = tempfile.mkdtemp(prefix="reporttests-")
    try:
        synth = os.path.join(tmp, "synthetic")
        p = run([sys.executable, SYNTH, synth])
        check(p.returncode == 0, "synthetic dataset generated", failures)
        out = os.path.join(tmp, "synthetic.html")
        p = build(synth, out)
        check(p.returncode == 0, "synthetic report builds", failures)
        html, external, scripts, data = analyze_html(out)
        check(not external, "synthetic report has no external assets", failures)
        check(len(scripts) == 1, "single inline script", failures)
        check(re.search(r'SCRIPT|SYNTHETIC', html) is not None, "synthetic data prominently labelled", failures)
        check(len(data["runs"]) == 9, "nine synthetic trials embedded", failures)
        check(data["meta"]["synthetic"] is True, "synthetic flag set", failures)
        if shutil.which("node"):
            js = os.path.join(tmp, "r.js")
            open(js, "w").write(scripts[0])
            p = run(["node", "--check", js])
            check(p.returncode == 0, "report JavaScript parses", failures)

        # empty state
        empty = os.path.join(tmp, "empty")
        os.makedirs(os.path.join(empty, "telemetry"), exist_ok=True)
        json.dump({"run_id": "EMPTY", "config": {"experiment_id": "EMPTY",
                   "agents": [{"id": "pi-bolt"}], "rounds": [{"round_id": "r1", "task_id": "t1"}],
                   "model": "m", "provider": "p", "reasoning_effort": "low"},
                   "host": {}, "runs": []}, open(os.path.join(empty, "summary_raw.json"), "w"))
        json.dump({}, open(os.path.join(empty, "telemetry", "environment.json"), "w"))
        out2 = os.path.join(tmp, "empty.html")
        p = build(empty, out2)
        check(p.returncode == 0, "empty dataset renders without exception", failures)
        html2, external2, _, data2 = analyze_html(out2)
        check(data2["meta"]["empty_state"] is True, "empty state flagged", failures)
        check(data2["verdict"]["recommendation"] == "INSUFFICIENT_EVIDENCE",
              "empty data produces no winner", failures)
        check(not external2, "empty report has no external assets", failures)

        # missing/corrupt telemetry is tolerated, not fatal
        broken = os.path.join(tmp, "broken")
        shutil.copytree(synth, broken)
        open(os.path.join(broken, "telemetry", "api_usage.jsonl"), "w").write("{not json}\n")
        open(os.path.join(broken, "runs", "t1", "pi-bolt", "hidden_grader.json"), "w").write("{bad")
        out3 = os.path.join(tmp, "broken.html")
        p = build(broken, out3)
        check(p.returncode == 0, "corrupt telemetry/grades do not crash the report", failures)

        # zero verified passes -> undefined cost per verified task
        zero = os.path.join(tmp, "zero")
        shutil.copytree(synth, zero)
        for dp, _, fs in os.walk(os.path.join(zero, "runs")):
            for f in fs:
                if f == "hidden_grader.json":
                    grade = json.load(open(os.path.join(dp, f)))
                    grade["status"] = "FAIL"
                    grade["total"] = 40
                    json.dump(grade, open(os.path.join(dp, f), "w"))
        out4 = os.path.join(tmp, "zero.html")
        p = build(zero, out4)
        _, _, _, data4 = analyze_html(out4)
        econ = data4["economics"]["per_agent"]["pi-bolt"]
        check(econ["verified_passes"] == 0, "zero-pass dataset detected", failures)
        check(econ["cost_per_verified"] is None, "cost per verified success is undefined (not zero)", failures)

        # secrets never embedded
        secret = "sk-" + "A" * 40
        html5 = open(out, encoding="utf-8").read()
        check(secret not in html5, "no API-key-shaped material in report", failures)
        check("Bearer " not in html5 or "REDACTED" in html5, "bearer tokens redacted/absent", failures)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if failures:
        print("FAILED:", len(failures))
        return 1
    print("ALL REPORT PIPELINE ACCEPTANCE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
