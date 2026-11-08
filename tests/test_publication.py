#!/usr/bin/env python3
"""Publication consistency tests (stdlib unittest)."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "results" / "publication-data.json").read_text())


class PublicationData(unittest.TestCase):
    def test_nine_unique_trials(self):
        trials = DATA["trials"]
        self.assertEqual(len(trials), 9)
        self.assertEqual(len({(t["task_id"], t["agent_id"]) for t in trials}), 9)

    def test_known_results(self):
        def t(task, agent):
            return next(x for x in DATA["trials"] if x["task_id"] == task and x["agent_id"] == agent)
        self.assertEqual([t("t1_order_lifecycle", a)["verified_pass"] for a in ["pi-bolt", "jcode", "hax"]],
                         [False, True, False])
        for task in ["t2_replay_perf", "t3_coordinator"]:
            for agent in ["pi-bolt", "jcode", "hax"]:
                self.assertTrue(t(task, agent)["verified_pass"])
        self.assertEqual(DATA["verdict"]["recommendation"], "JCODE")

    def test_h07_is_the_discriminator(self):
        def case(agent):
            t = next(x for x in DATA["trials"] if x["task_id"] == "t1_order_lifecycle" and x["agent_id"] == agent)
            return next(c for c in t["cases"] if c["id"] == "h07")
        self.assertTrue(case("jcode")["passed"])
        self.assertFalse(case("pi-bolt")["passed"])
        self.assertFalse(case("hax")["passed"])

    def test_costs_and_memory_are_consistent(self):
        for agent, agg in DATA["agents"].items():
            rows = [t for t in DATA["trials"] if t["agent_id"] == agent]
            nominal = round(sum(t["tokens"]["list_cost_usd"] for t in rows), 9)
            self.assertAlmostEqual(nominal, agg["nominal_cost_usd"], places=9)
            if agg["verified_passes"]:
                self.assertAlmostEqual(agg["cost_per_verified_usd"],
                                       nominal / agg["verified_passes"], places=9)
            else:
                self.assertIsNone(agg["cost_per_verified_usd"])

    def test_figures_exist_and_are_labeled(self):
        for name in ["hero", "race", "safety", "timing", "economics", "resources"]:
            for theme in ["light", "dark"]:
                svg = (ROOT / "assets" / f"{name}-{theme}.svg").read_text()
                self.assertIn("<title>", svg)
                self.assertIn("<desc>", svg)
                self.assertIn('role="img"', svg)
                self.assertNotIn("<script", svg)


if __name__ == "__main__":
    unittest.main()
