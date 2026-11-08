# Contributing

Contributions are welcome in four areas: new tasks, new agent adapters, checks
and reproducibility tooling, and corrections to the published record. The bar is
evidence, not enthusiasm.

## Ground rules

- **No fabricated data.** Never hand-edit `results/publication-data.json` or the
  figures. Change the sealed evidence or the derivation and re-derive. A missing
  value is published as unavailable, never zero.
- **Determinism.** Charts, site and dataset must be byte-reproducible from
  committed inputs: no clocks, unseeded randomness or network access in a build.
- **Neutrality.** No marketing language, superiority claims or disparagement;
  equal visual weight for every competitor.
- **Privacy.** No home paths, user names, hostnames, IP addresses, credentials or
  session identifiers in any committed file.

## Proposing a task

1. **Contract first.** `TASK.md` states required behaviour, the stable public
   API, constraints (standard library only, no network) and a testable
   definition of done.
2. **Public tests** a competent agent can run, without revealing hidden edges.
3. **Hidden suite** outside every agent-readable tree; each case has an id,
   severity, weight and requirement, grouped into P0 gates.
4. **Oracle and negatives.** A reference solution that passes and at least one
   hand-broken variant per P0 gate that fails for the intended reason. Run
   `python3 graders/<task>/validate.py`: oracle passes, seeded baseline fails,
   each negative fails its expected gate.
5. **Determinism.** The hidden suite must repeat identically; bound waits and
   record seeds.
6. **Pin the start commit** and task statement hash; do not rewrite history after
   a run.

## New hidden cases for future editions

The graders here are **public** and must not be reused as hidden tests in a
future competition. New cases must be authored and reviewed outside the public
tree, checked against the oracle and a negative control, deterministic and
offline, assigned to a P0 gate only when failure invalidates completion, and
secret until the edition closes. A leaked case is retired, not reused.

## Adding an agent adapter

An agent is measured through a thin adapter that must accept a non-interactive
prompt and operate on the provided workspace; run under `MemoryMax=3GiB`,
`CPUQuota=200%`, `TasksMax=4096` with its own `HOME`, `XDG_*` and `TMPDIR`;
reach the model only through the loopback relay with a dummy token; declare a
version string and binary SHA-256; exit 0 on completion and non-zero on failure;
and optionally emit a machine-readable tool trace (missing is recorded as
unavailable, not estimated). Do not patch agent behaviour for the benchmark:
transport headers and model pinning belong in the relay, semantic changes in the
agent.

## Checks before a pull request

```bash
python3 tools/make_charts.py --check
python3 tools/check_svg.py
python3 tools/check_contrast.py
python3 tools/check_publish.py
python3 tools/verify_publication.py
```

If a check is absent from your checkout, say so rather than claiming it passed.
See [REPRODUCE.md](REPRODUCE.md) and [SECURITY.md](SECURITY.md).

## Reporting issues

For a data or grading error, include the trial id, field and the primary record
you believe is correct; do not propose a hand-edited JSON. For a leak, describe
the location only and do not paste content. For a methodology disagreement,
state the confounder and the analysis that would detect it; re-analysis is
welcome, reruns need the sealed evidence.
