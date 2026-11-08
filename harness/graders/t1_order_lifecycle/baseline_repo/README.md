# V9 execution-safety core

Deterministic, dependency-free order lifecycle / position / protective-stop
engine used for replay-based safety analysis.

```
python3 -m unittest discover -s tests -v
python3 demo.py scenarios/basic_long.jsonl
```

The package performs no network or disk I/O; scenarios are JSONL event
streams. See `TASK.md` for the acceptance contract.
