# Grader notes — t2_replay_perf

Task: a correct-but-slow Python 3 market-event replay engine must be made
substantially faster (>= 3x on a large workload) while keeping the output
document **byte-identical** to the pristine engine.

* Task repo: `tasks/t2_replay_perf/repo` (initial commit
  `390b675f7c3bdbd8717ba7b43b28c725262f4687`)
* Pristine baseline used by the grader:
  `graders/t2_replay_perf/baseline_repo/` — an exact `git archive HEAD`
  export of that commit. It is bundled so a candidate workspace cannot
  tamper with the reference implementation.
* Grader: `graders/t2_replay_perf/grade.py`
* Controls + validation: `oracle/`, `negative/`, `validate.py`

## 1. Design

```
grade.py            grading harness (stdlib only, single entry point)
hidden_cases.py     independent seeded workload generator (never imported
                    from the candidate; tampering with replay/events.py
                    cannot influence the hidden inputs)
baseline_repo/      pristine git-archive export of the start commit
oracle/             reference optimised book.py + engine.py + apply script
negative/           7 intentionally wrong controls (each with meta.json)
validate.py         runs baseline + oracle + all negatives and writes
                    validation_report.json
manifest.json       task identity, hashes, hidden-case inventory, weights
```

### Execution model

1. The candidate workspace is **copied** into a private temp directory
   (`.git`, `__pycache__`, caches excluded). The baseline is copied too.
   Nothing in the candidate workspace is ever mutated.
2. Each replay is a fresh `python3 -m replay.cli --events ... --out ...`
   subprocess in the copied workspace.
3. Hidden inputs are written by `hidden_cases.py` into the temp directory
   and passed to both versions as identical files.
4. Timing: one warm-up run per version/profile (also used for peak-RSS
   measurement through a `RUSAGE_CHILDREN` wrapper process), then N
   measured repeats in which baseline and candidate strictly alternate
   (`baseline, candidate` per profile) so drift/thermal noise affects both
   equally. Medians are used.
5. Peak RSS is measured with `resource.getrusage(RUSAGE_CHILDREN)` inside a
   one-shot wrapper process, which isolates the child's `ru_maxrss` per run.

### Hidden workloads (`hidden_cases.py`)

| id | seed | events | purpose |
| --- | --- | --- | --- |
| h_small | 9001001 | 700 | parity/contract on a small log |
| h_large | 9001002 | 32000 | primary speedup (baseline ~7.5–9 s) |
| h_adversarial | 9001003 | 10000 | deep book (≈1200 live orders), one-tick spread, 70% shared timestamps, 12% duplicate order ids, quantities 1/3/7/13/1000 (heavy partial fills), cancels of dead orders |
| h_fresh | 9001004 | 900 | unseen-input parity |

Additional h_fresh variant: the same events with two extra irrelevant JSON
fields (`source`, `note`). The loader must ignore unknown fields, so a
correct engine produces the same document; input-hash-keyed caching or
"recognise the file" tricks break here.

### Parity

Per profile the baseline output document and the candidate output document
are parsed, deep-compared (`document_diff` reports the first differing
paths with truncated values), and also byte-compared. Semantic parity is
P0; byte-identity is reported separately as a P1 case (the contract asks for
byte-identical canonical JSON, but a semantically equal document with
different whitespace is not treated as a P0 cheat).

### Scoring (out of 100)

* correctness 40
  * 22 parity across the four hidden profiles, weighted
    h_large 0.40 / h_adversarial 0.30 / h_small 0.15 / h_fresh 0.15
  * 6 public test suite (all pass, none skipped)
  * 6 pristine test suite run against candidate code
  * 3 determinism (every repeat on the same input byte-identical)
  * 3 output contract (schema, 16-hex digest, event/trade counts,
    lifecycle consistency)
* performance 35
  * 25 × clamp((speedup_large − 1.2) / 1.8) — full at 3x, zero at ≤1.2x
  * 10 × clamp((speedup_adv − 1.2) / 1.3) — full at 2.5x
  * the 1.2x dead band keeps run-to-run noise from earning points
  * performance is forced to 0 if parity, contract, skipped-work,
    constant-output, cache-artefact or fake-speed checks fail
* memory 15 — `15 × clamp((1.5 − rss_candidate/rss_baseline) / 0.7)` on
  h_large (`<= 0.8x` → full, `>= 1.5x` → zero)
* maintainability 10 — baseline test files identical (2), suite not
  reduced (2), no debug prints/debugger hooks in `replay/` (2),
  ≤1500 changed `.py` lines (2), no forbidden imports (1), fixtures
  untouched (1)

`PASS` requires **all P0 gates pass and total ≥ 80**. `status` is `INVALID`
only when the reference side is unusable (missing baseline, baseline crash
or non-deterministic baseline), which is a harness problem, not a candidate
failure. Exit code: 0 for PASS, 1 otherwise; the JSON report is always
written.

### P0 gates

| gate | catches |
| --- | --- |
| `test_integrity` | deleted/modified public tests (hashes vs baseline) |
| `no_third_party_or_network` | numpy/pandas/orjson…, socket/urllib/http…, `time.sleep`, profiler hooks, `subprocess`, `eval/exec/__import__`, pickle/shelve/sqlite caches, `sitecustomize.py`/`conftest.py`/`.pth` startup hooks |
| `public_tests` | failing or skipped tests |
| `pristine_tests` | candidate code failing the pristine test suite |
| `baseline_sane` | baseline cannot replay the hidden workloads (→ INVALID) |
| `constant_output` | returning the same document for different inputs |
| `parity` | any value differing from the pristine engine on hidden inputs |
| `determinism` | output varying between runs (hash-seed/`id()`/random tie-breaks) |
| `no_cache_artifacts` | any non-`__pycache__` file created in the workspace during grading |
| `fake_speed` | median faster than `max(25 ms, 1% of baseline)`; first run ≥2.5x slower than later runs (cache-after-first-run signature) |

### Explicit anti-cheat reasoning

* **Hardcoded outputs** → hidden seeds/sizes are unknown and h_fresh is new;
  plus the perturbed h_fresh variant.
* **Caching keyed by input hash** → caches that persist between runs are
  detected by the workspace file-state diff and the timing floor /
  first-vs-later ratio. The fresh-copy model means a cache cannot survive
  between grading runs.
* **Skipping events / reduced workload** → `event_count` and
  `audit.entries` must equal the input event count, `traded_notional` /
  `traded_qty` are recomputed from the trade list, and parity would fail.
* **Weakening tests** → SHA-256 of every baseline test file (including
  `tests/golden/small.json`); plus the suite is re-run from pristine copies
  against candidate code.
* **Monkeypatching the clock** → `time.sleep`, `sys.setprofile`,
  `sys.settrace`, `set_trace`, `breakpoint` are grepped; timing is measured
  externally by the grader process (`perf_counter` around a subprocess).
* **Changing the generator** → the grader generates its own inputs, so
  `replay/events.py` generation edits cannot shrink the measured workload.

## 2. Controls

### Oracle (`oracle/`)

`apply_oracle.sh TARGET` replaces two files (kept as full replacements, not
a diff, to stay readable in review):

* `replay/book.py` — sorted price lists (`bisect`) so top-of-book is O(1),
  `deque` price levels for O(1) FIFO, id→order index, incremental
  `checksum`/`open_qty` counters.
* `replay/engine.py` — live-order index for cancels, incremental
  per-symbol long open interest, per-symbol traded quantity, and no
  per-event state history (max/mean open interest folded into accumulators).

The oracle changes no semantics: public tests pass unchanged, visible
fixtures and hidden workloads are byte-identical to the baseline.

Measured on the reference machine (6 vCPU, Debian, Python 3.13):

| profile | baseline median | oracle median | speedup |
| --- | --- | --- | --- |
| h_large (32k events) | ≈8.2 s | ≈1.6 s | ≈4.7–5.7x |
| h_adversarial (10k) | ≈2.24 s | ≈0.63 s | ≈3.5x |
| h_small (700) | ≈0.2 s | ≈0.2 s | ~1x (startup bound) |
| peak RSS h_large | ≈66 MB | ≈56 MB | 0.85x |

Oracle total: 97–99 / 100, `PASS`.

### Negative controls (`negative/`)

Each directory contains `apply.sh` (applied to a pristine export) and
`meta.json` with the expected failing gate ids.

| id | defect | expected failing gates | observed |
| --- | --- | --- | --- |
| neg01_output_value_changed | oracle + `FEE_BPS 2→20` | parity | parity, public_tests, pristine_tests |
| neg02_tests_weakened | deletes `tests/test_cli.py`, skips `AccountingTests` | test_integrity, public_tests | same |
| neg03_hardcoded_output | CLI always writes the committed golden doc | parity, constant_output | same (+ tests) |
| neg04_reduced_workload | loader keeps every second event | parity | parity (+ tests) |
| neg05_nondeterministic_ties | random tie-break inside a price level | determinism | determinism, parity, tests |
| neg06_hash_cache | result cache keyed by sha256(input) in `.replay_cache/` | no_cache_artifacts, fake_speed | same (performance forced to 0) |
| neg07_network_import | oracle + `import socket` | no_third_party_or_network | same |

Additional observed negative details:

* neg03 gets ~0.05 s and scores 28 → shows hardcoding is not rewarded.
* neg06 passes parity and determinism, is measured at ~33x, but its
  performance score is forced to 0 and status is FAIL — the strongest
  "fake speed" control.
* neg02 would otherwise total 85.47 (it is genuinely optimised) but fails P0
  gates, proving gates dominate the numeric score.

### Baseline

Grading the pristine baseline yields `FAIL`, total ≈ 57–62 (correctness 40,
performance ~0–1, memory ~10.7, maintainability ~10) with no P0 gate
failing. This confirms the benchmark cannot be passed without real
optimisation.

## 3. How validation was run

```bash
cd graders/t2_replay_perf
python3 validate.py                     # full run (all controls)
python3 validate.py --only oracle       # single control
python3 validate.py --only baseline,neg06 --negative-repeats 1
```

`validate.py`

1. exports the task repo with `git archive HEAD` (never touches it),
2. grades the pristine baseline (expect FAIL, ~1x speedup),
3. applies the oracle to a pristine export and grades it `--oracle-runs`
   times (default 3), requiring PASS, total ≥ 80, speedup ≥ 3x and an
   identical verdict across runs,
4. applies each negative and requires FAIL with its expected gates among
   the failed gate ids (and 0 performance points where declared),
5. writes `validation_report.json` (and per-run reports in
   `validation_reports/`) and exits non-zero on any unmet expectation.

## 4. Known limitations

* **Wall-clock noise.** Speedups vary by ±15–20% on a busy machine (the
  pristine baseline against itself has measured 0.88x–1.25x). The grader
  mitigates this with warmups, interleaved runs, medians and a 1.2x noise
  dead band, but a heavily loaded host can still move the speedup component
  by several points. It cannot flip a genuine 3x+ candidate below PASS
  because correctness+memory+maintainability already contribute ~62–65.
* **`subprocess` is forbidden in `replay/`.** A candidate that parallelises
  the replay by spawning workers would be rejected by the policy gate even
  though it is stdlib-only. This is deliberate (determinism and wall-clock
  gaming), and documented here; the intended solution is algorithmic.
  `multiprocessing`/`concurrent.futures` are *not* matched by the scanner,
  so process-pool solutions are allowed if the output stays identical and
  deterministic.
* **Memory score is coarse.** The baseline keeps a per-event state history
  (~10 MB at 32k events); an incremental solution gets ~0.85x RSS. A
  candidate that keeps the history (still correct) loses only ~1–2 points,
  since the ratio stays below 1.5x.
* **Byte-identity is a P1 case, not P0.** A semantically identical document
  with different whitespace would fail `c_byte_identical` (−0 points) but
  not the parity gate. The visible tests pin the canonical serialization, so
  this is unlikely in practice.
* **Baseline assumption.** The bundled `baseline_repo/` is trusted. A
  candidate with write access to `graders/` could tamper with it; the timed
  experiment must only expose `tasks/t2_replay_perf/repo` to the agent.
* **Single machine, single Python.** Timings are only comparable on the
  host that runs the grading; do not compare absolute millisecond values
  across machines.
* The grader copies the candidate tree, so a candidate workspace with very
  large untracked artefacts will slow the copy step (not the score).
