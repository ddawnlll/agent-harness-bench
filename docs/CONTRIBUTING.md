# Contributing

Contributions are welcome in four areas: new tasks, new agent adapters, checks
and reproducibility tooling, and corrections to the published record. The
project is a research artifact, so the bar is evidence, not enthusiasm.

## Ground rules

- **No fabricated data.** Never hand-edit `results/publication-data.json` or the
  figures. Change the sealed evidence or the derivation, then re-derive. A
  number that cannot be traced to a primary record does not ship; a missing value
  is published as unavailable, never as zero.
- **Determinism is a feature.** Charts, the site and the derived dataset must be
  byte-reproducible from committed inputs. If a change introduces a clock, a
  random seed without a constant, or network access into a build step, it will be
  rejected.
- **Neutrality.** Documentation and figures describe agents without marketing
  language, universal superiority claims or disparagement. Equal visual weight
  for every competitor.
- **Privacy.** No home paths, user names, hostnames, IP addresses, credentials or
  session identifiers in any committed file. Run the publication checks before
  opening a pull request.

## Proposing a task

A task is a frozen repository plus a contract, a hidden suite and controls.

1. **Contract first.** Write `TASK.md` with the required behaviour, the public
   API that must remain stable, the constraints (standard library only, no
   network) and the definition of done. The contract must be testable.
2. **Public tests.** Ship a public suite that a competent agent can run. It must
   not reveal the hidden edge cases.
3. **Hidden suite.** Keep hidden tests, oracle and negative controls outside any
   agent-readable tree. Each hidden case gets an id, a severity, a weight and a
   requirement string. Group them into P0 gates.
4. **Oracle and negatives.** Provide a reference solution that passes and at
   least one hand-broken variant per P0 gate that fails for the intended reason.
   Run `python3 graders/<task>/validate.py`; it must report the oracle passing,
   the seeded baseline failing, and every negative failing its expected gate.
5. **Determinism.** The hidden suite must produce the same result on repeat
   runs. If it depends on timing, seeds or process scheduling, bound the waits
   and record the seeds.
6. **Pin the start commit.** Record the commit and the task statement hash. Do
   not rewrite history in a task repository after a run.

## New hidden cases for future editions

The graders in this repository are **public** and must not be reused as hidden
tests in a future competition. New cases must be:

- authored and reviewed outside the public tree;
- checked against the oracle and at least one negative control;
- deterministic and offline;
- assigned to a P0 gate only if a failure invalidates the completion, not merely
  the score;
- kept secret until after the edition closes.

A case that leaks is retired, not silently reused.

## Adding an agent adapter

An agent is measured through a thin, documented adapter. It must:

1. accept a non-interactive prompt and operate on the provided workspace;
2. run under the standard scope limits (`MemoryMax=3GiB`, `CPUQuota=200%`,
   `TasksMax=4096`) with its own `HOME`, `XDG_*` and `TMPDIR`;
3. reach the model only through the loopback relay, holding a dummy token;
4. declare a version string and a binary SHA-256 for the record;
5. exit with code 0 on normal completion and a non-zero code on failure;
6. optionally emit a machine-readable tool trace. A missing trace is recorded as
   unavailable, not estimated.

Do not patch an agent's behaviour for the benchmark. If the adapter needs a
transport header or a model pin, the relay supplies it; semantic changes belong
in the agent, not the harness.

## Running checks before a pull request

```bash
python3 tools/make_charts.py --check
python3 tools/check_svg.py
python3 tools/check_contrast.py
python3 tools/check_publish.py
python3 tools/verify_publication.py
```

If a check is not present in your checkout, say so in the pull request rather
than claiming it passed. See [REPRODUCE.md](REPRODUCE.md) for the full pipeline
and [SECURITY.md](SECURITY.md) for the publication policy.

## Reporting issues

- **Data or grading error:** open an issue with the trial id, the field and the
  primary record you believe is correct. Do not propose a hand-edited JSON.
- **Leak or personal datum:** describe the location only; do not paste the
  content. See [SECURITY.md](SECURITY.md).
- **Methodology disagreement:** state the confounder and the analysis that would
  detect it. Re-analysis of the published data is welcome; reruns require the
  sealed evidence.
