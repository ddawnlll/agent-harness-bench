#!/usr/bin/env python3
"""Validate the Task 1 grader: oracle passes, seeded and negatives fail."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.abspath(os.path.join(HERE, "../../tasks/t1_order_lifecycle/reference"))
SEEDED = os.path.abspath(os.path.join(HERE, "../../tasks/t1_order_lifecycle/repo"))

NEGATIVES = {
    "n1_premature_ack": {
        "expect_gate": "G1_PREMATURE_PROTECTION",
        "edits": {"v9/engine.py": [(
            "            stop_price=round(stop_price, 8),\n        )",
            "            stop_price=round(stop_price, 8),\n            status=ACTIVE,\n        )")]},
    },
    "n2_fill_dedupe_bug": {
        "expect_gate": "G3_FILL_ATTRIBUTION",
        "edits": {"v9/order.py": [(
            "        if fill_id in self.fill_ids:\n            return False",
            "        if self.filled_qty > 0:\n            return False")]},
    },
    "n3_restart_promotes": {
        "expect_gate": "G5_RESTART_FIDELITY",
        "edits": {"v9/engine.py": [(
            '                status=d["status"],',
            '                status=ACTIVE if d["status"] == PROT_PENDING else d["status"],')]},
    },
    "n4_stale_callback": {
        "expect_gate": "G4_PROTECTION_LOSS",
        "edits": {"v9/protection.py": [(
            "        if version <= self.version:\n            return \"IGNORED_STALE\"\n        self.version = version",
            "        self.version = version")]},
    },
    "n5_no_identity_check": {
        "expect_gate": "G7_NATIVE_IDENTITY",
        "edits": {"v9/order.py": [(
            '        if self.native_id is not None and native_id is not None and native_id != self.native_id:\n            return "IDENTITY_MISMATCH"\n',
            "")]},
    },
    "n6_cancel_drops_protection": {
        "expect_gate": "G6_CANCEL_RACE",
        "edits": {"v9/engine.py": [(
            "        # A position opened by this order must keep its protection regardless\n        # of the cancel outcome.\n        if self.position(order.symbol).is_open:\n            self._reconcile_protection(order)",
            "        if result == \"CANCELLED\" and self.position(order.symbol).is_open:\n            prot = self.protection_for(order.symbol)\n            if prot is not None:\n                self.broker.cancel_stop(prot.client_stop_id)\n                prot.cancel()\n                self.protection_for_symbol.pop(order.symbol, None)")]},
    },
    "n7_deleted_tests": {"expect_gate": "G8_TEST_INTEGRITY", "delete": ["tests/test_public.py"]},
    "n8_hardcoded_fixture": {
        "expect_gate": "G9_NO_SHORTCUT",
        "edits": {"v9/engine.py": [(
            "    def _on_restart(self, ev: dict) -> None:",
            "    def _on_restart(self, ev: dict) -> None:\n        # GHOST fixture shortcut\n")]},
    },
}


def grade(ws):
    out = tempfile.mktemp(suffix=".json")
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "grade.py"), "--workspace", ws, "--json-out", out],
        capture_output=True, text=True, timeout=600)
    data = json.load(open(out))
    os.unlink(out)
    return data


def copy_ws(src):
    d = tempfile.mkdtemp(prefix="t1val-")
    dst = os.path.join(d, "ws")
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return d, dst


def main():
    report = {"oracle": {}, "seeded": {}, "negatives": {}, "determinism": {}, "ok": True}

    d, ws = copy_ws(SEEDED)
    subprocess.run(["bash", os.path.join(HERE, "oracle/apply_oracle.sh"), ws], check=True)
    oracle_runs = [grade(ws) for _ in range(3)]
    shutil.rmtree(d, ignore_errors=True)
    report["oracle"] = {"status": oracle_runs[0]["status"], "total": oracle_runs[0]["total"],
                        "gates": {g["id"]: g["passed"] for g in oracle_runs[0]["gates"]}}
    report["determinism"] = {
        "repeats": 3,
        "totals": [r["total"] for r in oracle_runs],
        "statuses": [r["status"] for r in oracle_runs],
        "agreement": len({(r["status"], r["total"]) for r in oracle_runs}) == 1,
    }
    if report["oracle"]["status"] != "PASS" or oracle_runs[0]["total"] < 80:
        report["ok"] = False
    if not report["determinism"]["agreement"]:
        report["ok"] = False

    seeded = grade(SEEDED)
    report["seeded"] = {"status": seeded["status"], "total": seeded["total"]}
    if seeded["status"] == "PASS":
        report["ok"] = False

    for name, spec in NEGATIVES.items():
        d, ws = copy_ws(REF)
        try:
            for rel, edits in spec.get("edits", {}).items():
                path = os.path.join(ws, rel)
                txt = open(path, encoding="utf-8").read()
                for old, new in edits:
                    if old not in txt:
                        raise SystemExit(f"negative {name}: anchor not found in {rel}")
                    txt = txt.replace(old, new, 1)
                open(path, "w", encoding="utf-8").write(txt)
            for rel in spec.get("delete", []):
                p = os.path.join(ws, rel)
                if os.path.exists(p):
                    os.remove(p)
            res = grade(ws)
            gates = {g["id"]: g["passed"] for g in res["gates"]}
            expected_gate = spec["expect_gate"]
            ok = res["status"] != "PASS" and gates.get(expected_gate) is False
            report["negatives"][name] = {
                "status": res["status"], "total": res["total"],
                "expected_failing_gate": expected_gate,
                "failed_gates": [g for g, ok_ in gates.items() if not ok_],
                "expectation_met": ok,
            }
            if not ok:
                report["ok"] = False
        finally:
            shutil.rmtree(d, ignore_errors=True)

    with open(os.path.join(HERE, "validation_report.json"), "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
