# Agent Harness Bench — documentation index

A controlled screening benchmark of three coding agents (Pi-Bolt, JCode, Hax) on
three engineering tasks under one model, provider and reasoning effort. Nine
trials, independent hidden grading, deterministic figures, sanitized public
data. The result is a low-confidence screening exercise, not a universal
ranking. Start with [RESULTS.md](RESULTS.md) and [LIMITATIONS.md](LIMITATIONS.md).

| Document | Contents |
|---|---|
| [DESIGN.md](DESIGN.md) | Figure design: type, colour tokens, agent mapping, grid, annotation, accessibility, SVG, responsive rules |
| [METHODOLOGY.md](METHODOLOGY.md) | Tasks, hidden tests, rubric and P0 gates, oracle/negative validation, isolation, relay accounting, measurement definitions and formulas |
| [FAIRNESS.md](FAIRNESS.md) | Controls, CPU rotation mapping, concurrency and grading order, remaining confounders |
| [RESULTS.md](RESULTS.md) | Per-trial scores, the `h07` case, T2 parity and speed, T3 gates, multi-objective summary |
| [LIMITATIONS.md](LIMITATIONS.md) | n=1, three tasks, contention, missing JCode trace, authorship bias, future work |
| [REPRODUCE.md](REPRODUCE.md) | Commands for data, charts, site, checks and browser QA; prerequisites and hashes |
| [SECURITY.md](SECURITY.md) | What was scanned, redaction rules, unpublished artifacts, verification, residual risks |
| [ATTRIBUTION.md](ATTRIBUTION.md) | Credits, previous benchmark, agent versions and hashes, licenses, no affiliation |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Proposing tasks, adapters, secret hidden cases, checks, issues |

Machine-readable files here: `LICENSE` (MIT) and `CITATION.cff`. Evidence lives
in `results/publication-data.json` and `results/TRANSFORM.json`, figures in
`assets/`, generation code in `tools/`.
