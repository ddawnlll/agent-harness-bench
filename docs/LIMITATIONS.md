# Limitations

This is a screening exercise, not a ranking. The dataset supports statements of
the form "in these nine trials, on these three tasks, with this model and this
host, the following was observed". It does not support "agent X is better than
agent Y" in general, and the repository does not claim that.

## Design limits

- **n = 1 per cell.** Each agent-task pair was run once. There is no estimate of
  within-cell variance, no confidence interval and no significance test. Any
  difference between the agents in a single cell could be sampling noise.
- **Three tasks.** All are Python, all are authored for this benchmark, and two
  of the three emphasise safety and durability. A different task mix could
  produce a different pass ordering. Task selection bias cannot be excluded.
- **Fixed order.** T1, T2 and T3 ran in that order, with a fixed grading order.
  Host state, page cache and provider connection reuse changed across rounds.
- **One host, one model, one effort.** A 6-vCPU KVM guest and
  `deepseek-v4.1-flash` at `reasoning_effort=low`. Nothing here establishes
  behaviour at higher effort, with a different model, or on other hardware.
- **No statistical inference is performed.** The published confidence is LOW,
  and the "best balanced" label is a decision aid, not a finding.

## Measurement limits

- **Provider contention.** Three streams shared one provider account. Latency
  and queueing are outcomes, and a different contention pattern could reorder
  wall times. Per-request durations are preserved so this can be re-examined,
  but they cannot be normalised away.
- **CPU placement is advisory.** The cpuset controller was unavailable, so
  `taskset` affinity was used. A process can change its own affinity, and host
  neighbours were not controlled.
- **Memory is cgroup peak, not RSS.** The published peak is cgroup
  `memory.peak`, which includes page cache charged to the scope. The T2 grader
  separately reports process peak RSS for its workloads; the two are different
  quantities and are labelled as such.
- **Singular resource events.** One hax trial recorded 8 CPU-throttle events
  totalling 0.0025 s; every other trial recorded none. Negligible, reported, not
  rounded to zero.
- **JCode has no tool-level trace.** Its CLI emitted only the final JSON result.
  Token and time accounting are complete because all model traffic crossed the
  relay, but *what it did between requests* is unknown, and tool-call counts for
  JCode are marked unavailable rather than zero.
- **Two heuristics.** Maintainability points are computed from diff size, debug
  prints, forbidden imports, test integrity and changed-line counts. They are
  rule-based, reproducible, and cannot establish long-term maintainability from
  a 30-minute patch.

## Accounting limits

- **Nominal price is not cash.** Costs are provider-reported list prices on
  OpenCode Go, a subscription service. They rank resource consumption, not the
  incremental amount billed. The provider's allowance figure is published but
  its cash meaning is undocumented.
- **Grading was sequential after each round.** Deterministic graders in isolated
  workspaces make order effects unlikely, but the sequence is disclosed.
- **Timing includes API waiting.** Wall time is barrier release to process exit,
  so provider latency is inside the number. `api_service_s` and derived busy
  time let a reader separate waiting from local work; neither is a clean
  "thinking time".

## Isolation and security limits

- **One shared Unix user.** Isolation was cgroup, process, filesystem and prompt
  based, not a security boundary. A determined agent could read predictable
  sibling paths; no tampering was detected, but detection is not prevention.
- **The relay listened on loopback.** Any local process on the host could have
  used it while a trial was active.
- **Hidden tests are only hidden for this edition.** The task repositories,
  public tests and grading rules are now published. Historical graders are
  therefore unsuitable as hidden tests for future competitions; new hidden cases
  must be authored and kept outside the public tree.

## Confounders that cannot be removed by re-analysis

- **Author-built benchmark.** The benchmark was built by the Pi-Bolt author.
  Grader controls were validated, and the task statements are public, but
  authorship is a structural confounder. Independent replication, ideally with
  tasks and graders authored by someone else, is the only real remedy.
- **Agent affordances differ by design.** Each agent brings its own system
  prompt, tool set, scheduling and context policy. Those differences *are* the
  thing being measured, not a fairness violation, but they mean the result is
  about the whole product, not a controlled comparison of one algorithm.

## What future work would establish

1. **Repeated trials** (n ≥ 5 per cell) with randomised round order and
   interleaved starts, yielding medians, ranges and a variance estimate.
2. **A larger, independently authored task suite** spanning languages and task
   families, with a pre-registered scoring rubric.
3. **Independent graders** authored outside the competing projects, reviewed by
   a third party before the run and kept secret until after it.
4. **Provider-controlled measurement**, or at least a second provider and a
   second host, to separate agent behaviour from infrastructure.
5. **Separate Unix users or containers per competitor**, making isolation a
   security boundary rather than an operational convention.
6. **Actual billing data** from the subscription, if the provider exposes it,
   instead of nominal list prices.
7. **A JCode-native trace** to make tool activity observable and comparable.

None of these are present in this release. The honest summary is that the
benchmark demonstrates a method and publishes reproducible evidence for one
small experiment; it does not crown a winner.
