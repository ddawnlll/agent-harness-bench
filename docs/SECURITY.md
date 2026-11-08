# Security and publication policy

This repository is a record of a benchmark. It is not a service, holds no
credentials and does not redistribute agent source trees or raw run artifacts.
It states what was scanned, what was removed, how to report a leak, and what the
release gate can certify from this checkout.

## 1. Publication model

Only **sanitized derivatives** are published: `results/publication-data.json`
(recomputed metrics and grades), `results/telemetry/*.jsonl` (measurements with
identifying fields removed), `results/TRANSFORM.json` (provenance, seal,
hashes), `assets/*.svg` (deterministic figures), the sanitized single-file report
under `reports/` with its seal in `reports/SEAL.json`, the static site under
`site/`, and `docs/`.

The sealed run — frozen workspaces, patches, raw logs and the unsanitized
report — is preserved outside the repository. It is not redistributed because it
contains agent code, full command traces and host-specific paths. Nothing here
is copied from it except sanitized, recomputed values.

## 2. What was scanned

The derivation and review scanned for provider API keys, personal access
tokens, GitHub tokens and authorization headers; absolute home paths; the
operator's user name and real hostname; IPv4 addresses; cgroup paths and systemd
unit names; session and request identifiers; compressed or embedded HTML
payloads (the sealed report is excluded); and patches, diffs and logs, which are
not published raw.

## 3. Redaction rules

`tools/derive_data.py` substitutes home paths, the real hostname, cgroup paths,
IPv4 addresses, key prefixes, GitHub token shapes and authorization values before
writing any string, using generic placeholders (`<user>`, `bench-host`,
`<cgroup>`, `<redacted-ip>`, `<redacted-key>`, `<redacted-token>`).

Telemetry uses **field allowlists**, stronger than pattern matching: API records
drop request and session ids; resource samples drop I/O statistics and host-load
corner values; lifecycle events drop workspace paths, cgroup, unit, pid and relay
details. Request bodies, prompts, responses and authorization headers were never
persisted by the relay.

## 4. Deliberately not published

| Artifact | Reason |
|---|---|
| The sealed run directory | frozen agent workspaces, patches, logs, unsanitized report |
| The upstream credential | only a short fingerprint prefix ever appeared in the manifest |
| Raw request/response bodies | never recorded by design |
| Agent binaries | identified by SHA-256 and version only |
| Frozen task workspaces | reproducible from pinned commits without shipping agent output |
| The unsanitized HTML report | embeds the above; replaced by the sanitized dataset |

Binaries are identified by SHA-256 (`1eecc82d…` Pi-Bolt, `059d05f0…` JCode,
`377d315e…` Hax) and version, so provenance is checkable without redistribution.

## 5. Verification performed for this release

| Check | Method | Result |
|---|---|---|
| Evidence seal | sealed `SHA256SUMS` verified during re-derivation | 755 files, 0 mismatches |
| Reconciliation | tokens, list price, elapsed, CPU source | `OK`, 0 discrepancies |
| Deterministic derivation | re-run against sealed evidence, byte comparison | dataset reproduced exactly |
| Deterministic charts | `python3 tools/make_charts.py --check` | `CHART CHECK OK: 12 SVGs byte-identical` |
| SVG structure | `python3 tools/check_svg.py` | 12 files, 6 light/dark pairs, parity verified |
| Contrast | `python3 tools/check_contrast.py` | all enforced pairs meet WCAG AA |
| Site currency | `python3 tools/build_site.py --check` | 15 files byte-identical |
| Release scan | `python3 tools/check_publish.py` | 269 text files, 0 secret/identifier findings, links and report seal verified |
| End-to-end gate | `python3 tools/verify_publication.py` | `PUBLICATION AUDIT: PASS (19 checks)` |
| Repository tests | `python3 -m unittest discover -s tests -p "test_*.py"` | 5 tests, OK |
| Redaction record | `TRANSFORM.json` lists applied redactions | present |

**Release gate status: PASS on this commit**, based on the checks above. The
aggregate gate writes `publication-audit.json` with 19 passing checks, including
that no sealed run directory is published, that the result and report seals
verify, and that README figures and links resolve. Re-run the gate after any
change; a failure is a release blocker, not a warning.

## 6. Residual risks

- **Model-generated text.** Published telemetry holds counts, not messages. The
  sanitized report contains normalized data and grades, not raw model output; the
  sealed unsanitized report outside the repository does and is untrusted.
- **Repository-relative paths and task names** remain; they are not personal
  data.
- **Finite patterns.** Scrubbing cannot prove every identifier is gone;
  allowlists reduce the surface, not to zero.
- **Host fingerprinting.** CPU model, kernel, memory and core count describe a
  host class, not a specific machine.
- **Loopback relay.** Any local process could use it during a trial; a run-time
  risk, not a publication risk.

## 7. Reporting a leak

Open an issue describing **where** the problem is, without pasting the suspected
secret, personal datum or path. If a credential is involved, do not reproduce
it; the maintainer will remove the content and rewrite history if necessary.
Rotate a live key first, report second.
