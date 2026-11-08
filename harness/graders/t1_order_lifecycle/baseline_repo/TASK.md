# TASK 1 — Cross-module execution-safety defect (protective coverage)

## Context
`v9/` is a deterministic replay core for an execution service. It coordinates
order lifecycle, positions and native protective stops. It performs **no I/O and
no network access**; exchange behaviour arrives as events.

A production incident exposed a cross-module defect: positions can be reported
as protected when native protective coverage is not actually in force. The
symptom is intermittent and depends on the interleaving of partial fills,
delayed acknowledgements, cancellation races, stale callbacks and restarts.

## Required behaviour (acceptance contract)
A position is **protected** only when an `ACTIVE` protective stop covers exactly
the absolute open position quantity for that symbol.

1. Protective stop submitted to the venue starts in `PENDING_ACK`.
2. It becomes `ACTIVE` **only** after a `STOP_ACK` with `accepted: true`.
3. `STOP_ACK` rejected while a position is open must fail closed: record
   `RISK_UNPROTECTED`, set `halted = True`, and refuse new entries.
4. Fills are authoritative and idempotent:
   - distinct `fill_id`s accumulate (partial fills);
   - repeated delivery of the same `fill_id` is applied exactly once;
   - a fill may arrive before `ENTRY_ACK`; it must never be dropped.
5. After any position change, protective coverage must be reconciled to the
   absolute position quantity (resubmit/resize when required). A stale stop
   quantity is a failure. During reconciliation the position is not protected.
6. Cancellation races must never drop protection for an open position. If a
   cancel succeeds while a position is open, protection must remain (and be
   re-established if it was lost).
7. `STOP_CALLBACK` versions are monotonic. A stale/duplicate version must never
   downgrade or resurrect protection. A callback reporting the loss
   (`CANCELLED`, `REJECTED`, `FAILED`) of protection on an open position must
   fail closed (`RISK_PROTECTION_LOST`, `halted = True`).
8. Restart (`RESTART` event) restores state from the audit snapshot. A stop that
   was `PENDING_ACK` before restart stays `PENDING_ACK`; it must not be promoted
   to `ACTIVE` without a venue acknowledgement.
9. Entry acknowledgement identity: an `ENTRY_ACK` whose `native_id` does not
   match the order's native identity must not be treated as a working order
   (`IDENTITY_MISMATCH`); if exposure is open the engine halts
   (`RISK_IDENTITY_MISMATCH`).
10. New entries are refused whenever `halted` is true or any open position is
    not fully protected (`RISK_BLOCK_ENTRY`). Risk-reducing exits are allowed.
11. Audit records are append-only, deterministic, and contain the outcomes
    above (`PROTECTION_SUBMITTED`, `PROTECTION_ACTIVE` via `STOP_ACK`,
    `RISK_UNPROTECTED`, `RISK_PROTECTION_LOST`, `FILL_DUPLICATE_IGNORED`,
    `RISK_BLOCK_ENTRY`, `HALTED`). `Audit.digest()` must be stable across runs.

## Constraints
- Python 3 standard library only. No network access. No new services.
- Do not modify or delete tests in `tests/`. Do not hardcode scenario outputs.
- Keep the public API stable: `v9.Engine`, `v9.RecordingBroker`,
  `v9.parse_events`, `Engine.process`, `Engine.run`, `Engine.is_protected`,
  `Engine.unprotected_symbols`, `Engine.can_enter`, `Engine.audit.digest`.
- Existing public tests must keep passing:
  `python3 -m unittest discover -s tests -v`

## Deliverable
Fix the lifecycle so every invariant above holds under adversarial interleavings.
Add regression tests for the failures you fix. Keep changes narrow and auditable.
