# Limitations

This is a screening exercise, not a ranking. The data supports statements of the
form "in these nine trials, on these tasks, with this model and host, the
following was observed". It does not support "agent X is better than agent Y" in
general, and this repository does not claim that.

## Design

- **n = 1 per cell.** No within-cell variance, no confidence interval, no
  significance test. Any single-cell difference could be sampling noise.
- **Three tasks.** All Python, all authored for this benchmark, two emphasising
  safety and durability. Task selection bias cannot be excluded.
- **Fixed order.** T1 → T2 → T3, with fixed grading order; host state and
  provider connection reuse changed across rounds.
- **One host, one model, one effort.** A 6-vCPU KVM guest with
  `deepseek-v4.1-flash` at `reasoning_effort=low`. Nothing transfers automatically
  to other models, efforts or hardware.
- **No statistical inference** is performed. Confidence is LOW and "best
  balanced" is a decision aid, not a finding.

## Measurement

- **Provider contention.** Three streams shared one account; latency is an
  outcome. Per-request durations are preserved but cannot be normalised away.
- **Advisory CPU placement.** The cpuset controller was unavailable; `taskset`
  affinity can be changed by the process, and host neighbours were uncontrolled.
- **Memory is cgroup peak, not RSS.** It includes page cache charged to the
  scope; the T2 grader separately reports process peak RSS. The two are labelled
  distinctly.
- **Singular events.** One hax trial recorded 8 throttle events totalling
  0.0025 s; others none. Negligible and reported, not rounded to zero.
- **No JCode tool trace.** Its CLI emitted only a final JSON result. Token and
  time accounting are complete (all traffic crossed the relay), but what it did
  between requests is unknown; tool counts for JCode are unavailable, not zero.
- **Heuristic maintainability.** Points come from diff size, debug prints,
  imports, test integrity and changed lines. Reproducible, but no 30-minute patch
  establishes long-term maintainability.

## Accounting and isolation

- **Nominal price is not cash.** Costs are provider list prices on a subscription
  service. The provider's allowance figure is published but its cash meaning is
  undocumented.
- **Grading was sequential** after each round. Deterministic isolated graders make
  order effects unlikely, but the sequence is disclosed.
- **Wall time includes API waiting.** `api_service_s` and derived busy time let a
  reader separate waiting from local work; neither is a clean "thinking time".
- **One shared Unix user.** Isolation was cgroup, process, filesystem and prompt
  based, not a security boundary. No tampering was detected; detection is not
  prevention.
- **Loopback relay.** Any local process could use it while a trial was active.
- **Historical graders are public.** They must not be reused as hidden tests for
  future editions.

## Confounders beyond re-analysis

- **Author-built benchmark.** The benchmark was built by the Pi-Bolt author.
  Controls were validated and tasks are public, but authorship is structural.
  Independent replication is the remedy.
- **Agent affordances differ by design.** System prompt, tools, scheduling and
  context policy are part of each product. That is what is being measured, but it
  means the result is about products, not one algorithm.

## What future work would establish

1. Repeated trials (n ≥ 5) with randomised round order and interleaved starts.
2. A larger, independently authored task suite across languages and families,
   with a pre-registered rubric.
3. Graders authored outside the competing projects and kept secret until close.
4. A second provider and host, or provider-controlled measurement.
5. Separate Unix users or containers, making isolation a security boundary.
6. Actual billing data instead of nominal list prices.
7. A JCode-native trace so tool activity is observable.

None of these are present here. The honest summary: the benchmark demonstrates a
method and publishes reproducible evidence for one small experiment; it does not
crown a winner.
