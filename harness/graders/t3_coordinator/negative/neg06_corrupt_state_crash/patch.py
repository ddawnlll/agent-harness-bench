"""NEGATIVE 6: corrupt persisted rows crash the coordinator and are never repaired.

Expected failure: corrupted_state_safe gate.
"""
from _patchlib import main_wrapper, replace_once


def patch(path, workspace):
    replace_once(path, "        if auto_repair:\n            self.repair()",
                 "        if False:  # repair disabled\n            self.repair()",
                 "neg06-no-autorepair")
    replace_once(
        path,
        """        keys = row.keys()
        max_attempts = row["max_attempts"]""",
        """        keys = row.keys()
        if row["state"] not in ALL_STATES:
            raise ValueError(f"corrupt job state for {row['job_id']}: {row['state']!r}")
        max_attempts = row["max_attempts"]""",
        "neg06-crash-on-corrupt-state",
    )


if __name__ == "__main__":
    main_wrapper("neg06_corrupt_state_crash", patch)
