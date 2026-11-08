# Reproduce the publication

The repository publishes **derivatives only**. The sealed run is preserved
outside the repository and is not redistributed. This document covers how the
derivatives were produced, how to re-derive and check them, and which steps are
present here.

## Prerequisites

- **Python 3.13, standard library only** for derivation, charts, site and
  checks.
- Optional Node.js with Playwright and Chromium for visual QA.
- The sealed run directory for re-derivation; without it the charts, site and
  hashes can still be checked.
- The repository test suite runs with
  `python3 -m unittest discover -s tests -p "test_*.py" -v`.

## 1. Rebuild the sanitized dataset

```bash
python3 tools/derive_data.py --source <sealed-run> --out results
```

`<sealed-run>` contains `SHA256SUMS`, `summary_raw.json`, `telemetry/`,
`runs/<task>/<agent>/` and `verdict.json`. The script verifies every hash,
aborts on mismatch, recomputes metrics from primary records, re-checks them
against the controller summary, redacts host identifiers, and writes
`results/publication-data.json`, `results/telemetry/*.jsonl` and
`results/TRANSFORM.json`.

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
committed figures match the committed data; otherwise it lists differences and
exits non-zero. Six figures × two themes.

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
`results/publication-data.json` into `site/`, deterministically. Confirm it is
current with `python3 tools/build_site.py --check`, which reports
`SITE CHECK OK: 15 files byte-identical`.

## 4. Publication checks

```bash
python3 tools/make_charts.py --check   # CHART CHECK OK: 12 SVGs byte-identical
python3 tools/check_svg.py             # SVG structure, light/dark parity
python3 tools/check_contrast.py        # WCAG AA for every enforced pair
python3 tools/build_site.py --check    # SITE CHECK OK: 15 files byte-identical
python3 tools/check_publish.py         # secrets, identifiers, links, report seal
python3 tools/verify_publication.py    # end-to-end gate -> publication-audit.json
```

`check_contrast.py` and `check_publish.py` are inherited from the author's
previous benchmark ([`ddawnlll/goal-extension-benchmark`](https://github.com/ddawnlll/goal-extension-benchmark),
MIT); `verify_publication.py` aggregates 19 checks into
`publication-audit.json`. On the release commit all six commands exit 0, and the
audit reports `PUBLICATION AUDIT: PASS (19 checks)`.

## 5. Browser QA (optional)

```bash
npx playwright install chromium
node tests/visual_qa.js            # checks + tests/qa-report.json
node tests/visual_qa.js --shots    # also write tests/screenshots/*.png
```

The script loads `site/index.html` from disk at five viewports (1440, 1280, 768,
390, 360 px) in light and dark, exercises the trial filters, checks figure
labels and overflow, and records console errors, page errors and failed
requests. The committed `tests/qa-report.json` shows 0 errors, 0 failed
requests, no overflow and working filter counts (3 / 1 / 9 trials).

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

Transformation: read-only copy → verify seal → recompute metrics → redact →
deterministic JSON/JSONL. No original file is modified and no metric is
estimated.

## 7. What cannot be reproduced

- **The agent runs.** Sampling is stochastic and the provider external; only the
  data the nine trials produced is preserved.
- **The sealed evidence.** Without it `derive_data.py` cannot run, but the data
  and charts can still be checked against their hashes.
- **JCode's tool trace**, never emitted.
