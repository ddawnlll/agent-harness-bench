# Fairness, controls and confounders

The benchmark holds the model constant and varies the agent. That is true only
to the extent described here. This document lists what was controlled, what was
rotated, and what could still bias the result.

## 1. Held constant

| Dimension | Value |
|---|---|
| Model | `deepseek-v4.1-flash` |
| Provider | OpenCode Go (`opencode-go`) |
| Reasoning effort | `low`, pinned at the relay and forwarded unchanged |
| Credential | one upstream key, held only by the relay; agents received a dummy token |
| Task bytes | fresh checkout of the same pinned commit in every workspace of a round |
| Task statement, public tests | identical files in every workspace of a round |
| Hidden tests, oracle, negatives | outside every agent-readable tree, never copied in |
| Git remote | removed from every workspace |
| Limits | `MemoryMax=3GiB`, `CPUQuota=200%`, `TasksMax=4096` |
| Per-trial environment | separate `HOME`, `XDG_*`, `TMPDIR`, workspace and relay listener |
| Deadline | 1680 s per round; 7200 s global watchdog |
| Start | common barrier released once all three agents are ready |
| Grading | independent grader, sequential after the round, same script for all |
| Sampling | 1 s cgroup samples; final cgroup snapshot authoritative |

The relay observed one model identifier and `reasoning_effort=low` on all 261
chat-completions requests, so no trial silently changed model or effort.

## 2. Concurrency and CPU rotation

Three agents run simultaneously, all starting at one barrier. Concurrency
reflects parallel use and exposes all three to the same provider-capacity
pressure; it also makes latency an outcome, not a constant. The 6 logical CPUs
are split into three pairs and rotated per round:

| Round | Task | Pi-Bolt | JCode | Hax |
|---|---|---|---|---|
| `r1-safety` | T1 | `0,1` | `2,3` | `4,5` |
| `r2-performance` | T2 | `2,3` | `4,5` | `0,1` |
| `r3-coordinator` | T3 | `4,5` | `0,1` | `2,3` |

This is a balanced Latin square across agents and CPU pairs: each agent occupies
each pair once. It balances placement but does not randomise order — every agent
sees T1 first, on its first pair. Round, task and pair are confounded by design;
full randomisation would need repeated trials, which this run did not have.

One hax trial recorded 8 CPU-throttle events totalling 0.0025 s during T3; all
other trials recorded none. Negligible against a 377 s run, but reported rather
than rounded to "zero throttling".

## 3. Isolation

Separate Unix users were unavailable (no passwordless sudo), so all competitors
ran as the same user. Isolation consisted of: a transient systemd user scope per
trial with memory/CPU/task limits; `taskset` affinity; private `HOME`, `XDG_*`
and `TMPDIR`; a private workspace copy with no remote; hidden material placed
outside the workspace tree; and a per-trial loopback relay.

This is **operational isolation, not a security boundary**. A determined agent
running as the same user could read predictable sibling paths. The prompt
forbids it, graders detect test tampering and fixture hardcoding, and none was
observed — but detection is not prevention.

## 4. What could still bias the result

1. **Author-built benchmark.** The benchmark and graders were written by the
   Pi-Bolt author. Grader controls were validated and task statements are public,
   but authorship remains a structural confounder. Independent replication is the
   remedy.
2. **Task choice.** Three Python tasks, two emphasising safety and durability. A
   different mix could reorder quality and cost.
3. **n = 1.** One run per cell: no within-cell variance, no confidence interval,
   no superiority claim. Confidence is LOW.
4. **Provider contention.** Three streams shared one account and endpoint.
   Queueing and rate limits affect wall time, and a different pattern could
   reorder timings. Per-request durations and statuses are preserved.
5. **Advisory CPU placement.** Without the cpuset controller, a process can
   change its own affinity, and host neighbours were not controlled.
6. **Shared Unix user.** Isolation is cgroup and permission based, not a
   security boundary.
7. **Fixed order and warm-up.** T1 → T2 → T3 changes page cache, connection pools
   and host state across rounds. Launch jitter was small (2.5–14.0 ms) but is not
   the only order effect.
8. **Sequential grading.** Graders ran after each round in Pi-Bolt, JCode, Hax
   order. Grading is deterministic and isolated, but the sequence is disclosed
   because it is not randomised.
9. **Missing JCode trace.** JCode emitted only a final JSON result, so its
   tool-level activity is unknown. Token and time accounting are complete because
   all traffic crossed the relay; the shape of its work is unobserved.
10. **Nominal cost.** Provider list price is not incremental subscription cash;
    the dollar figures rank consumption under a published price list.
11. **Model nondeterminism.** Sampling makes trajectories stochastic; a rerun
    would not reproduce the diffs.

None of these can be removed by re-weighting the existing data. They are why the
result is a LOW-confidence screening exercise, not a ranking.
