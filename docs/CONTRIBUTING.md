# Contributing

Contributions are welcome in four areas: new tasks, new agent adapters, checks
and reproducibility tooling, and corrections to the published record. This is a
research artifact, so the bar is evidence, not enthusiasm.

## Ground rules

- **No fabricated data.** Never hand-edit `results/publication-data.json` or the
  figures. Change the sealed evidence or the derivation and re-derive. A number
  must trace to a primary record; a missing value is published as unavailable,
  never zero.
- **Determinism.** Charts, site and dataset must be byte-reproducible from
  committed inputs. No clocks, unseeded randomness or network access in a build
  step.
- **Neutrality.** No marketing language, universal superiority claims or
  disparagement; equal visual weight for every competitor.
- **Privacy.** No home paths, user names, hostnames, IP addresses, credentials or
  session identifiers in any committed file. Run the publication checks before
  opening a pull request.

## Proposing a task

1. **Contract first.** Write `TASK.md` with required behaviour, stable public
   API, constraints (standard library only, no network) and a testable
   definition of done.
2. **Public tests** that a competent agent can run, without revealing hidden
   edge cases.
3. **Hidden suite** outside every agent-readable tree; each case gets an id,
   severity, weight and requirement string, grouped into P0 gates.
4. **Oracle and negatives.** A reference solution that passes and at least one
   hand-broken variant per P0 gate that fails for the intended reason. Run
   `python3 graders/<task>/validate.py`; the oracle must pass, the seeded
   baseline must fail, and each negative must fail its expected gate.
5. **Determinism.** The hidden suite must repeat identically; bound waits and
   record seeds.
6. **Pin the start commit** and task statement hash; do not rewrite history in a
   task repository after a run.

## New hidden cases for future editions

The graders here are **public** and must not be reused as hidden tests in a
future competition. New cases must be authored and reviewed outside the public
tree, checked against the oracle and a negative control, deterministic and
offline, assigned to a P0 gate only when a failure invalidates completion, and
kept secret until the edition closes. A leaked case is retired, not reused.

## Adding an agent adapter

An agent is measured through a thin adapter that must:

1. accept a non-interactive prompt and operate on the provided workspace;
2. run under `MemoryMax=3GiB`, `CPUQuota=200%`, `TasksMax=4096` with its own
   `HOME`, `XDG_*` and `TMPDIR`;
3. reach the model only through the loopback relay, holding a dummy token;
4. declare a version string and binary SHA-256;
5. exit 0 on normal completion and non-zero on failure;
6. optionally emit a machine-readable tool trace. A missing trace is recorded as
   unavailable, not estimated.

Do not patch agent behaviour for the benchmark. Transport headers and model
pinning belong in the relay; semantic changes belong in the agent.

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

- **Data or grading error:** include the trial id, the field and the primary
  record you believe is correct. Do not propose a hand-edited JSON.
- **Leak or personal datum:** describe the location only; do not paste content.
- **Methodology disagreement:** state the confounder and the analysis that would
  detect it. Re-analysis of published data is welcome; reruns need the sealed
  evidence.
