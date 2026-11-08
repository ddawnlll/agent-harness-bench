# Task 3 -- Crash-safe durable job coordinator

You are implementing the durable job coordinator used to drive unattended
software-engineering workers. Workers are processes that may be killed at any
moment (`SIGKILL`), run concurrently on the same machine, retry, and sometimes
send the same acknowledgement twice. The coordinator must never lose work,
never let two workers own the same active job, and never retry forever.

The repository already contains a **partial implementation**. Persistence,
schema bootstrap, `add_job`, `status`, `list_jobs`, `audit`, `stats` and a
single-worker happy path work. The hard parts are missing or incorrect:

| Area | Current state |
| --- | --- |
| Atomic claiming | check-then-act, two workers can lease the same job |
| Lease expiry / crash recovery | leases are written but never expire or get reclaimed |
| Heartbeats | recorded, but the lease is not extended |
| Idempotent completion | repeated `complete()` overwrites the result and appends a second `COMPLETED` receipt |
| Bounded retries | `max_attempts` is stored but never enforced |
| Corrupt persisted state | `status()` raises `ValueError`; there is no repair path |

Your job is to make the coordinator satisfy the contract below.

## Ground rules

* Python 3.13, **standard library only** (`sqlite3`, `threading`, `subprocess`,
  `os`, `time`, `json`, ...). No pip, no third-party packages, **no network**.
* Do **not** modify files in `tests/`, or `TASK.md`. You may (and should) add
  new test files of your own under `tests/`.
* Keep the documented public API compatible. You may add methods/keywords but
  every documented name/signature below must keep working.
* Persistence must be a real file-backed SQLite database. `":memory:"` is
  rejected; a coordinator that only remembers state inside one process does not
  pass.
* Run the public tests with:

  ```sh
  python3 -m unittest discover -s tests -v
  ```

  Hidden tests will additionally drive your code directly through the public
  Python API from **multiple concurrent OS processes**, kill worker processes
  with `SIGKILL`, reopen the database from new processes, and inject corrupt
  rows directly into SQLite before constructing a coordinator.

## Public API contract

```python
from coordinator import Coordinator, Worker, Job, Event, JobState
from coordinator.errors import (
    CoordinatorError, DuplicateJobError, UnknownJobError, LeaseError,
    InvalidTransitionError, CorruptStateError, StorageError,
)

coord = Coordinator(
    db_path,                 # str, path of the SQLite file
    *,
    max_attempts: int = 3,   # default retry budget per job
    lease_seconds: float = 30.0,
    auto_repair: bool = True,  # sanitise corrupt state on open (see repair)
)
coord.close()                # idempotent; also supports `with Coordinator(...)`
coord.init_schema()          # idempotent, safe from concurrent processes
```

### Methods

| Method | Semantics |
| --- | --- |
| `add_job(job_id, payload=None, *, max_attempts=None) -> Job` | Insert a new job in `PENDING`. `job_id` must be a non-empty string and unique; a duplicate raises `DuplicateJobError`. `payload` is any JSON-serialisable value (may be `None`). Writes an `ADDED` event. |
| `claim(worker_id) -> Job \| None` | **Atomically** lease one claimable job to `worker_id`, else `None`. Claimable = state `PENDING`/`RETRY`, or state `CLAIMED`/`RUNNING` whose lease exists and has expired. Claim increments `attempts`, sets state `CLAIMED`, sets `job.worker_id` and `job.lease_expires_at ≈ now + lease_seconds`, and writes a `CLAIMED` event. If the claim would make `attempts > max_attempts`, the job must instead become terminal `FAILED` (event `RETRIES_EXHAUSTED`) and the claim must move on to another job / return `None`. `attempts` must never exceed `max_attempts`. |
| `heartbeat(job_id, worker_id) -> Job` | Extend the lease of a job owned by `worker_id` to `now + lease_seconds` and update `job.lease_expires_at`. Only the current owner with a **non-expired** lease may heartbeat. Writes a `HEARTBEAT` event. |
| `start(job_id, worker_id) -> Job` | Optional `CLAIMED -> RUNNING` transition for the lease owner. Must reject any other state/owner. |
| `complete(job_id, worker_id, result=None) -> Job` | Mark an owned job `DONE`, persist `result`, release the lease, write exactly **one** `COMPLETED` event for it. Requires the caller to be the current lease owner with a non-expired lease. If the job is already `DONE` the call is a **no-op success** (regardless of `worker_id`) returning the stored job: the stored result must not change, no second result is recorded, and at most a `COMPLETION_REPEATED` event may be appended. |
| `fail(job_id, worker_id, error="failed") -> Job` | Record a failed attempt. Requires owner + non-expired lease. If `attempts >= max_attempts` the job becomes terminal `FAILED`; otherwise it becomes `RETRY` and is claimable immediately, with the lease released. Repeating `fail()` on an already-`FAILED` job is a no-op returning the job. |
| `status(job_id) -> Job` | Current job record (including lease fields). Unknown id -> `UnknownJobError`. Must not crash on corrupt persisted rows (see `repair`). |
| `list_jobs(state=None, limit=None) -> list[Job]` | Jobs in creation order, optional state filter. |
| `audit(job_id=None, limit=None) -> list[Event]` | Ordered append-only receipts. Unknown/corrupt rows must be returned safely (unknown types verbatim, non-JSON details as raw text), never raise. |
| `stats() -> dict` | Must contain at least: `jobs` (total), `results` (number of **distinct** jobs completed successfully), `by_state` (dict state -> count), `events`, `attempts` (sum), `workers`. `results` must stay 1 when a job is completed (or a duplicate completion is received) 5 times. |
| `repair() -> dict` | Deterministically clean up corrupt persisted state (see below). Idempotent: a second call reports zero changes. Called automatically from `__init__` when `auto_repair=True`. |

### Value objects

`Job` is a dataclass with attributes
`job_id, state, payload, attempts, max_attempts, worker_id, lease_expires_at,
result, error, created_at, updated_at` and a `to_dict()` method.
`state` is one of the strings `PENDING`, `CLAIMED`, `RUNNING`, `RETRY`, `DONE`,
`FAILED` (`coordinator.JobState` exposes these as class attributes).
After `complete()`/`fail()` the lease is released, so `worker_id` and
`lease_expires_at` are `None`. When a *persisted* state is corrupt, `status()`
returns the raw stored string instead of raising.
`Event` has `event_id, ts, type, job_id, worker_id, detail` and `to_dict()`.

### State machine

```
PENDING --claim--> CLAIMED --start--> RUNNING --complete--> DONE
   ^                   |                  |
   |                   +---fail-----------+--> RETRY --claim--> ...
   |                   |                      (budget left)
   +---fail (RETRY)    |
                       +---budget exhausted--> FAILED   (terminal)
lease expiry / crash of owner: CLAIMED|RUNNING becomes claimable again
DONE, FAILED are terminal and never claimable
```

`RETRY` behaves like `PENDING` for claiming; it exists so the audit trail can
distinguish "never attempted" from "an attempt failed".

### Errors

* `UnknownJobError` -- unknown `job_id`.
* `DuplicateJobError` -- `add_job` with an existing id.
* `LeaseError` -- foreign worker, missing lease, lease already expired
  (heartbeat/complete/fail/start).
* `InvalidTransitionError` -- transition not allowed from the current state
  (e.g. `fail()` on a `DONE` job, `start()` on `PENDING`).
* All of the above derive from `CoordinatorError`.

## Durable audit receipts

Every state change appends one row to `events`; the table is append-only
(triggers reject `UPDATE`/`DELETE`). Required event types:

`ADDED`, `CLAIMED`, `STARTED`, `HEARTBEAT`, `COMPLETED`, `RETRY_SCHEDULED`,
`RETRIES_EXHAUSTED`, `FAILED`, `COMPLETION_REPEATED`, `REPAIRED`,
`ORPHAN_LEASE_REMOVED`, `REJECTED` (free to add more).
`audit()` must let a fresh process reconstruct the history of a job, and
`audit(job_id)` must return only that job's events.

## Corrupt / incomplete persisted state

A previous buggy version may have written garbage. Your coordinator is opened on
a database that can contain, for example:

* a `jobs` row whose `state` is not one of the six valid states;
* a `leases` row whose `job_id` has no matching job row;
* an `events` row with an unknown `type` and a truncated (non-JSON) `detail`;
* a `jobs` row whose `attempts` exceeds `max_attempts` (or is negative);
* a `jobs` row whose JSON `payload` is truncated.

Opening the coordinator and ordinary operations (`claim`, `status`, `list_jobs`,
`audit`, `stats`, `repair`) must **not raise** because of such rows and must not
delete jobs or rewrite stored results. Deterministic repair rules:

1. orphan leases are removed (event `ORPHAN_LEASE_REMOVED`);
2. jobs with an unknown state are quarantined to terminal `FAILED`, keeping
   payload/result/error (event `REPAIRED`);
3. leases attached to terminal (`DONE`/`FAILED`) jobs are removed;
4. `attempts` is clamped into `[0, max_attempts]`;
5. missing timestamps are filled in;
6. jobs/results are never deleted; `status()` on a corrupt-state job returns it
   (after repair it is `FAILED`).

`status()` must also tolerate a corrupt `payload`: return the raw text instead of
raising.

## Retry budget

`max_attempts` is the maximum number of **claims** a job may receive (per job,
overridable with `add_job(..., max_attempts=N)`). The invariant
`0 <= job.attempts <= job.max_attempts` must hold for every persisted row, across
restarts and crash loops. A job that exhausts its budget is `FAILED` and can
never be claimed again.

## CLI

`python3 -m coordinator.cli --db PATH <command>` must keep working:
`init`, `add`, `claim`, `start`, `heartbeat`, `complete`, `fail`, `status`,
`list`, `audit`, `stats`, `repair`, `serve-worker`. Keep the existing flags
(`--worker`, `--payload`, `--result`, `--error`, `--state`, `--job`,
`--max-jobs`) and print JSON.

## Definition of done

1. `python3 -m unittest discover -s tests -v` passes (public tests unchanged).
2. Add at least one new test of your own under `tests/` that covers a recovery
   or concurrency scenario you implemented (it must pass).
3. The hidden suite passes: multi-process claim stress, `SIGKILL` mid-ownership,
   lease expiry/reclaim, heartbeat extension, restart from a new process,
   duplicate completion, retry exhaustion, corrupt-state repair, invalid input.
