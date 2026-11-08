#!/usr/bin/env python3
"""Run hidden unittest methods and emit a JSON result map."""
import importlib.util
import io
import json
import os
import sys
import unittest


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(test_file: str) -> int:
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromName("test_hidden")
    mod = load_module(test_file, "test_hidden")
    suite = unittest.defaultTestLoader.loadTestsFromModule(mod)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    bad = {}
    for case, tb in list(result.failures) + list(result.errors):
        bad[case.id()] = tb.strip().splitlines()[-1][:500]
    out = {
        "tests_run": result.testsRun,
        "cases": bad,
        "stdout": stream.getvalue()[-4000:],
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
