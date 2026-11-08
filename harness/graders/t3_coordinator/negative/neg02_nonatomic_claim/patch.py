"""NEGATIVE 2: claim is check-then-act in two autocommitted statements.

A short sleep widens the window so two workers reliably pick the same job.
Expected failure: no_duplicate_ownership gate.
"""
from _patchlib import main_wrapper, replace_once


def patch(path, workspace):
    replace_once(
        path,
        """        now = self._now()
        with self._write_tx() as conn:
            while True:""",
        """        now = self._now()
        conn = self._conn  # no explicit transaction: check-then-act
        while True:""",
        "neg02-no-transaction",
    )
    replace_once(
        path,
        """                if row is None:
                    return None""",
        """                if row is None:
                    return None
                time.sleep(0.002)  # widen the race window""",
        "neg02-race-window",
    )


if __name__ == "__main__":
    main_wrapper("neg02_nonatomic_claim", patch)
