"""NEGATIVE 1: ignore the database path and keep everything in RAM.

Expected failure: non_memory_storage gate (and restart persistence).
"""
import sys

from _patchlib import main_wrapper, replace_once


def patch(path, workspace):
    replace_once(
        path,
        """            self._conn = sqlite3.connect(
                self.db_path,""",
        """            self._conn = sqlite3.connect(
                ":memory:",""",
        "neg01-inmemory",
    )


if __name__ == "__main__":
    main_wrapper("neg01_inmemory", patch)
