#!/usr/bin/env python3
"""Independent grader for benchmark task t2_replay_perf.

Usage
-----
    python3 grade.py --workspace DIR --json-out OUT.json [--baseline DIR]
                     [--repeats N] [--quick] [--work-dir DIR]

The grader never mutates the candidate workspace: it copies the candidate
(and the pristine baseline) into a private temporary directory and runs
everything there.

What it does
------------
1. static checks ...... test-file integrity, forbidden imports/network,
                        fixture integrity, layout
2. correctness ....... public unittest suite (candidate's own copy and the
                        pristine copy), hidden-workload parity against the
                        pristine engine, determinism, output contract
3. performance ....... interleaved timing (warmup + N measured repeats) on
                        hidden workloads, medians, speedup ratios
4. memory ............ peak RSS (RUSAGE_CHILDREN) per version
5. anti-cheat ........ caching artefacts, implausible timing, skipped work,
                        constant output, irrelevant-field invariance

Everything is stdlib only and deterministic for a given candidate.
"""

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import hidden_cases  # noqa: E402

GRADER_VERSION = "1.0.0"
TASK_ID = "t2"
PASS_THRESHOLD = 80.0
DEFAULT_REPEATS = 3
DEFAULT_RUN_TIMEOUT = 300
TEST_TIMEOUT = 420
PYTHON = sys.executable
BUNDLED_BASELINE = os.path.join(HERE, "baseline_repo")

RUN_PROFILES = ("h_large", "h_adversarial", "h_small", "h_fresh")
TIMED_PROFILES = ("h_large", "h_adversarial")

RSS_WRAPPER = (
    "import resource, subprocess, sys\n"
    "rc = subprocess.run(sys.argv[1:]).returncode\n"
    "print('RSS_KB=' + str(resource.getrusage("
    "resource.RUSAGE_CHILDREN).ru_maxrss))\n"
    "sys.exit(rc)\n"
)

FORBIDDEN_PATTERNS = [
    ("third_party_import",
     r"^\s*(?:import|from)\s+(?:numpy|pandas|scipy|orjson|ujson|numba|"
     r"pyarrow|polars|requests|urllib3|aiohttp|httpx|pytest|cython|"
     r"Cython|msgpack|cv2|sklearn)\b"),
    ("network_module",
     r"^\s*(?:import|from)\s+(?:socket|urllib|urllib2|http|ftplib|"
     r"smtplib|telnetlib|xmlrpc|asyncio|ssl|selectors|socketserver)\b"),
    ("network_call",
     r"\b(?:urlopen|urlretrieve|HTTPConnection|smtplib\.|ftplib\.)\b"),
    ("time_manipulation",
     r"\b(?:time\.sleep|sys\.setprofile|sys\.settrace|threading\.settrace|"
     r"time\.monkeypatch)\b"),
    ("subprocess_use", r"^\s*(?:import|from)\s+subprocess\b"),
    ("code_injection",
     r"\b(?:eval|exec|__import__)\s*\("),
    ("persistent_cache",
     r"\b(?:shelve|pickle|marshal|dbm|sqlite3)\b|"
     r"\.(?:pkl|pickle|sqlite|db|npy|npz|cache)\b"),
]

INJECTION_FILES = ("sitecustomize.py", "usercustomize.py", "conftest.py")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return b""


def short(value, limit=90):
    text = repr(value)
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def rel_files(root, suffix=None):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in ("__pycache__", ".git",
                                          ".mypy_cache", ".pytest_cache"))
        for name in sorted(filenames):
            if suffix and not name.endswith(suffix):
                continue
            full = os.path.join(dirpath, name)
            out.append(os.path.relpath(full, root))
    return out


def copy_workspace(src, dst):
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc",
                                      "*.pyo", ".mypy_cache", ".pytest_cache",
                                      ".benchmark_tmp"),
    )
    return dst


def file_state(root):
    state = {}
    for rel in rel_files(root):
        full = os.path.join(root, rel)
        try:
            stat = os.stat(full)
        except OSError:
            continue
        state[rel] = (stat.st_size, stat.st_mtime_ns)
    return state


# ---------------------------------------------------------------------------
# subprocess execution
# ---------------------------------------------------------------------------
def run_process(argv, cwd, timeout, env=None):
    started = time.perf_counter()
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, env=env)
        timed_out = False
        stdout, stderr, rc = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (exc.stdout or b"")
        stderr = (exc.stderr or b"")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        rc = -9
    return {
        "rc": rc,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "elapsed_ms": (time.perf_counter() - started) * 1000.0,
    }


def run_cli(repo, events, out_path, timeout, measure_rss=False):
    args = [PYTHON, "-m", "replay.cli", "--events", events,
            "--out", out_path]
    if measure_rss:
        args = [PYTHON, "-c", RSS_WRAPPER] + args
    result = run_process(args, repo, timeout)
    result["rss_bytes"] = None
    if measure_rss:
        for line in result["stdout"].splitlines():
            if line.startswith("RSS_KB="):
                try:
                    result["rss_bytes"] = int(line.split("=", 1)[1]) * 1024
                except ValueError:
                    pass
    return result


TEST_LINE = re.compile(r"^(\w+) \(([\w\.]+)\)(?:\s*\.\.\.)?\s*(.*)$")


def parse_unittest(output):
    results = {}
    for line in output.splitlines():
        match = TEST_LINE.match(line.strip())
        if not match:
            continue
        status = match.group(3).strip().lower()
        if status.startswith("ok"):
            status = "ok"
        elif status.startswith("fail"):
            status = "fail"
        elif status.startswith("error"):
            status = "error"
        elif status.startswith("skipped"):
            status = "skipped"
        elif status.startswith("expected failure"):
            status = "expected_failure"
        elif status.startswith("unexpected success"):
            status = "unexpected_success"
        elif status.startswith("..."):  # pragma: no cover - defensive
            continue
        else:
            continue
        results[match.group(2) + "::" + match.group(1)] = status
    return results


def run_unittest(repo, timeout):
    result = run_process(
        [PYTHON, "-m", "unittest", "discover", "-s", "tests", "-v"],
        repo, timeout)
    tests = parse_unittest(result["stdout"] + "\n" + result["stderr"])
    result["tests"] = tests
    result["passed"] = sorted(name for name, status in tests.items()
                              if status in ("ok", "expected_failure"))
    result["failed"] = sorted(name for name, status in tests.items()
                              if status in ("fail", "error",
                                            "unexpected_success"))
    result["skipped"] = sorted(name for name, status in tests.items()
                               if status == "skipped")
    result["total"] = len(tests)
    return result


# ---------------------------------------------------------------------------
# document comparison / validation
# ---------------------------------------------------------------------------
def document_diff(left, right, max_items=8):
    problems = []

    def walk(a, b, path):
        if len(problems) >= max_items:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            keys = sorted(set(a) | set(b))
            for key in keys:
                if len(problems) >= max_items:
                    return
                child = path + "/" + key
                if key not in a:
                    problems.append([child, "<missing>", short(b[key])])
                elif key not in b:
                    problems.append([child, short(a[key]), "<missing>"])
                else:
                    walk(a[key], b[key], child)
            return
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                problems.append([path, "len=%d" % len(a),
                                 "len=%d" % len(b)])
            for index, (item_a, item_b) in enumerate(zip(a, b)):
                if len(problems) >= max_items:
                    return
                walk(item_a, item_b, path + "[%d]" % index)
            return
        if isinstance(a, bool) != isinstance(b, bool) or (
                not isinstance(a, (int, float, str, bool, type(None)))
                or not isinstance(b, (int, float, str, bool, type(None)))):
            if a != b:
                problems.append([path, short(a), short(b)])
            return
        if a != b:
            problems.append([path, short(a), short(b)])

    walk(left, right, "")
    return problems


DIGEST_RE = re.compile(r"^[0-9a-f]{16}$")


def validate_contract(document, expected_events):
    problems = []
    if not isinstance(document, dict):
        return ["document is not a JSON object"]
    for key in ("schema", "event_count", "trade_count", "trades", "accounts",
                "symbols", "lifecycle", "audit"):
        if key not in document:
            problems.append("missing key %r" % key)
    if problems:
        return problems
    if document["schema"] != "replay.v1":
        problems.append("schema is %r" % (document["schema"],))
    if not isinstance(document["trades"], list):
        problems.append("trades is not a list")
    if not isinstance(document["accounts"], dict):
        problems.append("accounts is not an object")
    if not isinstance(document["symbols"], dict):
        problems.append("symbols is not an object")
    audit = document["audit"]
    if not isinstance(audit, dict) or "digest" not in audit:
        problems.append("audit.digest missing")
    elif not isinstance(audit["digest"], str) or not DIGEST_RE.match(
            audit["digest"]):
        problems.append("audit.digest is not 16 lowercase hex chars: %r"
                        % (short(audit.get("digest")),))
    if document["event_count"] != expected_events:
        problems.append("event_count=%r expected %d"
                        % (document["event_count"], expected_events))
    if audit.get("entries") != expected_events:
        problems.append("audit.entries=%r expected %d"
                        % (audit.get("entries"), expected_events))
    if document["trade_count"] != len(document["trades"]):
        problems.append("trade_count=%r but len(trades)=%d"
                        % (document["trade_count"],
                           len(document["trades"])))
    lifecycle = document["lifecycle"]
    if isinstance(lifecycle, dict):
        for key in ("submitted", "cancelled", "rejected", "filled_orders",
                    "open_at_end", "open_qty_at_end", "trade_count",
                    "traded_qty", "traded_notional", "max_open_orders"):
            if key not in lifecycle:
                problems.append("lifecycle missing %r" % key)
            elif not isinstance(lifecycle[key], int):
                problems.append("lifecycle.%s is not an int" % key)
        if lifecycle.get("trade_count") != len(document["trades"]):
            problems.append("lifecycle.trade_count != len(trades)")
        notional = sum(trade.get("price", 0) * trade.get("qty", 0)
                       for trade in document["trades"]
                       if isinstance(trade, dict))
        if lifecycle.get("traded_notional") != notional:
            problems.append("lifecycle.traded_notional != sum(price*qty) "
                            "over trades (%r vs %r)"
                            % (lifecycle.get("traded_notional"), notional))
        if lifecycle.get("traded_qty") != sum(trade.get("qty", 0)
                                              for trade in document["trades"]
                                              if isinstance(trade, dict)):
            problems.append("lifecycle.traded_qty != sum(qty) over trades")
    else:
        problems.append("lifecycle is not an object")
    return problems


def load_document(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        return None, "cannot read output: %s" % exc
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, "output is not valid JSON: %s" % exc


# ---------------------------------------------------------------------------
# static anti-cheat checks
# ---------------------------------------------------------------------------
def scan_forbidden(repo):
    """Scan the runtime package (replay/) for forbidden patterns.

    Tooling and tests are excluded: they are not part of the replay path and
    the baseline itself uses subprocess in tools/gen_fixtures.py.
    """
    findings = []
    for rel in rel_files(repo):
        if not rel.endswith(".py"):
            continue
        if not (rel == "replay" or rel.startswith("replay/")):
            continue
        text = read_text(os.path.join(repo, rel))
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for name, pattern in FORBIDDEN_PATTERNS:
                if re.search(pattern, line):
                    findings.append({
                        "file": rel, "line": lineno, "rule": name,
                        "code": stripped[:160],
                    })
    for rel in rel_files(repo):
        base = os.path.basename(rel)
        if base in INJECTION_FILES:
            findings.append({"file": rel, "line": 0,
                             "rule": "startup_hook", "code": base})
        if base.endswith(".pth"):
            findings.append({"file": rel, "line": 0,
                             "rule": "startup_hook", "code": base})
    return findings


def debug_print_findings(repo):
    """Flag debug output in replay/ (CLI ``main()`` output is allowed)."""
    findings = []

    def visit(node, current_function, rel):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if isinstance(child, ast.Call) and isinstance(
                        child.func, ast.Name) and child.func.id == "print":
                    findings.append("%s:%d: print() in nested function"
                                    % (rel, child.lineno))
                visit(child, child.name, rel)
                continue
            if isinstance(child, ast.Call):
                func = child.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name == "print" and current_function != "main":
                    findings.append("%s:%d: print() outside main()"
                                    % (rel, child.lineno))
                elif name in ("breakpoint", "set_trace", "post_mortem"):
                    findings.append("%s:%d: debugger hook %s()"
                                    % (rel, child.lineno, name))
            visit(child, current_function, rel)

    for rel in rel_files(repo, suffix=".py"):
        if not rel.startswith("replay/"):
            continue
        if rel == "replay/cli.py":
            continue
        text = read_text(os.path.join(repo, rel))
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            findings.append("%s: cannot parse (%s)" % (rel, exc))
            continue
        visit(tree, None, rel)
    return findings


def change_size(baseline, candidate):
    """Return total added/removed lines across the code files."""
    import difflib

    baseline_files = set(rel_files(baseline, suffix=".py"))
    candidate_files = set(rel_files(candidate, suffix=".py"))
    total = 0
    for rel in sorted(baseline_files | candidate_files):
        if rel.startswith("tests/") or rel.startswith("tests" + os.sep):
            continue
        old = read_text(os.path.join(baseline, rel)).splitlines()
        new = read_text(os.path.join(candidate, rel)).splitlines()
        if not old and new:
            total += len(new)
            continue
        if old == new:
            continue
        diff = difflib.unified_diff(old, new, lineterm="", n=0)
        for line in diff:
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+") or line.startswith("-"):
                total += 1
    return total


# ---------------------------------------------------------------------------
# grading
# ---------------------------------------------------------------------------
class Report(object):
    def __init__(self):
        self.gates = []
        self.cases = []

    def gate(self, gate_id, severity, passed, detail, evidence=None):
        self.gates.append({
            "id": gate_id,
            "severity": severity,
            "passed": bool(passed),
            "detail": detail,
            "evidence": evidence or [],
        })
        return bool(passed)

    def case(self, case_id, requirement, severity, passed, detail,
             duration_ms=0.0):
        self.cases.append({
            "id": case_id,
            "requirement": requirement,
            "severity": severity,
            "passed": bool(passed),
            "detail": detail,
            "duration_ms": round(duration_ms, 2),
        })
        return bool(passed)

    def gate_passed(self, gate_id):
        for gate in self.gates:
            if gate["id"] == gate_id:
                return gate["passed"]
        return False


def resolve_baseline(explicit):
    candidates = []
    if explicit:
        candidates.append(explicit)
    candidates.append(BUNDLED_BASELINE)
    for path in candidates:
        if path and os.path.isdir(os.path.join(path, "replay")):
            return os.path.abspath(path)
    return None


def grade(args):
    report = Report()
    metrics = {
        "baseline_median_ms": {},
        "candidate_median_ms": {},
        "speedup": {},
        "peak_rss_bytes": {"baseline": {}, "candidate": {}},
        "repeats": args.repeats,
    }
    determinism = {"repeats": args.repeats, "agreement": True,
                   "detail": {}}
    evidence_refs = []
    timed_out = False

    workspace = os.path.abspath(args.workspace)
    if not os.path.isdir(workspace):
        return invalid_report("workspace %r does not exist" % args.workspace,
                             report, metrics, determinism, evidence_refs)

    baseline_path = resolve_baseline(args.baseline)
    if baseline_path is None:
        return invalid_report(
            "pristine baseline not found (looked for --baseline and the "
            "bundled %r)" % BUNDLED_BASELINE,
            report, metrics, determinism, evidence_refs)

    if args.work_dir:
        work_root = os.path.abspath(args.work_dir)
        os.makedirs(work_root, exist_ok=True)
        tmp_root = tempfile.mkdtemp(prefix="t2-", dir=work_root)
        keep = True
    else:
        tmp_root = tempfile.mkdtemp(prefix="t2-grade-")
        keep = False

    cand_dir = copy_workspace(workspace, os.path.join(tmp_root, "candidate"))
    base_dir = copy_workspace(baseline_path, os.path.join(tmp_root, "baseline"))
    inputs_dir = os.path.join(tmp_root, "inputs")
    outputs_dir = os.path.join(tmp_root, "outputs")
    os.makedirs(inputs_dir, exist_ok=True)
    os.makedirs(outputs_dir, exist_ok=True)
    evidence_refs.extend([cand_dir, base_dir, inputs_dir, outputs_dir])

    # ---------------------------------------------------------------- inputs
    events_by_profile = {}
    input_paths = {}
    for name in hidden_cases.PROFILE_ORDER:
        events = hidden_cases.build_profile(name)
        path = os.path.join(inputs_dir, name + ".jsonl")
        hidden_cases.write_events(path, events)
        events_by_profile[name] = events
        input_paths[name] = path

    # perturbed fresh input: identical semantics, extra irrelevant field.
    fresh_events = events_by_profile["h_fresh"]
    perturbed = [dict(event, source="grader", note="irrelevant") for event
                 in fresh_events]
    perturbed_path = os.path.join(inputs_dir, "h_fresh_perturbed.jsonl")
    hidden_cases.write_events(perturbed_path, perturbed)

    malformed_path = os.path.join(inputs_dir, "malformed.jsonl")
    with open(malformed_path, "w", encoding="utf-8") as handle:
        handle.write("{this is not json}\n")

    # ------------------------------------------------------- static checks
    candidate_tests = os.path.join(cand_dir, "tests")
    baseline_tests = os.path.join(base_dir, "tests")

    tampered = []
    missing = []
    baseline_test_files = rel_files(baseline_tests)
    for rel in baseline_test_files:
        cand_file = os.path.join(candidate_tests, rel)
        if not os.path.isfile(cand_file):
            missing.append(rel)
            continue
        if sha256_file(os.path.join(baseline_tests, rel)) != \
                sha256_file(cand_file):
            tampered.append(rel)
    test_integrity_ok = not tampered and not missing
    report.gate(
        "test_integrity", "P0", test_integrity_ok,
        "public test files are unchanged" if test_integrity_ok else
        "test files modified: %s; missing: %s"
        % (tampered or "none", missing or "none"),
        [os.path.join(candidate_tests, rel) for rel in tampered + missing])

    fixture_changes = []
    baseline_data = os.path.join(base_dir, "data")
    candidate_data = os.path.join(cand_dir, "data")
    for rel in rel_files(baseline_data):
        cand_file = os.path.join(candidate_data, rel)
        if not os.path.isfile(cand_file) or \
                sha256_file(os.path.join(baseline_data, rel)) != \
                sha256_file(cand_file):
            fixture_changes.append(rel)
    report.case(
        "c_fixtures_untampered",
        "TASK.md: do not touch data/ (committed fixtures are inputs)",
        "P1", not fixture_changes,
        "fixtures unchanged" if not fixture_changes else
        "fixtures modified/missing: %s" % ", ".join(fixture_changes))

    findings = scan_forbidden(cand_dir)
    report.gate(
        "no_third_party_or_network", "P0", not findings,
        "no third-party imports, network use, timing tricks or persistent "
        "caches detected" if not findings else
        "%d forbidden pattern(s) found: %s"
        % (len(findings), json.dumps(findings[:4])),
        [os.path.join(cand_dir, finding["file"])
         for finding in findings[:8]])

    layout_ok = (os.path.isdir(os.path.join(cand_dir, "replay")) and
                 os.path.isfile(os.path.join(cand_dir, "replay", "cli.py")) and
                 os.path.isdir(candidate_tests))
    report.case(
        "c_layout",
        "TASK.md: keep the replay package, the CLI and the tests in place",
        "P0", layout_ok, "replay/ and tests/ present" if layout_ok else
        "replay/, replay/cli.py or tests/ missing")

    # ------------------------------------------------------- test execution
    started = time.perf_counter()
    public_tests = run_unittest(cand_dir, TEST_TIMEOUT)
    public_ms = (time.perf_counter() - started) * 1000.0
    tests_ok = (public_tests["rc"] == 0 and
                public_tests["total"] > 0 and
                not public_tests["failed"] and
                not public_tests["skipped"])
    report.gate(
        "public_tests", "P0", tests_ok,
        "all %d public tests pass" % public_tests["total"] if tests_ok else
        "rc=%s total=%d failed=%s skipped=%s\n%s"
        % (public_tests["rc"], public_tests["total"],
           public_tests["failed"][:8], public_tests["skipped"][:8],
           (public_tests["stdout"] + public_tests["stderr"])[-1500:]),
        [os.path.join(cand_dir, "tests")])
    report.case(
        "c_public_tests",
        "TASK.md: python3 -m unittest discover -s tests -v must pass",
        "P0", tests_ok,
        "%d passed, %d failed, %d skipped"
        % (len(public_tests["passed"]), len(public_tests["failed"]),
           len(public_tests["skipped"])), public_ms)

    baseline_test_count = None
    baseline_test_run = run_unittest(base_dir, TEST_TIMEOUT)
    baseline_test_count = baseline_test_run["total"]
    report.case(
        "c_tests_not_reduced",
        "TASK.md: do not delete tests (the suite must not shrink)",
        "P1",
        baseline_test_count is not None and
        public_tests["total"] >= baseline_test_count,
        "baseline suite has %s tests, candidate suite has %d"
        % (baseline_test_count, public_tests["total"]))

    # pristine tests run against candidate code in a fresh copy
    pristine_dir = os.path.join(tmp_root, "pristine_tests")
    copy_workspace(cand_dir, pristine_dir)
    shutil.rmtree(os.path.join(pristine_dir, "tests"), ignore_errors=True)
    shutil.copytree(baseline_tests, os.path.join(pristine_dir, "tests"))
    started = time.perf_counter()
    pristine = run_unittest(pristine_dir, TEST_TIMEOUT)
    pristine_ms = (time.perf_counter() - started) * 1000.0
    pristine_ok = (pristine["rc"] == 0 and pristine["total"] > 0 and
                   not pristine["failed"] and not pristine["skipped"])
    report.gate(
        "pristine_tests", "P0", pristine_ok,
        "candidate code passes the pristine test suite (%d tests)"
        % pristine["total"] if pristine_ok else
        "candidate code fails the pristine tests: failed=%s skipped=%s\n%s"
        % (pristine["failed"][:8], pristine["skipped"][:8],
           (pristine["stdout"] + pristine["stderr"])[-1500:]),
        [pristine_dir])
    report.case(
        "c_pristine_tests",
        "TASK.md: public tests must keep passing (pristine copies)",
        "P0", pristine_ok,
        "%d passed, %d failed" % (len(pristine["passed"]),
                                  len(pristine["failed"])), pristine_ms)

    # ------------------------------------------------- baseline validation
    baseline_runs = {}
    baseline_docs = {}
    baseline_hashes = {}
    baseline_ok = True
    baseline_problems = []
    for name in RUN_PROFILES:
        out_path = os.path.join(outputs_dir, "baseline_%s_%s.json"
                                % (name, "warm"))
        result = run_cli(base_dir, input_paths[name], out_path,
                         args.run_timeout, measure_rss=True)
        baseline_runs.setdefault(name, []).append(result)
        metrics["peak_rss_bytes"]["baseline"][name] = result.get("rss_bytes")
        if result["rc"] != 0 or result["timed_out"]:
            baseline_ok = False
            baseline_problems.append(
                "%s: baseline rc=%s timed_out=%s stderr=%s"
                % (name, result["rc"], result["timed_out"],
                   result["stderr"][-400:]))
            continue
        doc, problem = load_document(out_path)
        if problem:
            baseline_ok = False
            baseline_problems.append("%s: %s" % (name, problem))
            continue
        problems = validate_contract(doc, len(events_by_profile[name]))
        if problems:
            baseline_ok = False
            baseline_problems.append("%s: baseline contract: %s"
                                     % (name, problems[:3]))
        baseline_docs[name] = doc
        baseline_hashes.setdefault(name, set()).add(sha256_file(out_path))

    if not report.gate(
            "baseline_sane", "P0", baseline_ok,
            "pristine baseline replays all hidden workloads cleanly" if
            baseline_ok else
            "the pristine baseline is broken: %s" % "; ".join(
                baseline_problems[:3]),
            [os.path.join(outputs_dir, "baseline_*")]):
        return invalid_report("baseline is not usable", report, metrics,
                             determinism, evidence_refs, keep=keep,
                             tmp_root=tmp_root)

    # ------------------------------------------------ warmup candidate runs
    candidate_runs = {}
    candidate_docs = {}
    candidate_hashes = {}
    candidate_ok = True
    candidate_problems = []
    before_state = file_state(cand_dir)
    for name in RUN_PROFILES:
        out_path = os.path.join(outputs_dir, "candidate_%s_warm.json" % name)
        result = run_cli(cand_dir, input_paths[name], out_path,
                         args.run_timeout, measure_rss=True)
        candidate_runs.setdefault(name, []).append(result)
        metrics["peak_rss_bytes"]["candidate"][name] = result.get("rss_bytes")
        if result["rc"] != 0 or result["timed_out"]:
            candidate_ok = False
            candidate_problems.append(
                "%s: candidate rc=%s timed_out=%s stderr=%s"
                % (name, result["rc"], result["timed_out"],
                   result["stderr"][-500:]))
            continue
        doc, problem = load_document(out_path)
        if problem:
            candidate_ok = False
            candidate_problems.append("%s: %s" % (name, problem))
            continue
        candidate_docs[name] = doc
        candidate_hashes.setdefault(name, set()).add(sha256_file(out_path))

    # ------------------------------------------------------- harness checks
    # irrelevant-field invariance: extra keys must not change the result.
    perturbed_out = os.path.join(outputs_dir, "candidate_h_fresh_perturbed.json")
    perturbed = run_cli(cand_dir, perturbed_path, perturbed_out,
                        args.run_timeout)
    perturbed_ok = False
    perturbed_detail = "candidate could not replay the perturbed input"
    if perturbed["rc"] == 0:
        doc, problem = load_document(perturbed_out)
        if problem:
            perturbed_detail = problem
        elif "h_fresh" in candidate_docs:
            diff = document_diff(candidate_docs["h_fresh"], doc)
            perturbed_ok = not diff
            perturbed_detail = ("extra input fields are ignored" if
                                perturbed_ok else
                                "extra input fields changed the output: %s"
                                % json.dumps(diff[:4]))
        else:
            perturbed_detail = "no reference h_fresh output to compare"
    report.case(
        "c_irrelevant_field_invariance",
        "Parity must not depend on input file bytes/known hashes: unknown "
        "event fields are ignored by the documented loader",
        "P1", perturbed_ok, perturbed_detail)

    # CLI error handling
    bad_out = os.path.join(outputs_dir, "candidate_malformed_out.json")
    bad_run = run_cli(cand_dir, malformed_path, bad_out, args.run_timeout)
    cli_error_ok = (bad_run["rc"] == 2 and not os.path.exists(bad_out))
    report.case(
        "c_cli_invalid_input",
        "TASK.md: the event schema/CLI contract is unchanged (invalid input "
        "exits 2 and writes nothing)",
        "P1", cli_error_ok,
        "rc=%s output_written=%s" % (bad_run["rc"],
                                     os.path.exists(bad_out)))

    # constant-output guard: different inputs must give different documents
    distinct_ok = True
    distinct_detail = "ok"
    if "h_small" in candidate_docs and "h_fresh" in candidate_docs:
        if candidate_docs["h_small"] == candidate_docs["h_fresh"]:
            distinct_ok = False
            distinct_detail = "different inputs produced identical documents"
    report.gate(
        "constant_output", "P0", distinct_ok,
        distinct_detail)

    # ------------------------------------------------------- parity checks
    parity_weights = {"h_large": 0.40, "h_adversarial": 0.30,
                      "h_small": 0.15, "h_fresh": 0.15}
    parity_fraction = 0.0
    parity_failures = []
    byte_failures = []
    skipped_work = []
    contract_failures = []
    for name in RUN_PROFILES:
        parity_weight = parity_weights[name]
        if name not in candidate_docs:
            parity_failures.append("%s: candidate produced no usable output"
                                   % name)
            continue
        if name not in baseline_docs:
            parity_failures.append("%s: no baseline document" % name)
            continue
        candidate_doc = candidate_docs[name]
        problems = validate_contract(candidate_doc,
                                     len(events_by_profile[name]))
        if problems:
            contract_failures.append("%s: %s" % (name, problems[:4]))
        diff = document_diff(baseline_docs[name], candidate_doc)
        if diff:
            parity_failures.append("%s: %s" % (name, json.dumps(diff[:6])))
        else:
            parity_fraction += parity_weight
        base_bytes = read_bytes(os.path.join(
            outputs_dir, "baseline_%s_warm.json" % name))
        cand_bytes = read_bytes(os.path.join(
            outputs_dir, "candidate_%s_warm.json" % name))
        if base_bytes != cand_bytes:
            byte_failures.append(name)
        if candidate_doc.get("event_count") != len(events_by_profile[name]) \
                or candidate_doc.get("audit", {}).get("entries") != \
                len(events_by_profile[name]):
            skipped_work.append(name)

    parity_ok = not parity_failures and parity_fraction >= 0.999
    report.gate(
        "parity", "P0", parity_ok,
        "hidden-workload outputs match the pristine engine exactly" if
        parity_ok else
        "output parity mismatch: %s" % "; ".join(parity_failures[:3]),
        [os.path.join(outputs_dir, "baseline_*"),
         os.path.join(outputs_dir, "candidate_*")])
    report.case(
        "c_parity_hidden_workloads",
        "TASK.md: output must be identical to the pristine engine on the "
        "hidden small/large/adversarial/fresh workloads",
        "P0", parity_ok,
        "parity fraction %.2f; mismatches: %s"
        % (parity_fraction, "; ".join(parity_failures[:3]) or "none"))
    report.case(
        "c_byte_identical",
        "TASK.md: output must be byte-identical (canonical JSON, one line, "
        "trailing newline)",
        "P1", not byte_failures,
        "all profiles byte-identical" if not byte_failures else
        "byte differences on: %s" % ", ".join(byte_failures))
    report.case(
        "c_output_contract",
        "TASK.md: result schema, digest format and lifecycle counters stay "
        "valid",
        "P0", not contract_failures,
        "contract valid on all profiles" if not contract_failures else
        "; ".join(contract_failures[:3]))
    report.case(
        "c_no_skipped_work",
        "TASK.md: every event must be processed (no workload reduction)",
        "P0", not skipped_work,
        "event_count/audit.entries match the input on every profile" if not
        skipped_work else
        "event counts do not match on: %s" % ", ".join(skipped_work))

    # --------------------------------------------------------- timing runs
    timings = {"baseline": {}, "candidate": {}}
    run_hashes = {"baseline": {}, "candidate": {}}
    rss_values = metrics["peak_rss_bytes"]
    for name in RUN_PROFILES:
        run_hashes["baseline"][name] = set(baseline_hashes.get(name, set()))
        run_hashes["candidate"][name] = set(candidate_hashes.get(name, set()))
        timings["baseline"][name] = [baseline_runs[name][0]["elapsed_ms"]]
        timings["candidate"][name] = [candidate_runs[name][0]["elapsed_ms"]]

    for repeat in range(args.repeats):
        for name in RUN_PROFILES:
            base_out = os.path.join(outputs_dir, "baseline_%s_r%d.json"
                                    % (name, repeat))
            cand_out = os.path.join(outputs_dir, "candidate_%s_r%d.json"
                                    % (name, repeat))
            base_run = run_cli(base_dir, input_paths[name], base_out,
                               args.run_timeout)
            cand_run = run_cli(cand_dir, input_paths[name], cand_out,
                               args.run_timeout)
            timings["baseline"][name].append(base_run["elapsed_ms"])
            timings["candidate"][name].append(cand_run["elapsed_ms"])
            for tag, run, out in (("baseline", base_run, base_out),
                                  ("candidate", cand_run, cand_out)):
                if run["rc"] == 0 and os.path.exists(out):
                    run_hashes[tag][name].add(sha256_file(out))
                if run["timed_out"]:
                    timed_out = True
                if tag == "candidate" and run["rc"] != 0:
                    candidate_problems.append(
                        "%s run %d: rc=%s stderr=%s"
                        % (name, repeat, run["rc"],
                           run["stderr"][-300:]))

    # use measured (non-warmup) medians
    for name in RUN_PROFILES:
        base_series = timings["baseline"][name][1:] or \
            timings["baseline"][name]
        cand_series = timings["candidate"][name][1:] or \
            timings["candidate"][name]
        metrics["baseline_median_ms"][name] = round(
            statistics.median(base_series), 2)
        metrics["candidate_median_ms"][name] = round(
            statistics.median(cand_series), 2)
        base_med = metrics["baseline_median_ms"][name]
        cand_med = metrics["candidate_median_ms"][name]
        metrics["speedup"][name] = round(
            (base_med / cand_med) if cand_med > 0 else 0.0, 3)

    # ------------------------------------------------------- determinism
    det_ok = True
    det_detail = {}
    for name in RUN_PROFILES:
        cand_set = run_hashes["candidate"][name]
        base_set = run_hashes["baseline"][name]
        det_detail[name] = {"candidate_distinct_outputs": len(cand_set),
                            "baseline_distinct_outputs": len(base_set)}
        if len(cand_set) > 1:
            det_ok = False
        if len(base_set) > 1:
            return invalid_report(
                "pristine baseline is nondeterministic on %s" % name,
                report, metrics, determinism, evidence_refs, keep=keep,
                tmp_root=tmp_root)
    determinism["agreement"] = det_ok
    determinism["detail"] = det_detail
    report.gate(
        "determinism", "P0", det_ok,
        "repeated runs on the same input are byte-identical" if det_ok else
        "candidate output varies between runs: %s" % json.dumps(det_detail),
        [os.path.join(outputs_dir, "candidate_*")])

    # --------------------------------------------------------- anti-cheat
    after_state = file_state(cand_dir)
    new_files = sorted(set(after_state) - set(before_state))
    suspicious = [rel for rel in new_files
                  if "__pycache__" not in rel and not rel.endswith(".pyc")]
    report.gate(
        "no_cache_artifacts", "P0", not suspicious,
        "candidate does not write cache/state files" if not suspicious else
        "files created during grading: %s" % ", ".join(suspicious[:8]))

    large_base = metrics["baseline_median_ms"].get("h_large", 0.0)
    large_cand = metrics["candidate_median_ms"].get("h_large", 0.0)
    floor_ms = max(25.0, 0.01 * large_base)
    implausible = large_cand < floor_ms
    first_vs_later = None
    if args.repeats >= 2 and large_cand > 0:
        series = timings["candidate"]["h_large"][1:]
        first = timings["candidate"]["h_large"][0]
        if series and first > 0:
            first_vs_later = first / max(statistics.median(series), 0.001)
    cached_after_first = (first_vs_later is not None and
                          first_vs_later > 2.5)
    workload_evidence = read_bytes(os.path.join(
        outputs_dir, "candidate_h_large_r0.json"))
    real_work = len(workload_evidence) > 1000 and not implausible
    fake_speed_ok = real_work and not cached_after_first and not suspicious
    report.gate(
        "fake_speed", "P0", fake_speed_ok,
        "no timing/caching anomalies (large median %.1f ms, floor %.1f ms, "
        "first/later ratio %s)" % (
            large_cand, floor_ms,
            "n/a" if first_vs_later is None else "%.2f" % first_vs_later)
        if fake_speed_ok else
        "implausible timing or caching: median %.1f ms vs floor %.1f ms, "
        "first/later ratio %s, new files %s"
        % (large_cand, floor_ms,
           "n/a" if first_vs_later is None else "%.2f" % first_vs_later,
           suspicious[:4]),
        [os.path.join(outputs_dir, "candidate_h_large_*")])

    # ------------------------------------------------------------ scoring
    parity_points = 22.0 * parity_fraction if parity_ok else 0.0
    test_ratio = ((len(public_tests["passed"]) / public_tests["total"])
                  if public_tests["total"] else 0.0)
    tests_points = 6.0 * test_ratio if tests_ok else 0.0
    pristine_ratio = ((len(pristine["passed"]) / pristine["total"])
                      if pristine["total"] else 0.0)
    pristine_points = 6.0 * pristine_ratio if pristine_ok else 0.0
    determinism_points = 3.0 if det_ok else 0.0
    contract_ok = not contract_failures
    contract_points = 3.0 if contract_ok else 0.0
    correctness = (parity_points + tests_points + pristine_points +
                   determinism_points + contract_points)

    speed_large = metrics["speedup"].get("h_large", 0.0)
    speed_adv = metrics["speedup"].get("h_adversarial", 0.0)
    # Dead band: improvements below 1.2x are within measurement noise and
    # earn nothing.  3x on the large profile and 2.5x on the adversarial
    # profile earn full marks.
    large_speed_points = 25.0 * clamp((speed_large - 1.2) / 1.8)
    adv_speed_points = 10.0 * clamp((speed_adv - 1.2) / 1.3)
    performance = large_speed_points + adv_speed_points
    performance_valid = (parity_ok and contract_ok and not skipped_work and
                         not suspicious and fake_speed_ok and distinct_ok)
    if not performance_valid:
        performance = 0.0
        large_speed_points = 0.0
        adv_speed_points = 0.0

    base_rss = metrics["peak_rss_bytes"]["baseline"].get("h_large")
    cand_rss = metrics["peak_rss_bytes"]["candidate"].get("h_large")
    if base_rss and cand_rss:
        rss_ratio = cand_rss / float(base_rss)
        memory = 15.0 * clamp((1.5 - rss_ratio) / 0.7)
    else:
        rss_ratio = None
        memory = 0.0
    metrics["peak_rss_bytes"]["ratio_h_large"] = (
        round(rss_ratio, 3) if rss_ratio else None)

    debug_findings = debug_print_findings(cand_dir)
    changed_lines = change_size(base_dir, cand_dir)
    maint_points = 0.0
    if test_integrity_ok and not missing:
        maint_points += 2.0
    if public_tests["total"] >= (baseline_test_count or 0):
        maint_points += 2.0
    if not debug_findings:
        maint_points += 2.0
    if changed_lines <= 1500:
        maint_points += 2.0
    if not findings:
        maint_points += 1.0
    if not fixture_changes:
        maint_points += 1.0
    maintainability = maint_points

    report.case(
        "c_no_debug_prints",
        "TASK.md: keep the code maintainable (no debug prints, no dead "
        "debug scaffolding in replay/)",
        "P2", not debug_findings,
        "no debug prints" if not debug_findings else
        "; ".join(debug_findings[:4]))
    report.case(
        "c_change_scope",
        "TASK.md: implementation changes only, keep the result contract",
        "P2", changed_lines <= 1500,
        "%d changed lines in .py files" % changed_lines)
    report.case(
        "c_speedup_large",
        "TASK.md: at least 3x faster on a large workload",
        "P1", speed_large >= 3.0,
        "large median speedup %.2fx (baseline %.0f ms, candidate %.0f ms)"
        % (speed_large, large_base, large_cand))
    report.case(
        "c_speedup_adversarial",
        "TASK.md: similar speedups on adversarial workloads",
        "P2", speed_adv >= 2.0,
        "adversarial median speedup %.2fx (baseline %.0f ms, candidate %.0f ms)"
        % (speed_adv,
           metrics["baseline_median_ms"].get("h_adversarial", 0.0),
           metrics["candidate_median_ms"].get("h_adversarial", 0.0)))
    report.case(
        "c_memory_no_regression",
        "TASK.md: peak memory must not regress meaningfully",
        "P2", rss_ratio is not None and rss_ratio <= 1.2,
        "peak RSS ratio %.2fx (baseline %s bytes, candidate %s bytes)"
        % (rss_ratio if rss_ratio else 0.0, base_rss, cand_rss))

    total = correctness + performance + memory + maintainability
    total = round(total, 2)

    p0_gates = [gate for gate in report.gates if gate["severity"] == "P0"]
    all_p0 = all(gate["passed"] for gate in p0_gates)
    status = "PASS" if (all_p0 and total >= PASS_THRESHOLD) else "FAIL"

    document = {
        "task_id": TASK_ID,
        "grader_version": GRADER_VERSION,
        "status": status,
        "total": total,
        "categories": {
            "correctness": {
                "score": round(correctness, 2),
                "max": 40.0,
                "components": {
                    "parity_hidden_workloads": round(parity_points, 2),
                    "public_tests": round(tests_points, 2),
                    "pristine_tests": round(pristine_points, 2),
                    "determinism": round(determinism_points, 2),
                    "output_contract": round(contract_points, 2),
                },
            },
            "performance": {
                "score": round(performance, 2),
                "max": 35.0,
                "components": {
                    "large_speedup": round(large_speed_points, 2),
                    "adversarial_speedup": round(adv_speed_points, 2),
                },
                "scaling": {
                    "noise_dead_band_speedup": 1.2,
                    "large_full_marks_at_speedup": 3.0,
                    "adversarial_full_marks_at_speedup": 2.5,
                    "parity_gate_zeroes_performance": True,
                    "anti_cheat_gate_zeroes_performance": True,
                },
            },
            "memory": {
                "score": round(memory, 2),
                "max": 15.0,
                "components": {"peak_rss_ratio_h_large": metrics[
                    "peak_rss_bytes"]["ratio_h_large"]},
            },
            "maintainability": {
                "score": round(maintainability, 2),
                "max": 10.0,
                "components": {
                    "test_files_intact": test_integrity_ok,
                    "suite_not_reduced": public_tests["total"] >=
                    (baseline_test_count or 0),
                    "no_debug_prints": not debug_findings,
                    "changed_lines": changed_lines,
                    "no_forbidden_imports": not findings,
                    "fixtures_untampered": not fixture_changes,
                },
            },
        },
        "gates": report.gates,
        "cases": report.cases,
        "metrics": metrics,
        "determinism": determinism,
        "evidence_refs": evidence_refs,
        "pass_threshold": PASS_THRESHOLD,
        "candidate_problems": candidate_problems[:10],
        "work_dir": tmp_root,
        "kept_work_dir": keep,
    }
    return document


def invalid_report(reason, report, metrics, determinism, evidence_refs,
                   keep=False, tmp_root=None):
    return {
        "task_id": TASK_ID,
        "grader_version": GRADER_VERSION,
        "status": "INVALID",
        "total": 0.0,
        "invalid_reason": reason,
        "categories": {
            "correctness": {"score": 0.0, "max": 40.0},
            "performance": {"score": 0.0, "max": 35.0},
            "memory": {"score": 0.0, "max": 15.0},
            "maintainability": {"score": 0.0, "max": 10.0},
        },
        "gates": report.gates,
        "cases": report.cases,
        "metrics": metrics,
        "determinism": determinism,
        "evidence_refs": evidence_refs,
        "pass_threshold": PASS_THRESHOLD,
        "work_dir": tmp_root,
        "kept_work_dir": keep,
    }


def print_summary(document):
    print("=" * 78)
    print("task %s  status=%s  total=%.2f/100  (pass threshold %.0f)"
          % (document.get("task_id"), document.get("status"),
             document.get("total", 0.0),
             document.get("pass_threshold", PASS_THRESHOLD)))
    if document.get("invalid_reason"):
        print("INVALID: %s" % document["invalid_reason"])
    print("-" * 78)
    categories = document.get("categories", {})
    for name in ("correctness", "performance", "memory", "maintainability"):
        entry = categories.get(name, {})
        print("  %-15s %6.2f / %5.1f" % (name, entry.get("score", 0.0),
                                         entry.get("max", 0.0)))
    metrics = document.get("metrics", {})
    speedup = metrics.get("speedup", {})
    if speedup:
        print("-" * 78)
        print("  speedups: " + "  ".join(
            "%s=%.2fx" % (name, speedup[name]) for name in RUN_PROFILES
            if name in speedup))
        print("  medians (ms): baseline=%s"
              % json.dumps(metrics.get("baseline_median_ms", {})))
        print("                 candidate=%s"
              % json.dumps(metrics.get("candidate_median_ms", {})))
        ratio = metrics.get("peak_rss_bytes", {}).get("ratio_h_large")
        print("  peak RSS ratio (h_large): %s" % ratio)
    gates = document.get("gates", [])
    if gates:
        print("-" * 78)
        failed = [gate for gate in gates if not gate["passed"]]
        print("  gates: %d/%d passed" % (len(gates) - len(failed), len(gates)))
        for gate in gates:
            mark = "PASS" if gate["passed"] else "FAIL"
            print("   [%s] %-26s %s: %s"
                  % (mark, gate["id"], gate["severity"],
                     gate["detail"].splitlines()[0][:120]))
    cases = document.get("cases", [])
    failed_cases = [case for case in cases if not case["passed"]]
    if failed_cases:
        print("-" * 78)
        for case in failed_cases:
            print("   [FAIL] %-30s %s" % (case["id"],
                                         case["detail"].splitlines()[0][:120]))
    print("=" * 78)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Grade a t2_replay_perf candidate workspace.")
    parser.add_argument("--workspace", required=True,
                        help="candidate repository to grade (read only)")
    parser.add_argument("--json-out", required=True,
                        help="path of the JSON grading report")
    parser.add_argument("--baseline", default=None,
                        help="pristine baseline repository (defaults to the "
                             "bundled baseline_repo/)")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS,
                        help="measured timing repeats (default %d)"
                             % DEFAULT_REPEATS)
    parser.add_argument("--run-timeout", type=int,
                        default=DEFAULT_RUN_TIMEOUT,
                        help="per-replay timeout in seconds")
    parser.add_argument("--work-dir", default=None,
                        help="keep the grading work directory here")
    parser.add_argument("--quick", action="store_true",
                        help="shortcut: --repeats 1 (for control "
                             "validation only)")
    args = parser.parse_args(argv)
    if args.quick:
        args.repeats = 1
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")

    document = grade(args)
    out_path = os.path.abspath(args.json_out)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print_summary(document)
    return 0 if document["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
