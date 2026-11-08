"""Tests for the synthetic event generator and the JSONL loader."""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from replay.events import (  # noqa: E402
    EventError,
    generate_events,
    load_events,
    validate_event,
    write_events,
)

DATA = os.path.join(ROOT, "data")


class GeneratorTests(unittest.TestCase):
    def test_same_seed_same_bytes(self):
        first = generate_events(7, 200, accounts=4, target_live=20)
        second = generate_events(7, 200, accounts=4, target_live=20)
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.jsonl")
            b = os.path.join(tmp, "b.jsonl")
            write_events(a, first)
            write_events(b, second)
            with open(a, "rb") as fh:
                raw_a = fh.read()
            with open(b, "rb") as fh:
                raw_b = fh.read()
        self.assertEqual(raw_a, raw_b)

    def test_different_seeds_differ(self):
        first = generate_events(1, 200, accounts=4, target_live=20)
        second = generate_events(2, 200, accounts=4, target_live=20)
        self.assertNotEqual(first, second)

    def test_sequence_numbers_are_strictly_increasing(self):
        events = generate_events(3, 300, accounts=5, target_live=30)
        seqs = [event["seq"] for event in events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))

    def test_timestamps_are_non_decreasing(self):
        events = generate_events(5, 400, accounts=5, target_live=30,
                                 dup_ts_prob=0.5)
        stamps = [event["ts"] for event in events]
        self.assertEqual(stamps, sorted(stamps))

    def test_cancels_reference_existing_order_ids(self):
        events = generate_events(9, 300, accounts=4, target_live=25)
        submitted = {event["order_id"] for event in events
                     if event["type"] == "submit"}
        cancelled = {event["order_id"] for event in events
                     if event["type"] == "cancel"}
        self.assertTrue(cancelled)
        self.assertTrue(cancelled.issubset(submitted))

    def test_events_are_plain_json(self):
        events = generate_events(11, 50, accounts=2, target_live=10)
        for event in events:
            round_tripped = json.loads(json.dumps(event))
            self.assertEqual(event, round_tripped)


class LoaderTests(unittest.TestCase):
    def test_committed_fixtures_load(self):
        for name in ("small.jsonl", "medium.jsonl",
                     "adversarial_small.jsonl"):
            events = load_events(os.path.join(DATA, name))
            self.assertTrue(events)
            for event in events:
                self.assertIn(event["type"], ("submit", "cancel"))

    def test_load_returns_canonical_order(self):
        events = [
            {"ts": 5, "seq": 2, "type": "cancel", "order_id": 1},
            {"ts": 5, "seq": 1, "type": "cancel", "order_id": 2},
            {"ts": 4, "seq": 9, "type": "cancel", "order_id": 3},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "events.jsonl")
            write_events(path, events)
            loaded = load_events(path)
        self.assertEqual([event["order_id"] for event in loaded], [3, 2, 1])

    def test_blank_lines_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "events.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n")
                fh.write('{"ts":1,"seq":1,"type":"cancel","order_id":1}\n')
                fh.write("\n")
            loaded = load_events(path)
        self.assertEqual(len(loaded), 1)

    def test_missing_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "events.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"ts":1,"seq":1,"type":"submit","order_id":1}\n')
            with self.assertRaises(EventError):
                load_events(path)

    def test_bad_side_is_rejected(self):
        with self.assertRaises(EventError):
            validate_event({"ts": 1, "seq": 1, "type": "submit",
                            "order_id": 1, "account": "a", "symbol": "S",
                            "side": "up", "price": 10, "qty": 1}, 1)

    def test_non_positive_price_is_rejected(self):
        with self.assertRaises(EventError):
            validate_event({"ts": 1, "seq": 1, "type": "submit",
                            "order_id": 1, "account": "a", "symbol": "S",
                            "side": "buy", "price": 0, "qty": 1}, 1)

    def test_invalid_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "events.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json}\n")
            with self.assertRaises(EventError):
                load_events(path)


if __name__ == "__main__":
    unittest.main()
