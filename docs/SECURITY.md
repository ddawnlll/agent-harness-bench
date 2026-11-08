# Security and publication policy

This repository is a record of a benchmark. It is not a service, it holds no
credentials, and it does not redistribute agent source trees or raw run
artifacts. This document states what was scanned, what was removed, what is
deliberately absent, how to report a leak, and what the release gate can and
cannot certify from this checkout.

## 1. Publication model

Only **sanitized derivatives** are published:

- `results/publication-data.json` — recomputed metrics and grades;
- `results/telemetry/*.jsonl` — measurements, with identifying fields removed;
- `results/TRANSFORM.json` — provenance, seal and hashes;
- `assets/*.svg` — deterministic figures generated from the dataset;
- `docs/` — this documentation.

The original sealed run (frozen workspaces, patches, raw logs, unsanitized
single-file HTML report) is preserved outside the repository. It is **not**
redistributed, because it contains agent-generated code, full command traces and
host-specific paths. Nothing in this repository is copied from it except
sanitized, recomputed values.

## 2. What was scanned

The derivation and review pipeline scanned the published material and the source
records for:

- credential-shaped strings: provider API keys, personal access tokens, GitHub
  tokens, and authorization headers;
- absolute home paths for Linux and macOS;
- the operator's user name and the host's real hostname;
- IPv4 addresses;
- cgroup paths and systemd unit names;
- session identifiers and provider request identifiers;
- compressed or embedded HTML payloads (the sealed report is a single HTML file;
  it is excluded rather than shipped);
- patches, diffs and logs, which were not published in raw form.

## 3. Redaction rules

`tools/derive_data.py` applies these substitutions on every string it writes:

| Input pattern | Published as |
|---|---|
| Linux home path | `/home/<user>` |
| macOS home path | `/Users/<user>` |
| real hostname | `bench-host` |
| cgroup path | `<cgroup>` |
| IPv4 address | `<redacted-ip>` |
| cloud/provider key prefix | `<redacted-key>` |
| GitHub token shapes | `<redacted-token>` |
| authorization header value | redacted placeholder |

Telemetry is additionally published through **field allowlists**, which is a
stronger control than pattern matching:

- API records drop provider request ids and session ids and keep only task,
  agent, timestamps, model/effort fields, status, durations, token counts, cost
  and usage provenance;
- resource samples drop I/O device statistics and host-load corner values, and
  keep cgroup counters, memory, pids, pressure and steal;
- lifecycle events drop workspace paths, cgroup, unit, pid and relay details,
  and keep event type, task, agent, timestamps and non-identifying detail.

Request bodies, prompts, model responses and authorization headers were never
persisted by the relay, so they cannot leak from the published data.

## 4. Deliberately not published

| Artifact | Reason |
|---|---|
| The sealed run directory | frozen agent workspaces, patches, full logs and the unsanitized report |
| The upstream credential | only a short fingerprint prefix ever appeared in the run manifest |
| Raw request/response bodies | never recorded by design |
| Agent binaries | pinned by SHA-256 and version only; not redistributed |
| Frozen task workspaces | reproducible from pinned commits and the public patches' descriptions, without shipping gigabytes of agent output |
| The unsanitized HTML report | embeds the above; the sanitized dataset replaces it |

The three agent binaries are identified in the dataset by SHA-256
(`1eecc82d…` Pi-Bolt, `059d05f0…` JCode, `377d315e…` Hax) and version strings.
The hashes let a reader confirm which binaries were measured without shipping
them.

## 5. Verification performed for this release

What can be checked directly in this checkout:

| Check | Method | Result |
|---|---|---|
| Evidence seal | `derive_data.py` verifies `SHA256SUMS` while re-deriving | 755 files checked, 0 mismatches |
| Reconciliation | token totals, list price, elapsed, CPU source | `status: OK`, 0 discrepancies |
| Deterministic derivation | re-run against the sealed run and compared bytes | `publication-data.json` reproduced byte-for-byte |
| Deterministic charts | `python3 tools/make_charts.py --check` | `CHART CHECK OK: 12 SVGs byte-identical` |
| SVG well-formedness | stdlib XML parse of all 12 assets | all parse |
| Documentation scan | grep for home paths, user name, hostname fragment, key shapes and authorization headers | no credential or path findings; see the release report |
| Redaction presence | `TRANSFORM.json` lists the applied redactions | present |

**Release gate status.** The checks above pass. The repository's full publication
gate (`tools/check_publish.py` and `tools/verify_publication.py`) is **not
included in this documentation-only checkout**, so this document does not claim
that those scripts pass. It claims only what the table shows. A full release
should run the complete gate in the assembled repository before tagging; if any
check there fails, the release gate is not passed.

## 6. Residual risks

- **Model-generated text.** Published telemetry contains counts, not messages,
  so this risk is low here. The sealed report outside the repository does contain
  model output and agent-generated code; treat it as untrusted.
- **Path names.** Redaction replaces absolute home paths, but generic
  repository-relative paths and task names remain. They are not personal data.
- **The redaction patterns are finite.** Pattern-based scrubbing cannot prove the
  absence of every possible identifier. Field allowlists reduce the surface
  substantially but not to zero.
- **Host fingerprinting.** CPU model, kernel version, memory size and core count
  are published because they are part of the measured environment. Together they
  describe a host class, not a specific machine.
- **Loopback relay.** During a run, any local process could use the relay while a
  trial was active. This is a run-time risk, not a publication risk.

## 7. Reporting a leak

Open an issue in the repository describing **where** the problem is, without
pasting the suspected secret, personal datum or path. If a credential is
involved, do not reproduce it in the issue; the maintainer will remove the
content and rewrite history if necessary. For anything that looks like a live key,
rotate it first and report second.
