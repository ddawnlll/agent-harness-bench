# Reproduce the publication

The repository publishes **derivatives only**. The sealed run — raw logs, frozen
workspaces, patches, unsanitized report — is preserved outside the repository
and is not redistributed. This document describes how the committed derivatives
were produced, how to re-derive and check them, and which steps are present in
this checkout.

## Prerequisites

- **Python 3.13, standard library only.** No third-party packages are required
  for derivation, charts or checks.
- Optional: Node.js and Playwright for site QA; a browser to inspect figures.
- The sealed run directory, if re-deriving data. Without it you can still
  regenerate the charts from the committed dataset and verify hashes.

## 1. Rebuild the sanitized dataset

```bash
python3 tools/derive_data.py --source <sealed-run> --out results
```

`<sealed-run>` contains `SHA256SUMS`, `summary_raw.json`, `telemetry/`,
`runs/<task>/<agent>/` and `verdict.json`. The script verifies every hash and
aborts on mismatch, recomputes tokens, cost, CPU, memory, elapsed time and
aggregates from primary records, re-checks them against the controller summary,
redacts host identifiers, and writes `results/publication-data.json`,
`results/telemetry/*.jsonl` and `results/TRANSFORM.json`.

Expected output for the recorded run:

```
data: 9 trials, seal ok (755 files), discrepancies=0, verdict=JCODE
```

The command was re-run against the sealed evidence while preparing this
documentation and reproduced `publication-data.json` byte-for-byte (SHA-256
`76a6383123425507e1a25a8e2a257917fac1311804b232c6664af9fdb32b9d6b`).

## 2. Rebuild the figures

```bash
python3 tools/make_charts.py            # write assets/*.svg
python3 tools/make_charts.py --check    # regenerate in memory and compare
```

`--check` prints `CHART CHECK OK: 12 SVGs byte-identical` and exits 0 when the
committed light and dark figures match the committed data; otherwise it lists
the differences and exits non-zero. Six figures × two themes.

A dependency-free structural check without the repository's SVG checker:

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

Renders the static site from `docs/`, `assets/` and
`results/publication-data.json` into `site/`, deterministically (no clocks,
network or environment-dependent output).

**Status:** not included in this documentation-only checkout; listed for
reproducibility, not run here.

## 4. Publication checks

```bash
python3 tools/check_svg.py           # SVG well-formedness and hygiene
python3 tools/check_contrast.py      # WCAG AA contrast for every token pair
python3 tools/check_publish.py       # secret/path/PII scan over the tree
python3 tools/verify_publication.py  # end-to-end gate
python3 tools/make_charts.py --check
```

`check_contrast.py` and `check_publish.py` are inherited from the author's
previous benchmark ([`ddawnlll/goal-extension-benchmark`](https://github.com/ddawnlll/goal-extension-benchmark),
MIT); `verify_publication.py` is the aggregate gate.

**Status:** only `make_charts.py --check` is present here and was run.
`check_svg.py`, `check_contrast.py`, `check_publish.py` and
`verify_publication.py` belong to the full repository but not this
documentation-only checkout, so their passing status is not asserted. The checks
that can be run here are documented in [SECURITY.md](SECURITY.md).

## 5. Browser QA (optional)

```bash
npx playwright install chromium
npx playwright test tests/site.spec.ts
```

The spec loads `site/index.html` from disk, asserts no request leaves the local
filesystem, exercises both colour schemes, checks every figure has an
`alt`/`aria-label`, and captures light/dark screenshots. Optional; not run here.

## 6. Transformation and hashes

`results/TRANSFORM.json` records the source run id, seal and per-file hashes.

| Item | SHA-256 |
|---|---|
| Source seal (`SHA256SUMS`, 755 files) | `05306b0c91c7a3f7ba9d26786a66a2aaec9dd4da23f9bc4ec8c4fa75c740d4fa` |
| `results/publication-data.json` | `76a6383123425507e1a25a8e2a257917fac1311804b232c6664af9fdb32b9d6b` |
| `results/telemetry/api_usage.jsonl` | `7bbda2f2bd63c470e31ccd8217cfe82935a14e562d1a3175f6fc7c62bbc73570` |
| `results/telemetry/resource_samples.jsonl` | `b82c5364ff3a6e9a1b455771c16aed75ece966379e0927a8898e9cc84b0435cd` |
| `results/telemetry/events.jsonl` | `c3ae484bb3b289b23e0b822a0c0875d1b023810794a6b342f330f533eac8539e` |
| Sealed `summary_raw.json` | `870017e13f9a11f15e8d7b47f55a00c6838ea8ff50c3297fd5760bb3f8e8e06a` |
| Sealed `verdict.json` | `3bd83ed03945b33cf8627321bc954b533064e19290eb00b44a3be1998572f490` |

Transformation: read-only copy from the sealed run → verify seal → recompute
metrics → redact → deterministic JSON/JSONL. No original file is modified and no
metric is estimated.

## 7. What cannot be reproduced

- **The agent runs.** Model sampling is stochastic and the provider external;
  only the data the nine trials produced is preserved.
- **The sealed evidence** (not redistributed). Without it `derive_data.py`
  cannot run, but the published data and charts can still be checked against
  their hashes and regenerated from the committed dataset.
- **JCode's tool trace**, which was never emitted.
