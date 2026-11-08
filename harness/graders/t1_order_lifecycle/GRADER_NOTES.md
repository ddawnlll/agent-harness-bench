# Task 1 grader notes

## Design
- The candidate workspace is copied to scratch and never mutated.
- Hidden acceptance tests live outside every agent-readable tree and import only
  the documented public API (`v9.Engine`, `v9.RecordingBroker`).
- 16 acceptance cases are grouped into nine P0 gates. Correctness (50),
  safety (25), regression (15) and maintainability (10) are scored separately;
  a P0 gate failure prevents PASS regardless of the numeric total.
- Integrity checks: existing public test files must be byte-identical to the
  baseline export, no skip decorators, no network imports in changed modules,
  no fixture hardcoding patterns, change size bounded.
- The suite runs twice; differing outcomes mark the grade INVALID.

## Controls (see validation_report.json)
- Seeded baseline: FAIL 22.03, gates G1-G7 fail. The intended defect exists.
- Oracle: PASS 100.0, stable across 3 repeats.
- Negative controls: 8 hand-broken variants, each fails exactly the intended
  gate (premature ACK, fill dedupe, restart promotion, stale callback,
  identity check, cancel race, deleted tests, fixture hardcoding).

## Known limitations
- Maintainability is partly heuristic (diff size, debug prints, regression
  tests); it cannot establish long-term maintainability from a 30-minute patch.
- Two repeat determinism runs are used at race time; heavier repetition is
  reserved for grader validation.
- The hidden suite does not inspect the audit format beyond required record
  types and digest stability; alternative audit designs are acceptable.
