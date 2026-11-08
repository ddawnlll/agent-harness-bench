# GRADER NOTES -- t3_coordinator (crash-safe durable concurrent job coordinator)

Grader version 1.0.0. Task repo commit
`4b4f88d092b049f9ae02d9aa678337ace96c4d10` (recorded in `manifest.json`).

## 1. What the task is

The candidate gets a working-but-naive SQLite job coordinator
(`tasks/t3_coordinator/repo`). Persistence, schema bootstrap, `add_job`,
`status`, `list_jobs`, `audit`, `stats` and a single-worker happy path work.
The hard semantics are missing or wrong:

| Missing/wrong in fixture | Hidden case that proves it |
| --- | --- |
| check-then-act claim (two owners possible) | C1, C2, C5 |
| leases never expire, never reclaimed | C2, C5, C6 |
| heartbeat does not extend the lease | F7 |
| completion not idempotent | F4, C3 |
| retries unbounded | F5 |
| no repair / crash on corrupt rows | F10 |

`TASK.md` in the repo is the contract (API, state machine, error types, event
vocabulary, repair rules, CLI). The reference solution lives in
`oracle/files/coordinator` and is a full replacement of the package.

## 2. Layout

```
graders/t3_coordinator/
  grade.py                  grader CLI (--workspace, --json-out, --keep-temp)
  manifest.json             task id, start commit, inventory, weights, gates, timeouts
  baseline_repo/            exact export of the task repo's initial commit
  hidden/                   hidden suite (never shipped in the task repo)
    harness.py              temp DBs, child process helpers, bounded waits
    child_worker.py         real worker processes (add/claim/complete/heartbeat/read/stress)
    test_functional.py      F1,F2,F3,F4,F5,F7,F8,F9
    test_corruption.py      F10
    test_recovery.py        C1..C6
    run_hidden.py           unittest -> per-test JSON (case ids from docstrings)
  oracle/
    apply_oracle.sh         WORKSPACE -> replaces coordinator/, adds a regression test
    files/coordinator/      reference implementation
    files/tests/            reference regression tests (so T2 is earned, not assumed)
  negative/neg01..neg07/    apply.sh + patch.py + expected_gates.json
  validate.py               end-to-end validation (baseline, oracle x3, negatives, determinism)
  validation_report.json    latest validation run
  validation_evidence/      both full validation runs
```

## 3. Grading pipeline

`python3 grade.py --workspace DIR --json-out OUT.json`

1. **Never mutates the candidate.** The workspace is copied to a temp dir
   (ignoring `__pycache__`, `.git`, `*.db`, venvs). A SHA-256 tree digest is
   taken before and after; `grader.workspace_untouched` reports equality.
2. **Static analysis on the copy** (anti-cheat + maintainability), see §5.
3. **Hidden suite injected** into the copy as `_hidden_<random>/` and executed
   from there (random name, so a candidate cannot pre-branch on the path).
4. **Public suite** run via the same JSON runner with `--start-dir tests`.
5. **Hidden modules** run one subprocess per module, each with its own timeout,
   under a global 175 s deadline. A hang is a failed case, not a hung grader.
6. **Test tampering checked twice**: baseline file hashes are compared before
   and after the public run (self-modification during the run is detected too).
7. **Score, gates, determinism, evidence**; JSON is written even on internal
   error; human summary on stdout. Exit 0 only for PASS.

Timing on the reference solution: **~19 s**. Worst observed broken solution
(neg03, no lease expiry) ~40 s. Hard ceiling 175 s.

## 4. Scoring (100 points)

Category maxima equal the sum of their case weights, so category score is
simply the weight of passed cases.

| Category | Cases (weights) | Max |
| --- | --- | --- |
| functional | F1 4, F2 4, F3 4, F4 6, F5 6, F7 4, F8 6, F9 2, F10 4 | 40 |
| concurrency_recovery | C1 12, C2 8, C3 5, C4 4, C5 4, C6 2 | 35 |
| tests | T1 10 (public suite green), T2 5 (candidate added a passing test) | 15 |
| maintainability | M1 3, M2 2, M3 2, M4 2, M5 1 | 10 |

`PASS` iff `total >= 80` **and** all ten P0 gates pass.

| P0 gate | Evidence | Why it is P0 |
| --- | --- | --- |
| `no_duplicate_ownership` | C1 (2 rounds of 8 processes x 200 jobs), C2, C5, C6 | Two workers on one job is the core safety failure |
| `persistence_restart` | C4 | Restart must not lose jobs/attempts/leases/results |
| `no_lost_completion` | C3, C5 | A late duplicate must not erase the winner's result |
| `bounded_retries` | F5 | `0 <= attempts <= max_attempts`, terminal FAILED |
| `non_memory_storage` | C4 + `S_MEMDB` | In-process-only storage is not durability |
| `crash_recovery` | C2, C6 | Expired lease must be reclaimed after SIGKILL |
| `test_integrity` | T1 + `S_TESTS` (twice) | Deleted/weakened public tests invalidate the run |
| `corrupted_state_safe` | F10 | Corrupt rows must not crash the service or lose data |
| `no_network_thirdparty` | `S_IMPORTS` | Stdlib-only, offline requirement |
| `idempotent_completion` | F4 | Duplicate acks are expected in an at-least-once system |

`INVALID` is reserved for grader-side impossibilities (workspace missing, no
Python files, missing `baseline_repo`); a broken submission is `FAIL`.

## 5. Anti-cheat (concrete checks)

| Cheat | Detection |
| --- | --- |
| modify/delete/weaken public tests | SHA-256 of every `baseline_repo/tests/**` file vs candidate, before *and* after the suite runs (`S_TESTS`); T1 also requires the suite to be green |
| in-memory only database | C4 reads the sqlite file from a *new* process and counts rows; `S_MEMDB` regex finds `sqlite3.connect(":memory:")` |
| fake durability (state kept in one process) | C4 spawns three separate processes (add/claim/close, SIGKILL, read) |
| sleeping fake concurrency / thread tricks | C1/C5 use real OS processes spawned around a shared start timestamp; C5 SIGKILLs a real process group; threads cannot fake `SIGKILL` survival |
| hardcoded job ids / fabricated jobs | random UUID job ids and a per-run stress token; the child verifies `payload["job"] == job.job_id` and the result's `by` field against the recorded claimant |
| double counting via lying `stats()` | cross-checks `stats()["results"]`, `list_jobs("DONE")`, the raw `results`/`jobs` tables, and the count of `COMPLETED` audit receipts per job |
| third-party packages / network | `ast` import scan against `sys.stdlib_module_names` + local modules; blocklist for `socket`, `ssl`, `urllib`, `http`, `requests`, `aiohttp`, `httpx`, `paramiko`, ... (`S_IMPORTS`) |
| prompt/injection style test-path tricks | hidden tests are copied into a randomly named directory inside the workspace copy and imported with the hidden dir first on `sys.path` (a candidate `harness.py` cannot shadow it) |
| mutating the grader | grader runs from its own directory, only ever writes to `OUT.json`, `<OUT>.artifacts/` and a temp dir |

Note: a candidate *could* in principle detect the hidden directory at runtime and
branch on it. That is not detectable statically; the mitigation is that the
contract is fully documented, the same behaviour is checked through the CLI and
through the public API from independent processes, and hidden case ids are not
present in the workspace. See limitations.

## 6. Determinism strategy

* All waiting is bounded polling (`wait_until`) with explicit timeouts; no
  unbounded sleeps as assertions.
* Lease timings use real time but with margins: functional expiry tests use
  1.0-1.2 s leases, C2 waits `claimed_at + lease + 0.3 s` measured from the
  child's own report, F7 proves extension numerically before sleeping past the
  original expiry.
* C1 runs **2 rounds** in the same test; both must pass, and each round's
  metrics are written to `_hidden_evidence/c1_stress.json`, which the grader
  turns into `determinism.repeats` / `determinism.agreement`.
* Transient SQLite `database is locked` errors are retried inside the child
  workers (storage contention is a quality issue, not the failure this task
  measures); semantic errors are never retried.
* Claim ordering is **not** asserted (FIFO is not in the contract); tests only
  use the job id reported by the worker.
* `validate.py` grades the oracle 3x in independent workspaces and requires
  identical verdict signatures; race-driven negatives are repeated.

## 7. Known limitations / deliberate choices

1. **Durability level.** The oracle uses WAL + `synchronous=NORMAL`, which is
   crash-safe for process death (`SIGKILL`) but not for power loss. Tests only
   model process death, as documented.
2. **Expired lease completion is rejected.** `complete()` after the lease
   expired raises `LeaseError`, and a heartbeat cannot revive an expired lease.
   This is documented in `TASK.md` (fencing); an implementation that accepts
   late completions fails F8 by design.
3. **Duplicate completion of a DONE job is a no-op success** for *any* worker
   (documented), so a late ack cannot be lost. Implementations that raise
   instead fail F4.
4. **`repair()` must be idempotent and return a dict of counts**; F10 sums its
   values. Different keys are fine.
5. **No FIFO/priority guarantee.** Any claim order is accepted.
6. **T1 is part of `test_integrity`.** A candidate that breaks a public test
   without tampering also fails that P0 gate; this is intentional (a green
   public suite is a non-negotiable regression signal), but it means
   `test_integrity` can fail for a functional reason.
7. **Baseline scores 38/100** (maintainability 10, public tests 10, plus
   F1/F2/F3/F6-equivalents) yet fails 6 P0 gates -- the fixture is a plausible
   partial implementation, not a stub.
8. **C5 assumes the machine can run 3-4 Python processes concurrently** and
   that 199 jobs x 15 ms of simulated work keep the survivors busy while the
   victim's 1 s lease expires. On an extremely slow/loaded box the timing
   margins shrink; the oracle and all negatives were stable across repeated
   full validations (see §8).
9. **No-expiry implementations pay a 30 s timeout** in C5 before failing; this
   keeps grading under the 175 s budget (observed worst case ~40 s).
10. **Clock injection.** The oracle accepts `clock=` for tests, but the hidden
    suite never depends on it: all timing uses real time, so an implementation
    that does not support clock injection is not penalised.

## 8. Validation evidence (executed)

Environment: Debian, Python 3.13.5, SQLite 3.46.1, 6 CPUs, stdlib only, offline.

```
python3 validate.py        # full loop, run 1 -> PASS in 310.8s
python3 validate.py        # full loop, run 2 -> PASS in 311.1s
```

Full loop = fixture integrity + baseline + oracle x3 + 7 negatives
(+2 repeats for the race-driven neg02/neg06) + determinism comparison.
`validation_evidence/validation_report_run{1,2}.json` are the raw reports; the
two verdict signatures were compared programmatically and are **identical**.

| Target | Verdict | Total | Failing P0 gates |
| --- | --- | --- | --- |
| pristine baseline | FAIL | 38.0 | no_duplicate_ownership, no_lost_completion, bounded_retries, crash_recovery, corrupted_state_safe, idempotent_completion |
| oracle (3 runs) | PASS | 100.0 | none (all runs identical) |
| neg01_inmemory | FAIL | 53.0 | non_memory_storage, persistence_restart (+ secondary) |
| neg02_nonatomic_claim (2 runs) | FAIL | 84.0 | no_duplicate_ownership, no_lost_completion |
| neg03_no_lease_expiry | FAIL | 60.0 | crash_recovery, bounded_retries (+ secondary) |
| neg04_nonidempotent_complete | FAIL | 89.0 | idempotent_completion, no_lost_completion |
| neg05_unbounded_retries | FAIL | 79.0 | bounded_retries, test_integrity |
| neg06_corrupt_state_crash (2 runs) | FAIL | 96.0 | corrupted_state_safe |
| neg07_test_deletion | FAIL | 100.0 | test_integrity |

Additional edge cases exercised manually:

* `--workspace` missing -> `INVALID`, JSON still written, exit 1.
* syntax-broken `coordinator/core.py` -> `FAIL` 5.0, no grader crash.
* `import socket` added to a correct oracle -> `FAIL` 100.0 with only
  `no_network_thirdparty` failing (cheat detected without collateral damage).
* candidate adds `tests/__init__.py` -> still `PASS` 100.0 (T2 still detects the
  added test module).
* `workspace_untouched: true` in every run.

Raw commands used for this task:

```
git -C tasks/t3_coordinator/repo init && git add -A && git commit
git -C tasks/t3_coordinator/repo archive HEAD | tar -x -C graders/t3_coordinator/baseline_repo
python3 graders/t3_coordinator/grade.py --workspace graders/t3_coordinator/baseline_repo --json-out /tmp/base.json
python3 graders/t3_coordinator/oracle/apply_oracle.sh /tmp/ws
python3 graders/t3_coordinator/grade.py --workspace /tmp/ws --json-out /tmp/oracle.json
python3 graders/t3_coordinator/validate.py
python3 graders/t3_coordinator/validate.py            # second full loop
```

## 9. Re-validating after a change

```
python3 validate.py --quick     # baseline + oracle once + negatives once (~3 min)
python3 validate.py             # full loop with determinism repeats (~5 min)
```

If you change the fixture, re-export `baseline_repo/` and update
`manifest.json:start_commit`; `validate.py` fails if they disagree with the git
HEAD or if `baseline_repo/` differs from the tracked tree.
