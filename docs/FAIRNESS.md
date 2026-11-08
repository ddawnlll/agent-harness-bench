# Fairness, controls and confounders

The benchmark holds the model constant and varies the agent — to the extent
described here.

## 1. Held constant

| Dimension | Value |
|---|---|
| Model, provider, effort | `deepseek-v4.1-flash`, OpenCode Go, `low`, pinned at the relay |
| Credential | one upstream key held only by the relay; agents received a dummy token |
| Task bytes | fresh checkout of the same pinned commit in every workspace of a round |
| Task statement, public tests | identical files in every workspace of a round |
| Hidden tests, oracle, negatives | outside every agent-readable tree, never copied in |
| Git remote | removed from every workspace |
| Limits | `MemoryMax=3GiB`, `CPUQuota=200%`, `TasksMax=4096` |
| Environment | separate `HOME`, `XDG_*`, `TMPDIR`, workspace and relay listener |
| Deadline, start, grading | 1680 s per round, 7200 s watchdog; common barrier; independent sequential grading |
| Sampling | 1 s cgroup samples; final snapshot authoritative |

The relay observed one model identifier and `reasoning_effort=low` on all 261
chat-completions requests, so no trial silently changed model or effort.

## 2. Concurrency and CPU rotation

Three agents run simultaneously from one barrier, exposing all three to the same
provider-capacity pressure. The 6 logical CPUs are split into three pairs and
rotated per round:

| Round | Task | Pi-Bolt | JCode | Hax |
|---|---|---|---|---|
| `r1-safety` | T1 | `0,1` | `2,3` | `4,5` |
| `r2-performance` | T2 | `2,3` | `4,5` | `0,1` |
| `r3-coordinator` | T3 | `4,5` | `0,1` | `2,3` |

This is a balanced Latin square: each agent occupies each pair once. It does not
randomise order — every agent sees T1 first, on its first pair — so round, task
and pair are confounded by design. Full randomisation would need repeated trials,
which this run did not have. One hax trial recorded 8 CPU-throttle events
totalling 0.0025 s during T3; all others recorded none. Negligible, but reported
rather than rounded to "zero throttling".

## 3. Isolation

Separate Unix users were unavailable (no passwordless sudo), so all competitors
ran as the same user. Isolation was: a transient systemd user scope per trial
with limits; `taskset` affinity; private `HOME`, `XDG_*` and `TMPDIR`; a private
workspace copy with no remote; hidden material outside the workspace; and a
per-trial loopback relay. This is **operational isolation, not a security
boundary**: a determined agent could read predictable sibling paths. The prompt
forbids it, graders detect test tampering, and none was observed — but detection
is not prevention.

## 4. What could still bias the result

1. **Author-built benchmark.** Written by the Pi-Bolt author. Controls were
   validated and tasks are public, but authorship is a structural confounder;
   independent replication is the remedy.
2. **Task choice.** Three Python tasks, two emphasising safety and durability. A
   different mix could reorder quality and cost.
3. **n = 1.** No within-cell variance, confidence interval or superiority claim.
4. **Provider contention.** Three streams shared one account and endpoint;
   queueing changes wall time and could reorder timings.
5. **Advisory CPU placement.** Without cpuset, a process can change its own
   affinity, and host neighbours were uncontrolled.
6. **Shared Unix user.** Isolation is cgroup and permission based, not a
   security boundary.
7. **Fixed order and warm-up.** T1 → T2 → T3 changes page cache, connection pools
   and host state across rounds; launch jitter was small (2.5–14.0 ms) but not
   the only order effect.
8. **Sequential grading.** After each round, in Pi-Bolt, JCode, Hax order.
   Deterministic and isolated, but not randomised.
9. **Missing JCode trace.** Only a final JSON result was emitted; its tool
   activity is unknown. Token and time accounting are complete because all
   traffic crossed the relay.
10. **Nominal cost.** Provider list price is not incremental subscription cash.
11. **Model nondeterminism.** Sampling makes trajectories stochastic; a rerun
    would not reproduce the diffs.

None of these can be removed by re-weighting the data. They are why this is a
LOW-confidence screening exercise, not a ranking.
