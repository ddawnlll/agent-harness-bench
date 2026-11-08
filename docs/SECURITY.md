# Security and publication policy

This repository is a record of a benchmark. It is not a service, holds no
credentials, and does not redistribute agent source trees or raw run artifacts.
This document states what was scanned, what was removed, what is absent, how to
report a leak, and what the release gate can certify from this checkout.

## 1. Publication model

Only **sanitized derivatives** are published: `results/publication-data.json`
(recomputed metrics and grades), `results/telemetry/*.jsonl` (measurements with
identifying fields removed), `results/TRANSFORM.json` (provenance, seal,
hashes), `assets/*.svg` (deterministic figures) and `docs/`.

The sealed run — frozen workspaces, patches, raw logs and the unsanitized
single-file HTML report — is preserved outside the repository. It is not
redistributed because it contains agent-generated code, full command traces and
host-specific paths. Nothing here is copied from it except sanitized, recomputed
values.

## 2. What was scanned

The derivation and review scanned published material and source records for:
provider API keys, personal access tokens, GitHub tokens and authorization
headers; absolute Linux and macOS home paths; the operator's user name and the
real hostname; IPv4 addresses; cgroup paths and systemd unit names; session and
provider request identifiers; compressed or embedded HTML payloads (the sealed
report is excluded rather than shipped); and patches, diffs and logs, which are
not published raw.

## 3. Redaction rules

`tools/derive_data.py` substitutes home paths, the real hostname, cgroup paths,
IPv4 addresses, cloud/provider key prefixes, GitHub token shapes and
authorization values before writing any string. The placeholders are generic
(`<user>`, `bench-host`, `<cgroup>`, `<redacted-ip>`, `<redacted-key>`,
`<redacted-token>`).

Telemetry is published through **field allowlists**, a stronger control than
pattern matching:

- API records drop provider request ids and session ids, keeping task, agent,
  timestamps, model/effort fields, status, durations, tokens, cost and usage
  provenance;
- resource samples drop I/O device statistics and host-load corner values;
- lifecycle events drop workspace paths, cgroup, unit, pid and relay details.

Request bodies, prompts, model responses and authorization headers were never
persisted by the relay, so they cannot leak from the published data.

## 4. Deliberately not published

| Artifact | Reason |
|---|---|
| The sealed run directory | frozen agent workspaces, patches, logs, unsanitized report |
| The upstream credential | only a short fingerprint prefix ever appeared in the manifest |
| Raw request/response bodies | never recorded by design |
| Agent binaries | identified by SHA-256 and version only |
| Frozen task workspaces | reproducible from pinned commits without shipping agent output |
| The unsanitized HTML report | embeds the above; replaced by the sanitized dataset |

The three binaries are identified by SHA-256 (`1eecc82d…` Pi-Bolt, `059d05f0…`
JCode, `377d315e…` Hax) and version strings, so provenance is checkable without
redistribution.

## 5. Verification performed for this release

| Check | Method | Result |
|---|---|---|
| Evidence seal | sealed `SHA256SUMS` verified during re-derivation | 755 files, 0 mismatches |
| Reconciliation | tokens, list price, elapsed, CPU source | `OK`, 0 discrepancies |
| Deterministic derivation | re-run against sealed evidence, byte comparison | dataset reproduced exactly |
| Deterministic charts | `python3 tools/make_charts.py --check` | `CHART CHECK OK: 12 SVGs byte-identical` |
| SVG well-formedness | stdlib XML parse of all 12 assets | all parse |
| Documentation scan | grep for home paths, user name, hostname fragment, key shapes, authorization headers | no credential or path findings |
| Redaction record | `TRANSFORM.json` lists applied redactions | present |

**Release gate status.** These checks pass. The repository's full publication
gate (`tools/check_publish.py`, `tools/verify_publication.py`) is **not included
in this documentation-only checkout**, so this document does not claim those
scripts pass — only what the table shows. The assembled repository should run
the complete gate before tagging; if it fails there, the gate is not passed.

## 6. Residual risks

- **Model-generated text.** Published telemetry holds counts, not messages, so
  risk is low here. The sealed report outside the repository does contain model
  output and agent code; treat it as untrusted.
- **Repository-relative paths and task names** remain; they are not personal
  data.
- **Finite patterns.** Pattern scrubbing cannot prove every identifier is gone.
  Allowlists reduce the surface substantially, not to zero.
- **Host fingerprinting.** CPU model, kernel, memory and core count are published
  as part of the measured environment. Together they describe a host class, not
  a specific machine.
- **Loopback relay.** During a run, any local process could use it while a trial
  was active. That is a run-time risk, not a publication risk.

## 7. Reporting a leak

Open an issue describing **where** the problem is, without pasting the suspected
secret, personal datum or path. If a credential is involved, do not reproduce
it; the maintainer will remove the content and rewrite history if necessary.
Rotate a live key first, report second.
