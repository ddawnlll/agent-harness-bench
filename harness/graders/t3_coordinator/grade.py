#!/usr/bin/env python3
"""Independent grader for task t3_coordinator (crash-safe durable job coordinator).

Usage::

    python3 grade.py --workspace DIR --json-out OUT.json [--keep-temp]

Guarantees
----------
* The candidate workspace is **never** mutated: everything runs in a temporary
  copy, and a tree hash of the candidate is taken before and after.
* Public tests, hidden functional/concurrency/crash/corruption tests and static
  anti-cheat checks are all executed against that copy.
* A JSON report is always written, even on internal failure.  Exit code is 0
  only for PASS.

Scoring (100): functional 40, concurrency & crash recovery 35, tests added /
regression 15, maintainability & observability 10.  Ten P0 gates must all pass.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE_REPO = os.path.join(HERE, "baseline_repo")
HIDDEN_SRC = os.path.join(HERE, "hidden")
TASK_ID = "t3"
GRADER_VERSION = "1.0.0"
TOTAL_TIME_BUDGET_S = 175.0

TREE_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", ".git", ".venv", "venv", "node_modules",
    "*.db", "*.db-wal", "*.db-shm", ".mypy_cache", ".pytest_cache",
)

# --------------------------------------------------------------------------
# Case inventory: id -> (category, weight, requirement)
# Category maxima equal the sum of their weights (40 / 35 / 15 / 10).
# --------------------------------------------------------------------------
CASES: dict[str, tuple[str, int, str]] = {
    "F1": ("functional", 4, "add_job persists payload; duplicate id rejected"),
    "F2": ("functional", 4, "claim atomically leases one job; no second owner"),
    "F3": ("functional", 4, "complete persists result and releases the lease"),
    "F4": ("functional", 6, "repeated completion is idempotent (no double count)"),
    "F5": ("functional", 6, "bounded retries; exhaustion is terminal FAILED"),
    "F7": ("functional", 4, "heartbeat extends the lease"),
    "F8": ("functional", 6, "invalid input rejected with documented errors"),
    "F9": ("functional", 2, "ordered, filterable append-only audit trail"),
    "F10": ("functional", 4, "corrupt persisted state tolerated and repaired"),
    "C1": ("concurrency_recovery", 12, "8 processes x 200 jobs: exactly one owner per job"),
    "C2": ("concurrency_recovery", 8, "SIGKILL mid-ownership + lease expiry reclaim"),
    "C3": ("concurrency_recovery", 5, "late duplicate completion is not lost/corrupting"),
    "C4": ("concurrency_recovery", 4, "state survives process restart (graceful + abrupt)"),
    "C5": ("concurrency_recovery", 4, "kill one of three workers; survivors recover all"),
    "C6": ("concurrency_recovery", 2, "expired lease reclaimed with incremented attempt"),
    "T1": ("tests", 10, "public test suite passes unmodified"),
    "T2": ("tests", 5, "candidate added at least one passing test of their own"),
    "M1": ("maintainability", 3, "public API is documented (docstrings)"),
    "M2": ("maintainability", 2, "no TODO/FIXME/NotImplementedError stubs left"),
    "M3": ("maintainability", 2, "audit receipts are append-only (enforced in SQL)"),
    "M4": ("maintainability", 2, "no bare excepts / debug prints; sources compile"),
    "M5": ("maintainability", 1, "no eval/exec/wildcard imports"),
}

CATEGORY_MAX = {"functional": 40, "concurrency_recovery": 35, "tests": 15, "maintainability": 10}

# Static cases are gate inputs, not scored cases.
STATIC_CASES = {
    "S_TESTS": "baseline test files are present and byte-identical",
    "S_IMPORTS": "no third-party imports and no network modules",
    "S_MEMDB": "no in-memory SQLite database is used for storage",
}

HIDDEN_MODULES = [
    ("public", "tests", "test_*.py", 60.0, ["T1"]),
    ("functional", None, "test_functional.py", 60.0, ["F1", "F2", "F3", "F4", "F5", "F7", "F8", "F9"]),
    ("corruption", None, "test_corruption.py", 45.0, ["F10"]),
    ("recovery", None, "test_recovery.py", 110.0, ["C1", "C2", "C3", "C4", "C5", "C6"]),
]

GATES = [
    ("no_duplicate_ownership", ["C1", "C2", "C5", "C6"]),
    ("persistence_restart", ["C4"]),
    ("no_lost_completion", ["C3", "C5"]),
    ("bounded_retries", ["F5"]),
    ("non_memory_storage", ["C4", "S_MEMDB"]),
    ("crash_recovery", ["C2", "C6"]),
    ("test_integrity", ["T1", "S_TESTS"]),
    ("corrupted_state_safe", ["F10"]),
    ("no_network_thirdparty", ["S_IMPORTS"]),
    ("idempotent_completion", ["F4"]),
]

NETWORK_MODULES = {
    "socket", "ssl", "urllib", "http", "httplib", "ftplib", "telnetlib",
    "smtplib", "poplib", "imaplib", "xmlrpc", "requests", "aiohttp", "httpx",
    "websocket", "websockets", "paramiko", "boto3", "grpc",
}
STUB_MARKERS = ("notimplemented", "todo", "fixme", "xxx", "hack:")
MEMORY_DB_RE = re.compile(
    r"""(?:sqlite3\.)?connect\(\s*(?:[^)]*?)(?P<q>["']):memory:(?P=q)""", re.IGNORECASE
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(root: str) -> str:
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in ("__pycache__", ".git"))
        for filename in sorted(filenames):
            if filename.endswith(".pyc"):
                continue
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, root)
            digest.update(rel.encode("utf-8", "surrogateescape"))
            try:
                digest.update(sha256_file(path).encode("ascii"))
            except OSError:
                digest.update(b"<unreadable>")
    return digest.hexdigest()


def python_files(root: str, skip_hidden: bool = True):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d != "__pycache__" and not (skip_hidden and d.startswith("_hidden"))
        )
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def parse_tree(path: str):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        source = handle.read()
    return source, ast.parse(source, filename=path)


def write_json(path: str, payload) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False, default=str)
    os.replace(tmp, path)


def run_with_timeout(cmd, cwd, timeout, env=None):
    """Run a command in its own process group; return (rc, stdout, stderr, timed_out)."""
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=max(1.0, timeout))
        return proc.returncode, out, err, False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            out, err = "", ""
        return None, out, err, True


# --------------------------------------------------------------------------
# static analysis / anti-cheat
# --------------------------------------------------------------------------
class StaticReport:
    def __init__(self):
        self.cases: dict[str, dict] = {}
        self.metrics: dict[str, object] = {}

    def add(self, case_id, passed, detail):
        self.cases[case_id] = {"passed": bool(passed), "detail": detail}

    def fail_all(self, detail):
        for case_id in ("S_TESTS", "S_IMPORTS", "S_MEMDB"):
            self.add(case_id, False, detail)
        for case_id in ("M1", "M2", "M3", "M4", "M5"):
            self.cases.setdefault(case_id, {"passed": False, "detail": detail})


def baseline_test_hashes() -> dict[str, str]:
    tests_root = os.path.join(BASELINE_REPO, "tests")
    hashes = {}
    for path in python_files(tests_root, skip_hidden=False):
        rel = os.path.relpath(path, BASELINE_REPO)
        hashes[rel] = sha256_file(path)
    return hashes


def check_test_integrity(workspace: str, baseline: dict[str, str]):
    missing, modified, extra = [], [], []
    tests_root = os.path.join(workspace, "tests")
    present = {}
    if os.path.isdir(tests_root):
        for path in python_files(tests_root, skip_hidden=False):
            rel = os.path.relpath(path, workspace)
            present[rel] = sha256_file(path)
    for rel, expected in baseline.items():
        if rel not in present:
            missing.append(rel)
        elif present[rel] != expected:
            modified.append(rel)
    for rel in present:
        if rel not in baseline:
            extra.append(rel)
    passed = not missing and not modified
    detail = "baseline tests intact"
    if missing or modified:
        detail = f"missing={sorted(missing)} modified={sorted(modified)}"
    return (
        passed,
        detail,
        {"missing": sorted(missing), "modified": sorted(modified), "extra": sorted(extra)},
    )


def local_module_names(workspace: str) -> set:
    names = set()
    try:
        entries = list(os.scandir(workspace))
    except OSError:
        return names
    for entry in entries:
        if entry.is_dir() and os.path.exists(os.path.join(entry.path, "__init__.py")):
            names.add(entry.name)
        elif entry.is_file() and entry.name.endswith(".py"):
            names.add(entry.name[:-3])
    return names


def scan_imports(workspace: str):
    stdlib = set(sys.stdlib_module_names)
    local = local_module_names(workspace)
    third_party, network, parse_errors = set(), set(), []
    for path in python_files(workspace):
        try:
            _, tree = parse_tree(path)
        except (SyntaxError, ValueError) as exc:
            parse_errors.append(f"{os.path.relpath(path, workspace)}: {exc}")
            continue
        for node in ast.walk(tree):
            roots = []
            if isinstance(node, ast.Import):
                roots = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue
                if node.module:
                    roots = [node.module]
            for name in roots:
                root = name.split(".")[0]
                if root in NETWORK_MODULES or name.split(".")[0] in NETWORK_MODULES:
                    network.add(name)
                if root in stdlib or root in local:
                    continue
                third_party.add(name)
    return third_party, network, parse_errors


def scan_memory_db(workspace: str):
    hits = []
    for path in python_files(workspace):
        try:
            source, _ = parse_tree(path)
        except (SyntaxError, ValueError):
            continue
        for match in MEMORY_DB_RE.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            hits.append(f"{os.path.relpath(path, workspace)}:{line}")
    return hits


def coordinator_sources(workspace: str):
    root = os.path.join(workspace, "coordinator")
    if not os.path.isdir(root):
        return []
    return [p for p in python_files(root, skip_hidden=False)]


def scan_maintainability(workspace: str):
    """Deterministic maintainability checks; each returns (passed, detail)."""
    sources = coordinator_sources(workspace)
    if not sources:
        detail = "coordinator/ package not found"
        return {key: (False, detail) for key in ("M1", "M2", "M3", "M4", "M5")}

    parsed, parse_errors = {}, []
    for path in sources:
        try:
            source, tree = parse_tree(path)
            parsed[path] = (source, tree)
        except (SyntaxError, ValueError) as exc:
            parse_errors.append(f"{os.path.relpath(path, workspace)}: {exc}")

    # M1: Coordinator class + public methods documented.
    m1_passed, m1_detail = False, "Coordinator class not found"
    for path, (_, tree) in parsed.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "Coordinator":
                public = [
                    child for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not child.name.startswith("_")
                ]
                undocumented = [
                    child.name for child in public if not ast.get_docstring(child)
                ]
                has_class_doc = bool(ast.get_docstring(node))
                m1_passed = has_class_doc and not undocumented and len(public) >= 6
                m1_detail = (
                    "Coordinator and all public methods documented"
                    if m1_passed
                    else f"class_doc={has_class_doc} undocumented={undocumented} "
                         f"public_methods={len(public)}"
                )
                break
        if m1_passed:
            break

    # M2: no stub markers in the package.
    markers = []
    for path, (source, _) in parsed.items():
        lowered = source.lower()
        for marker in STUB_MARKERS:
            if marker in lowered:
                line = lowered[: lowered.index(marker)].count("\n") + 1
                markers.append(f"{os.path.relpath(path, workspace)}:{line}:{marker}")
    m2_passed = not markers
    m2_detail = "no stub markers" if m2_passed else f"stub markers found: {markers[:6]}"

    # M3: append-only audit enforced in SQL.
    triggers = []
    for path, (source, _) in parsed.items():
        for match in re.finditer(r"CREATE\s+TRIGGER", source, re.IGNORECASE):
            triggers.append(f"{os.path.relpath(path, workspace)}:{source.count(chr(10), 0, match.start()) + 1}")
    m3_passed = bool(triggers)
    m3_detail = (
        f"append-only triggers declared ({len(triggers)})"
        if m3_passed else "no CREATE TRIGGER for the audit table"
    )

    # M4: no bare excepts / debug prints; everything compiles.
    bare, prints, compile_errors = [], [], list(parse_errors)
    for path, (source, tree) in parsed.items():
        rel = os.path.relpath(path, workspace)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                bare.append(f"{rel}:{node.lineno}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "print" and not rel.endswith("cli.py"):
                prints.append(f"{rel}:{node.lineno}")
        try:
            compile(source, path, "exec")
        except SyntaxError as exc:
            compile_errors.append(f"{rel}: {exc}")
    m4_passed = not bare and not prints and not compile_errors
    m4_detail = (
        "no bare excepts, no debug prints, sources compile"
        if m4_passed
        else f"bare_except={bare[:4]} prints={prints[:4]} compile_errors={compile_errors[:4]}"
    )

    # M5: no eval/exec/import *.
    bad = []
    for path, (source, tree) in parsed.items():
        rel = os.path.relpath(path, workspace)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in ("eval", "exec"):
                bad.append(f"{rel}:{node.lineno}:{node.func.id}")
            if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
                bad.append(f"{rel}:{node.lineno}:import*")
    m5_passed = not bad
    m5_detail = "no eval/exec/wildcard imports" if m5_passed else f"found: {bad[:5]}"

    return {
        "M1": (m1_passed, m1_detail),
        "M2": (m2_passed, m2_detail),
        "M3": (m3_passed, m3_detail),
        "M4": (m4_passed, m4_detail),
        "M5": (m5_passed, m5_detail),
    }


def run_static_analysis(workspace: str, baseline: dict[str, str], report: StaticReport):
    report.metrics["tree_digest"] = tree_digest(workspace)

    passed, detail, hashes = check_test_integrity(workspace, baseline)
    report.add("S_TESTS", passed, detail)
    report.metrics["test_hashes"] = hashes

    third_party, network, parse_errors = scan_imports(workspace)
    passed = not third_party and not network
    detail = "stdlib-only imports, no network modules"
    if not passed:
        detail = f"third_party={sorted(third_party)} network={sorted(network)}"
    report.add("S_IMPORTS", passed, detail)
    report.metrics["imports"] = {
        "third_party": sorted(third_party),
        "network": sorted(network),
        "parse_errors": parse_errors,
    }

    memory_hits = scan_memory_db(workspace)
    report.add(
        "S_MEMDB", not memory_hits,
        "no sqlite3 :memory: connection" if not memory_hits
        else f"in-memory sqlite connection at {memory_hits}",
    )
    report.metrics["memory_db_hits"] = memory_hits

    maintain = scan_maintainability(workspace)
    for case_id, (ok, text) in maintain.items():
        report.add(case_id, ok, text)
    return report


# --------------------------------------------------------------------------
# test execution
# --------------------------------------------------------------------------
def install_hidden(workspace: str) -> str:
    name = "_hidden_" + os.urandom(4).hex()
    target = os.path.join(workspace, name)
    shutil.copytree(HIDDEN_SRC, target)
    return target


def run_test_group(runner, workspace, label, module, pattern, timeout, artifacts):
    env = dict(os.environ)
    env["PYTHONPATH"] = workspace + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONHASHSEED", None)
    out_path = os.path.join(artifacts, f"{label}.json")
    start_dir = module if module else os.path.dirname(runner)
    cmd = [sys.executable, runner, "--start-dir", start_dir, "--pattern", pattern,
           "--out", out_path, "--quiet"]
    started = time.monotonic()
    rc, out, err, timed_out = run_with_timeout(cmd, workspace, timeout, env=env)
    elapsed = round(time.monotonic() - started, 2)
    payload = None
    if os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            payload = None
    result = {
        "label": label,
        "command": " ".join(cmd),
        "rc": rc,
        "timed_out": timed_out,
        "elapsed_s": elapsed,
        "stdout_tail": (out or "")[-3000:],
        "stderr_tail": (err or "")[-3000:],
        "payload": payload,
    }
    return result


def collect_case_results(groups) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for group in groups:
        payload = group.get("payload") or {}
        records = payload.get("records") or []
        for record in records:
            case = record.get("case")
            if not case:
                continue
            existing = results.get(case)
            # A case id is unique to one hidden test; keep the worst outcome.
            if existing is None or (existing["passed"] and not record.get("passed")):
                results[case] = {
                    "passed": bool(record.get("passed")),
                    "detail": record.get("detail") or "",
                    "duration_ms": record.get("duration_ms"),
                    "id": record.get("id"),
                }
    return results


def mark_missing_cases(results, groups, expected_ids):
    """Any case in the inventory that produced no record counts as failed."""
    for case_id in expected_ids:
        if case_id in results:
            continue
        detail = "case did not run"
        for group in groups:
            payload = group.get("payload") or {}
            if payload.get("discovery_error"):
                detail = f"discovery failed: {payload['discovery_error'][:600]}"
                break
            if group.get("timed_out"):
                detail = f"test module timed out after {group['elapsed_s']}s"
                break
            if payload.get("counts", {}).get("tests") == 0:
                detail = "no tests were discovered in the module"
        results[case_id] = {"passed": False, "detail": detail, "duration_ms": None, "id": None}
    return results


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def invalid_report(reason: str) -> dict:
    cases = [
        {
            "id": case_id,
            "requirement": requirement,
            "severity": "P0" if case_id in {g for _, ids in GATES for g in ids} else "P1",
            "passed": False,
            "detail": f"invalid submission: {reason}",
            "duration_ms": None,
        }
        for case_id, (_, _, requirement) in CASES.items()
    ]
    for case_id, requirement in STATIC_CASES.items():
        cases.append({
            "id": case_id,
            "requirement": requirement,
            "severity": "P0",
            "passed": False,
            "detail": f"invalid submission: {reason}",
            "duration_ms": None,
        })
    return {
        "task_id": TASK_ID,
        "status": "INVALID",
        "total": 0.0,
        "categories": {
            name: {"score": 0.0, "max": maximum, "passed_cases": 0, "total_cases": 0}
            for name, maximum in CATEGORY_MAX.items()
        },
        "gates": [
            {"id": gate_id, "severity": "P0", "passed": False,
             "detail": f"invalid submission: {reason}", "evidence": []}
            for gate_id, _ in GATES
        ],
        "cases": cases,
        "determinism": {"repeats": 0, "agreement": False},
        "evidence_refs": [],
        "grader": {"version": GRADER_VERSION, "task_id": TASK_ID,
                   "error": reason},
    }


def grade(workspace: str, json_out: str, keep_temp: bool = False) -> dict:
    started = time.monotonic()
    deadline = started + TOTAL_TIME_BUDGET_S
    artifacts = os.path.join(
        os.path.dirname(os.path.abspath(json_out)),
        os.path.splitext(os.path.basename(json_out))[0] + ".artifacts",
    )
    os.makedirs(artifacts, exist_ok=True)

    if not os.path.isdir(workspace):
        return invalid_report(f"workspace not found: {workspace}")
    if not os.path.isdir(BASELINE_REPO):
        return invalid_report(f"grader baseline missing: {BASELINE_REPO}")
    py_count = sum(1 for _ in python_files(workspace))
    if py_count == 0:
        return invalid_report("workspace contains no Python files")

    candidate_digest_before = tree_digest(workspace)
    baseline = baseline_test_hashes()

    temp_root = tempfile.mkdtemp(prefix="t3-grade-")
    workspace_copy = os.path.join(temp_root, "workspace")
    shutil.copytree(workspace, workspace_copy, ignore=TREE_IGNORE, symlinks=True)

    static = StaticReport()
    try:
        run_static_analysis(workspace_copy, baseline, static)
    except Exception as exc:  # noqa: BLE001 - never lose the report
        static.fail_all(f"static analysis error: {type(exc).__name__}: {exc}")

    hidden_dir = install_hidden(workspace_copy)
    runner = os.path.join(hidden_dir, "run_hidden.py")

    groups = []
    for label, module, pattern, cap, _case_ids in HIDDEN_MODULES:
        remaining = deadline - time.monotonic()
        if remaining <= 5:
            groups.append({
                "label": label, "rc": None, "timed_out": True, "elapsed_s": 0.0,
                "payload": None, "stdout_tail": "", "stderr_tail": "",
                "command": "<skipped: time budget exhausted>",
            })
            continue
        timeout = max(5.0, min(cap, remaining))
        try:
            groups.append(run_test_group(runner, workspace_copy, label, module, pattern,
                                         timeout, artifacts))
        except Exception as exc:  # noqa: BLE001
            groups.append({
                "label": label, "rc": None, "timed_out": False, "elapsed_s": 0.0,
                "payload": None, "stdout_tail": "",
                "stderr_tail": f"runner crashed: {type(exc).__name__}: {exc}",
                "command": "<runner crashed>",
            })

    # Tamper check #2: tests must not change while the suite runs.
    integrity_after, detail_after, hashes_after = check_test_integrity(workspace_copy, baseline)
    static_ok = static.cases.get("S_TESTS", {}).get("passed", False)
    if not integrity_after:
        static.add("S_TESTS", False, f"test files changed during the run: {detail_after}")

    # Public test group summary.
    public_group = next((g for g in groups if g["label"] == "public"), None)
    public_payload = (public_group or {}).get("payload") or {}
    public_records = public_payload.get("records") or []
    public_counts = public_payload.get("counts") or {}
    public_passed = bool(
        public_group
        and not public_group.get("timed_out")
        and not public_payload.get("discovery_error")
        and public_counts.get("tests", 0) > 0
        and public_counts.get("failures", 1) == 0
        and public_counts.get("errors", 1) == 0
    )

    case_results = collect_case_results(groups)
    hidden_expected = [c for c, (cat, _, _) in CASES.items() if cat != "tests"
                       and c not in ("M1", "M2", "M3", "M4", "M5")]
    mark_missing_cases(case_results, groups, hidden_expected)

    # T1 / T2 from the public run.
    failing_public = [
        record["id"] for record in public_records if not record.get("passed")
    ]
    t1_detail = (
        f"public suite: {public_counts.get('tests', 0)} tests, "
        f"{public_counts.get('failures', 0)} failures, {public_counts.get('errors', 0)} errors"
    )
    if public_group and public_group.get("timed_out"):
        t1_detail = f"public suite timed out after {public_group['elapsed_s']}s"
    elif public_payload.get("discovery_error"):
        t1_detail = f"public suite discovery error: {public_payload['discovery_error'][:600]}"
    elif failing_public:
        t1_detail += f"; failing: {failing_public[:8]}"
    case_results["T1"] = {
        "passed": public_passed,
        "detail": t1_detail,
        "duration_ms": int((public_group or {}).get("elapsed_s", 0) * 1000) or None,
        "id": "public-suite",
    }

    baseline_stems = {os.path.splitext(os.path.basename(rel))[0] for rel in baseline}
    candidate_tests_dir = os.path.join(workspace_copy, "tests")
    candidate_stems = set()
    if os.path.isdir(candidate_tests_dir):
        for path in python_files(candidate_tests_dir, skip_hidden=False):
            stem = os.path.splitext(os.path.basename(path))[0]
            if stem != "__init__":
                candidate_stems.add(stem)
    new_stems = sorted(candidate_stems - baseline_stems)
    new_records = [
        record for record in public_records
        if any(stem in record.get("id", "").split(".") for stem in new_stems)
    ]
    t2_passed = bool(new_stems) and bool(new_records) and all(
        r.get("passed") for r in new_records
    )
    case_results["T2"] = {
        "passed": t2_passed,
        "detail": (
            f"added test modules: {new_stems}, {len(new_records)} tests"
            if new_stems else "no tests were added under tests/"
        ),
        "duration_ms": None,
        "id": "added-tests",
    }

    # Static cases feed scored M* cases and gates.
    for case_id in ("M1", "M2", "M3", "M4", "M5"):
        record = static.cases.get(case_id, {"passed": False, "detail": "not evaluated"})
        case_results[case_id] = {
            "passed": bool(record["passed"]),
            "detail": record["detail"],
            "duration_ms": None,
            "id": "static",
        }

    # ------------------------------------------------ scoring and gates
    categories = {}
    for name, maximum in CATEGORY_MAX.items():
        scored = [(cid, weight) for cid, (cat, weight, _) in CASES.items() if cat == name]
        earned = sum(weight for cid, weight in scored
                     if case_results.get(cid, {}).get("passed"))
        passed_count = sum(1 for cid, _ in scored if case_results.get(cid, {}).get("passed"))
        categories[name] = {
            "score": round(float(earned), 2),
            "max": maximum,
            "passed_cases": passed_count,
            "total_cases": len(scored),
        }
    total = round(sum(cat["score"] for cat in categories.values()), 2)

    static_results = {
        cid: {"passed": bool(rec["passed"]), "detail": rec["detail"]}
        for cid, rec in static.cases.items()
    }
    gate_reports = []
    for gate_id, evidence_ids in GATES:
        evidence = []
        gate_passed = True
        details = []
        for evidence_id in evidence_ids:
            if evidence_id in static_results:
                record = static_results[evidence_id]
            else:
                record = case_results.get(evidence_id, {"passed": False,
                                                        "detail": "case did not run"})
            evidence.append(evidence_id)
            if not record["passed"]:
                gate_passed = False
                details.append(f"{evidence_id}: {record['detail'][:400]}")
        gate_reports.append({
            "id": gate_id,
            "severity": "P0",
            "passed": gate_passed,
            "detail": "all evidence passed" if gate_passed else "; ".join(details)[:1500],
            "evidence": evidence,
        })

    determinism = _determinism(workspace_copy, artifacts)

    candidate_digest_after = tree_digest(workspace)
    workspace_untouched = candidate_digest_before == candidate_digest_after

    all_gates_pass = all(gate["passed"] for gate in gate_reports)
    status = "PASS" if total >= 80.0 and all_gates_pass else "FAIL"

    cases_out = []
    for case_id, (_, _, requirement) in CASES.items():
        record = case_results.get(case_id, {"passed": False, "detail": "case did not run"})
        cases_out.append({
            "id": case_id,
            "requirement": requirement,
            "severity": "P0" if any(case_id in ids for _, ids in GATES) else "P1",
            "passed": bool(record["passed"]),
            "detail": str(record.get("detail") or "")[:1500],
            "duration_ms": record.get("duration_ms"),
        })
    for case_id, requirement in STATIC_CASES.items():
        record = static_results.get(case_id, {"passed": False, "detail": "not evaluated"})
        cases_out.append({
            "id": case_id,
            "requirement": requirement,
            "severity": "P0",
            "passed": bool(record["passed"]),
            "detail": str(record.get("detail") or "")[:1500],
            "duration_ms": None,
        })

    evidence_refs = [os.path.abspath(p) for p in [
        os.path.join(artifacts, "public.json"),
        os.path.join(artifacts, "functional.json"),
        os.path.join(artifacts, "corruption.json"),
        os.path.join(artifacts, "recovery.json"),
    ] if os.path.exists(p)]
    write_json(os.path.join(artifacts, "static_analysis.json"), {
        "cases": static.cases,
        "metrics": static.metrics,
        "test_hashes_after": hashes_after,
        "workspace_untouched": workspace_untouched,
    })
    evidence_refs.append(os.path.abspath(os.path.join(artifacts, "static_analysis.json")))

    elapsed = round(time.monotonic() - started, 2)
    report = {
        "task_id": TASK_ID,
        "status": status,
        "total": total,
        "categories": categories,
        "gates": gate_reports,
        "cases": cases_out,
        "determinism": determinism,
        "evidence_refs": evidence_refs,
        "grader": {
            "version": GRADER_VERSION,
            "task_id": TASK_ID,
            "elapsed_s": elapsed,
            "workspace": os.path.abspath(workspace),
            "workspace_untouched": workspace_untouched,
            "baseline_repo": BASELINE_REPO,
            "baseline_sha": _read_baseline_sha(),
            "test_command": "python3 -m unittest discover -s tests -v",
            "time_budget_s": TOTAL_TIME_BUDGET_S,
            "groups": [
                {
                    "label": group["label"],
                    "rc": group["rc"],
                    "timed_out": group["timed_out"],
                    "elapsed_s": group["elapsed_s"],
                    "discovery_error": (group.get("payload") or {}).get("discovery_error"),
                    "counts": (group.get("payload") or {}).get("counts"),
                }
                for group in groups
            ],
        },
    }

    if keep_temp:
        report["grader"]["temp_dir"] = temp_root
    else:
        shutil.rmtree(temp_root, ignore_errors=True)
    return report


def _read_baseline_sha() -> str:
    manifest = os.path.join(HERE, "manifest.json")
    try:
        with open(manifest, "r", encoding="utf-8") as handle:
            return json.load(handle).get("start_commit", "")
    except (OSError, ValueError):
        return ""


def _determinism(workspace: str, artifacts: str) -> dict:
    """Determinism evidence: verdict agreement across repeated stress rounds."""
    path = os.path.join(workspace, "_hidden_evidence", "c1_stress.json")
    rounds = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        rounds = payload.get("rounds") or []
    except (OSError, ValueError):
        rounds = []
    verdicts = [bool(entry.get("passed")) for entry in rounds]
    agreement = bool(verdicts) and len(set(verdicts)) == 1
    if rounds:
        write_json(os.path.join(artifacts, "stress_rounds.json"), {"rounds": rounds})
    return {
        "repeats": len(rounds),
        "agreement": agreement,
        "stress_rounds": rounds,
    }


def print_summary(report: dict, json_out: str) -> None:
    print("=" * 78)
    print(f"t3_coordinator grader v{report['grader'].get('version')} "
          f"-- task {report['task_id']}")
    print(f"status: {report['status']}   total: {report['total']}/100")
    if report["grader"].get("error"):
        print(f"error : {report['grader']['error']}")
    print("-" * 78)
    for name, cat in report["categories"].items():
        print(f"  {name:22s} {cat['score']:6.2f}/{cat['max']:<3} "
              f"({cat['passed_cases']}/{cat['total_cases']} cases)")
    print("-" * 78)
    print("P0 gates:")
    for gate in report["gates"]:
        mark = "PASS" if gate["passed"] else "FAIL"
        print(f"  [{mark}] {gate['id']}")
        if not gate["passed"]:
            print(f"         {gate['detail'][:500]}")
    print("-" * 78)
    failed = [case for case in report["cases"] if not case["passed"]]
    print(f"cases: {len(report['cases']) - len(failed)}/{len(report['cases'])} passed")
    for case in failed:
        print(f"  FAIL {case['id']:5s} {case['requirement'][:60]}")
        detail = (case["detail"] or "").strip().splitlines()
        if detail:
            print(f"        {detail[-1][:240]}")
    det = report["determinism"]
    print(f"determinism: repeats={det['repeats']} agreement={det['agreement']}")
    print(f"elapsed: {report['grader'].get('elapsed_s')}s")
    print(f"json: {os.path.abspath(json_out)}")
    print("=" * 78)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Grade the t3_coordinator task")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = grade(args.workspace, args.json_out, keep_temp=args.keep_temp)
    except Exception as exc:  # noqa: BLE001 - always produce a report
        report = invalid_report(
            f"grader internal error: {type(exc).__name__}: {exc}\n"
            + traceback.format_exc()[-3000:]
        )
    try:
        write_json(args.json_out, report)
    except OSError as exc:  # pragma: no cover - defensive
        print(f"could not write json report: {exc}", file=sys.stderr)
    print_summary(report, args.json_out)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
