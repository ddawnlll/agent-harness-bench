# Publication summary — v0.1.0 research preview

## What was published

A complete, reproducible publication of the nine-trial coding-agent benchmark
(Pi-Bolt vs JCode vs Hax) on three engineering tasks, with:

- a sanitized, independently recomputed dataset (`results/publication-data.json`)
  and its provenance/transformation record (`results/TRANSFORM.json`);
- six handcrafted figures in light and dark themes, generated deterministically
  from that dataset (`assets/`, `tools/make_charts.py`);
- a self-contained static research site (`site/`, GitHub Pages);
- the sanitized single-file forensic report (`reports/`), with the original
  report hash and redaction record in `reports/SEAL.json`;
- methodology, fairness, results, limitations, security, attribution and
  reproduction documentation (`docs/`);
- automated chart, SVG, contrast, site, security and publication gates;
- Playwright visual QA evidence for five viewports and both themes.

## URLs

- Repository: https://github.com/ddawnlll/agent-harness-bench
- Research site (GitHub Pages): https://ddawnlll.github.io/agent-harness-bench/
- Forensic report (site): https://ddawnlll.github.io/agent-harness-bench/report.html
- Dataset: https://ddawnlll.github.io/agent-harness-bench/data/publication-data.json
- Release: https://github.com/ddawnlll/agent-harness-bench/releases/tag/v0.1.0

## Result

| Task | Pi-Bolt | JCode | Hax |
|---|---|---|---|
| T1 safety | FAIL 97.58 | PASS 98.00 | FAIL 97.58 |
| T2 performance | PASS 100 | PASS 100 | PASS 100 |
| T3 coordinator | PASS 100 | PASS 100 | PASS 100 |
| Verified | 2/3 | **3/3** | 2/3 |

Provisional verdict: **JCode**, confidence **LOW**. The discriminator is hidden
case `h07`: a stale protective-stop callback (version 0) must not downgrade an
ACTIVE protection established at version 1. JCode rejected the stale callback;
Pi-Bolt and Hax applied it and failed the critical gate.

## Source evidence

The sealed benchmark run is preserved outside the public repository. The public
dataset is derived by `tools/derive_data.py`, which verifies the original
SHA256SUMS (755 files), recomputes tokens, CPU, memory, scores and aggregates
from primary records, and reports **zero discrepancies** against the original
summary. Identifier redaction is recorded in `results/TRANSFORM.json`; the
sanitized forensic report keeps its original hash next to the derivative hash.

## Verification commands and outcomes

All run on the published tree and in CI (`verify` workflow):

| Command | Outcome |
|---|---|
| `python3 tools/make_charts.py --check` | 12 SVGs byte-identical |
| `python3 tools/check_svg.py` | 6 light/dark pairs, structural parity, a11y metadata |
| `python3 tools/check_contrast.py` | all enforced WCAG AA pairs pass |
| `python3 tools/build_site.py --check` | 15 site files byte-identical |
| `python3 tools/check_publish.py` | 268 text files, 0 secret/identifier findings |
| `python3 tools/verify_publication.py` | 19/19 checks pass |
| `python3 -m unittest discover -s tests -p "test_*.py"` | 5/5 pass |
| `node tests/visual_qa.js` | 5 viewports, 0 console errors, 0 overflow, 6/6 figures |

Playwright QA ran in headless Chromium from a Playwright install; screenshots
and `tests/qa-report.json` are committed. On minimal images, see
`docs/REPRODUCE.md` for the shared-library note.

## Security status

`PUBLISH CHECK OK`: no credentials, no non-public home paths, no hostname, no
IP addresses, no private keys, no raw workspaces or sealed run directories. The
embedded payload of the forensic report is decompressed and scanned, not just
the outer HTML. The sanitized report's hash matches `reports/SEAL.json`.

## Limitations

One trial per agent-task (n=1); three tasks only; shared VPS and provider; a
single Unix user for isolation; JCode's tool-level trace unavailable; nominal
list price is not subscription cash; the benchmark was built by the Pi-Bolt
author. These are documented in `docs/LIMITATIONS.md` and on the site. The
graders in this repository are a frozen public edition and must not be reused
as hidden tests for future competitive editions.

## Release

`v0.1.0` — experimental research preview. Not a universal coding-agent
ranking, not an industry leaderboard, and not a claim of statistical
superiority.
