"""End-to-end tests for the command line interface."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
GOLDEN = os.path.join(ROOT, "tests", "golden")

sys.path.insert(0, ROOT)


def run_cli(events, out):
    return subprocess.run(
        [sys.executable, "-m", "replay.cli", "--events", events,
         "--out", out],
        cwd=ROOT, capture_output=True, text=True)


class CliTests(unittest.TestCase):
    def test_small_fixture_produces_golden_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            proc = run_cli(os.path.join(DATA, "small.jsonl"), out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out, "r", encoding="utf-8") as handle:
                produced = handle.read()
        with open(os.path.join(GOLDEN, "small.json"), "r",
                  encoding="utf-8") as handle:
            expected = handle.read()
        self.assertEqual(produced, expected)

    def test_repeated_runs_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "a.json")
            second = os.path.join(tmp, "b.json")
            events = os.path.join(DATA, "adversarial_small.jsonl")
            for path in (first, second):
                proc = run_cli(events, path)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(first, "rb") as handle:
                raw_first = handle.read()
            with open(second, "rb") as handle:
                raw_second = handle.read()
        self.assertEqual(raw_first, raw_second)

    def test_output_is_single_canonical_json_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            proc = run_cli(os.path.join(DATA, "small.jsonl"), out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out, "r", encoding="utf-8") as handle:
                text = handle.read()
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.count("\n"), 1)
        document = json.loads(text)
        self.assertEqual(document["schema"], "replay.v1")

    def test_invalid_input_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = os.path.join(tmp, "bad.jsonl")
            with open(events, "w", encoding="utf-8") as handle:
                handle.write("not an event\n")
            out = os.path.join(tmp, "out.json")
            proc = run_cli(events, out)
            self.assertEqual(proc.returncode, 2)
            self.assertFalse(os.path.exists(out))

    def test_missing_input_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            proc = run_cli(os.path.join(tmp, "nope.jsonl"), out)
            self.assertNotEqual(proc.returncode, 0)

    def test_stdout_summary_mentions_event_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.json")
            proc = run_cli(os.path.join(DATA, "small.jsonl"), out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("600", proc.stdout)


if __name__ == "__main__":
    unittest.main()
