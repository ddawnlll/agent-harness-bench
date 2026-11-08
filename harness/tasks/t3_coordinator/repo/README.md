# t3_coordinator -- durable job coordinator

Part of a coding-agent benchmark. Read `TASK.md` for the full contract.

## Layout

```
coordinator/        the package to implement (partial implementation)
  core.py           Coordinator: claim/lease/complete/fail/repair live here
  models.py         Job / Event / Worker / JobState value objects
  errors.py         exception hierarchy
  schema.py         SQLite schema bootstrap (tables + append-only triggers)
  cli.py            python3 -m coordinator.cli
tests/              public tests (do not modify existing files; add your own)
TASK.md             requirements + public API contract
```

## Run the public tests

```sh
python3 -m unittest discover -s tests -v
```

No dependencies beyond the Python 3.13 standard library. No network access is
needed or allowed.

## Try it by hand

```sh
python3 -m coordinator.cli --db /tmp/jobs.db init
python3 -m coordinator.cli --db /tmp/jobs.db add job-1 --payload '{"cmd": "make test"}'
python3 -m coordinator.cli --db /tmp/jobs.db claim --worker w1
python3 -m coordinator.cli --db /tmp/jobs.db complete job-1 --worker w1 --result '{"ok": true}'
python3 -m coordinator.cli --db /tmp/jobs.db status job-1
python3 -m coordinator.cli --db /tmp/jobs.db audit --job job-1
python3 -m coordinator.cli --db /tmp/jobs.db stats
```

## Requirements

Python 3.13, standard library only, file-backed SQLite persistence, atomic
claiming, expiring leases with heartbeats, crash recovery, bounded retries,
idempotent completion, append-only audit receipts, and safe handling of corrupt
persisted state. See `TASK.md`.
