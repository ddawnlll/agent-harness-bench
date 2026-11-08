# Reproduce the publication

The repository publishes **derivatives only**. The original sealed run — raw
logs, frozen workspaces, patches, unsanitized report — is preserved outside the
repository and is not redistributed. This document describes how the committed
derivatives were produced from that sealed run, how to re-derive and check them,
and which steps can be verified from the files in this checkout.

## Prerequisites

- **Python 3.13, standard library only.** No third-party packages are needed for
  the derivation, the charts or the checks described here.
- Optional: Node.js and Playwright for the site QA step, and a browser only if
  you want to inspect the figures.
- The sealed run directory, if you are re-deriving data. Without it you can still
  check the committed charts and data hashes.

## 1. Rebuild the sanitized dataset

```bash
python3 tools/derive_data.py --source <sealed-run> --out results
```

`<sealed-run>` is the run directory that contains `SHA256SUMS`, `summary_raw.json`,
`telemetry/`, `runs/<task>/<agent>/` and `verdict.json`. The script:

1. verifies every file in `SHA256SUMS` and aborts on any mismatch;
2. recomputes tokens, cost, CPU, memory, elapsed time and aggregates from
   primary records — it never trusts `verdict.json` for an aggregate;
3. re-checks token totals, list-price cost and elapsed time against the
   controller summary and records any discrepancy;
4. redacts host identifiers (see [SECURITY.md](SECURITY.md));
5. writes `results/publication-data.json`,
   `results/telemetry/{api_usage,resource_samples,events}.jsonl` and
   `results/TRANSFORM.json`.

Expected output for the recorded run:

```
data: 9 trials, seal ok (755 files), discrepancies=0, verdict=JCODE
```

The command was re-run against the sealed evidence during the preparation of
this documentation and reproduced `results/publication-data.json` byte-for-byte
(SHA-256 `76a6383123425507e1a25a8e2a257917fac1311804b232c6664af9fdb32b9d6b`).
Do not commit a regenerated copy over the published one unless the hashes also
match; `TRANSFORM.json` records the published hashes.

## 2. Rebuild the figures

```bash
python3 tools/make_charts.py            # write assets/*.svg
python3 tools/make_charts.py --check    # regenerate in memory, compare bytes
```

`--check` is the reproducibility claim. It prints
`CHART CHECK OK: 12 SVGs byte-identical` and exits 0 when the committed light
and dark figures match what the committed data produces; it exits non-zero and
lists the differing files otherwise. There are six figures (hero, race, safety,
timing, economics, resources) in two themes.

A dependency-free structural check is available if you do not have the
repository's SVG checker:

```bash
python3 - <<'PY'
import glob, xml.etree.ElementTree as ET
for p in sorted(glob.glob("assets/*.svg")):
    ET.parse(p)
print("all SVGs well-formed")
PY
```

## 3. Build the site

```bash
python3 tools/build_site.py
```

This renders the static documentation site from `docs/`, `assets/` and
`results/publication-data.json` into `site/`. It is deterministic: no clocks, no
network, no environment-dependent output.

**Status in this snapshot:** `tools/build_site.py` is not included in the
documentation-only checkout; the command is the repository's build entry point
and is listed here for reproducibility, but it was not run while preparing these
docs.

## 4. Publication checks

```bash
python3 tools/check_svg.py           # XML well-formedness and SVG hygiene
python3 tools/check_contrast.py      # WCAG AA contrast for every token pair
python3 tools/check_publish.py       # secret/path/PII scan over the whole tree
python3 tools/verify_publication.py  # end-to-end gate: data, charts, site, docs
python3 tools/make_charts.py --check
```

Exit code 0 means the check passed. `check_contrast.py` and `check_publish.py`
are inherited from the author's previous benchmark
([`ddawnlll/goal-extension-benchmark`](https://github.com/ddawnlll/goal-extension-benchmark),
MIT); `verify_publication.py` is the aggregate gate.

**Status in this snapshot:** only `make_charts.py --check` is present and was
run. `check_svg.py`, `check_contrast.py`, `check_publish.py` and
`verify_publication.py` are part of the full repository's tooling but are not in
this documentation-only checkout, so their passing status cannot be asserted
from these files. The equivalent checks that *can* be run here are documented in
[SECURITY.md](SECURITY.md).

## 5. Browser QA (optional)

If Playwright is installed and the site has been built:

```bash
npx playwright install chromium
npx playwright test tests/site.spec.ts
```

The QA spec loads `site/index.html` from disk, asserts that no request leaves
the local filesystem, exercises both colour schemes, checks that every figure has
an `alt`/`aria-label`, and captures light and dark screenshots for review.
**Status:** optional and not part of the data pipeline; the spec lives in the
full repository and was not executed here.

## 6. Transformation record and hashes

`results/TRANSFORM.json` is the provenance record. It lists the source run id,
the verified seal, and the SHA-256 of every file consumed from the sealed run:

| Item | SHA-256 |
|---|---|
| Source seal (`SHA256SUMS`, 755 files) | `05306b0c91c7a3f7ba9d26786a66a2aaec9dd4da23f9bc4ec8c4fa75c740d4fa` |
| `results/publication-data.json` | `76a6383123425507e1a25a8e2a257917fac1311804b232c6664af9fdb32b9d6b` |
| `results/telemetry/api_usage.jsonl` | `7bbda2f2bd63c470e31ccd8217cfe82935a14e562d1a3175f6fc7c62bbc73570` |
| `results/telemetry/resource_samples.jsonl` | `b82c5364ff3a6e9a1b455771c16aed75ece966379e0927a8898e9cc84b0435cd` |
| `results/telemetry/events.jsonl` | `c3ae484bb3b289b23e0b822a0c0875d1b023810794a6b342f330f533eac8539e` |
| Sealed `summary_raw.json` | `870017e13f9a11f15e8d7b47f55a00c6838ea8ff50c3297fd5760bb3f8e8e06a` |
| Sealed `verdict.json` | `3bd83ed03945b33cf8627321bc954b533064e19290eb00b44a3be1998572f490` |

The transformation is: read-only copy from the sealed run → verify seal →
recompute metrics → redact → deterministic JSON/JSONL. No original file is
modified, and no metric is estimated.

## 7. What cannot be reproduced

- **The agent runs themselves.** Model sampling is stochastic, the provider is
  an external service, and the exact nine trials cannot be replayed; only the
  data they produced is preserved.
- **The sealed evidence.** It stays outside the repository. Without it,
  `derive_data.py` cannot run, but the published dataset and charts can still be
  checked against their recorded hashes and regenerated from committed data.
- **JCode's tool-level trace.** It was never emitted, so it cannot be recovered.
