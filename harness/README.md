# Benchmark harness (frozen edition)

This directory contains the source used to run the experiment:

```
controller/   Go controller: relay, systemd scopes, cgroup sampling, barrier, watchdog, grading
tasks/        three frozen task repositories (working trees; start SHAs in graders/*/manifest.json)
graders/      independent hidden graders, oracle solutions and negative controls
scripts/      data/report builders used around the run
tests/        controller and report pipeline tests (fake agents, no model spend)
STAGING.json  which files were staged, their hashes and the redaction count
```

The controller is built with `cd harness/controller && go build -o benchctl .`.
`config/experiment.example.json` in the repository root shows the expected
configuration with relative paths.

**These graders are a frozen, public edition.** They are published so the
reported scores can be audited and reproduced. They must not be reused as the
hidden acceptance suite for a future competitive edition: new, secret cases
must be written for that.

The task working trees here are exports, not the original git repositories; the
pinned start commits are recorded in each `graders/<task>/manifest.json`. Raw
run workspaces, logs and sealed evidence are deliberately not redistributed.
