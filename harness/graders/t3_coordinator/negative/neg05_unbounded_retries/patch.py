"""NEGATIVE 5: max_attempts is ignored; attempts grow forever.

Expected failure: bounded_retries gate.
"""
from _patchlib import main_wrapper, replace_once


def patch(path, workspace):
    replace_once(path, "                if attempts > budget:", "                if False:",
                 "neg05-claim-no-budget")
    replace_once(path, "            exhausted = attempts >= budget", "            exhausted = False",
                 "neg05-fail-no-budget")


if __name__ == "__main__":
    main_wrapper("neg05_unbounded_retries", patch)
