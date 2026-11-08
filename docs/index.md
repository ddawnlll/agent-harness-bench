# Agent Harness Bench — documentation index

A controlled screening benchmark of three coding agents (Pi-Bolt, JCode, Hax) on
three engineering tasks under one model, one provider and one reasoning effort.
Nine trials, independent hidden grading, deterministic figures and a sanitized
public dataset. The result is a low-confidence screening exercise, not a
universal ranking.

Start with [RESULTS.md](RESULTS.md) for the findings and
[LIMITATIONS.md](LIMITATIONS.md) for what they do not support.

| Document | What it covers |
|---|---|
| [DESIGN.md](DESIGN.md) | The figure design system: type, colour tokens, agent mapping, grid, annotation rules, accessibility, SVG and responsive rules |
| [METHODOLOGY.md](METHODOLOGY.md) | Tasks, hidden tests, rubric and P0 gates, oracle/negative validation, isolation, relay accounting, measurement definitions and formulas |
| [FAIRNESS.md](FAIRNESS.md) | Controls, CPU rotation mapping, concurrency and grading order, and the honest list of remaining confounders |
| [RESULTS.md](RESULTS.md) | Per-trial scores, the `h07` case study, T2 parity and speed, T3 P0 gates, and the multi-objective summary |
| [LIMITATIONS.md](LIMITATIONS.md) | n=1, three tasks, provider contention, missing JCode trace, authorship bias, and what future work would establish |
| [REPRODUCE.md](REPRODUCE.md) | Commands to re-derive data, rebuild figures, build the site, run checks and browser QA; prerequisites, hashes and what cannot be reproduced |
| [SECURITY.md](SECURITY.md) | Publication security: what was scanned, redaction rules, what is not published, verification performed and residual risks |
| [ATTRIBUTION.md](ATTRIBUTION.md) | Credits, previous benchmark, agent versions and hashes, model/provider, licensing and no-affiliation statement |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to propose tasks, add an agent adapter, keep hidden cases secret, run checks and report issues |

Repository root files: [`LICENSE`](../LICENSE) (MIT) and
[`CITATION.cff`](../CITATION.cff). The evidence lives in `results/publication-data.json` and
`results/TRANSFORM.json`, the figures in `assets/`, and the generation code in
`tools/`.
