# Fairness, controls and confounders

The benchmark holds the model constant and varies only the agent. That sentence
is true only to the extent described here; this document lists what was actually
controlled, what was rotated, and what could still bias the result.

## 1. Held constant across all nine trials

| Dimension | Value |
|---|---|
| Model | `deepseek-v4.1-flash` |
| Provider | OpenCode Go (`opencode-go`) |
| Reasoning effort | `low`, pinned at the relay; forwarded unchanged |
| Credential | one upstream key, held only by the relay; agents received a dummy token |
| Task bytes | fresh checkout of the same pinned commit per round; no per-agent editing of the task statement |
| Task statement and public tests | identical files in every workspace of a round |
| Hidden tests, oracle, negative controls | outside every agent-readable tree, never copied into a workspace |
| Git remote | removed from every workspace |
| Scope limits | `MemoryMax=3GiB`, `CPUQuota=200%`, `TasksMax=4096` |
| Per-trial environment | separate `HOME`, `XDG_*`, `TMPDIR`, workspace, relay listener |
| Deadline | 1680 s per round; 7200 s global watchdog |
| Start | common barrier released once all three agents are ready |
| Grading | independent grader, sequential after the round, same script for all agents |
| Resource sampling | 1 s cgroup sampling; final cgroup snapshot authoritative |

In addition, the relay observed one model identifier and `reasoning_effort=low`
on all 261 chat-completions requests, so no trial silently used a different model
or effort.

## 2. Concurrency and CPU rotation

Three agents run simultaneously in every round, all starting at one barrier.
Concurrency is deliberate: it reflects how developers run agents in parallel and
it exposes the agents to the same provider-capacity pressure. It also means
latency is an outcome, not a controlled constant.

The host has 6 logical CPUs. The controller assigns one core pair per agent and
rotates the pairs per round. The recorded mapping from the trial data is:

| Round | Task | Pi-Bolt | JCode | Hax |
|---|---|---|---|---|
| `r1-safety` | T1 | `0,1` | `2,3` | `4,5` |
| `r2-performance` | T2 | `2,3` | `4,5` | `0,1` |
| `r3-coordinator` | T3 | `4,5` | `0,1` | `2,3` |

This is a balanced Latin square across agents and CPU pairs: each agent occupies
each pair exactly once. It balances CPU placement but does **not** randomise
order: every agent sees T1 first and on its first pair. Round, task and pair are
therefore confounded by design. With three rounds, full randomisation would have
needed repeated trials, which this run did not have.

A single hax trial recorded 8 CPU-throttle events totalling 0.0025 s during
T3; every other trial recorded none. That is negligible against a 377 s run but
is reported rather than rounded to "zero throttling".

## 3. Isolation: what it is and is not

Separate Unix users were not available on the host (no passwordless sudo), so
all competitors ran as the same user. Isolation was:

- a transient systemd user scope per trial with memory, CPU and task limits;
- `taskset` core affinity;
- a private `HOME`, `XDG_*` and `TMPDIR` per trial;
- a private workspace copy with no git remote;
- filesystem placement of hidden material outside the workspace tree;
- a per-trial loopback relay.

This is **operational isolation, not a security boundary**. A determined agent
running as the same user could read predictable sibling paths. The prompt
forbids it, graders detect test tampering and fixture hardcoding, and no such
tampering was observed — but absence of detection is not proof of absence.

## 4. What could still bias the result

1. **Author-built benchmark.** The benchmark and its graders were written by the
   Pi-Bolt author. The graders were validated against oracle and negative
   controls, and task statements are public in the repository, but authorship
   remains a confounder. Independent replication is the remedy.
2. **Task choice.** Three tasks, all Python and all authored for this benchmark,
   over-represent safety and persistence engineering. A different task mix could
   reorder quality and cost.
3. **n = 1.** Every agent-task cell was run once. There is no within-cell
   variance estimate, no confidence interval and no basis for a superiority
   claim. The reported confidence is LOW.
4. **Provider contention.** Three concurrent streams shared one provider
   account and endpoint. Queueing, rate limits and latency affect wall time; a
   different contention pattern could reorder the timing ranking. Per-request
   durations and HTTP statuses are preserved so this can be re-examined.
5. **CPU placement is advisory.** Without the cpuset controller, `taskset`
   affinity can be escaped by a process that changes its own affinity, and
   neighbours on the host are not controlled.
6. **Shared Unix user.** See above: isolation is cgroup- and permission-based,
   not a security boundary, and cross-reading is detectable only after the fact.
7. **Order and warm-up.** Round order is fixed (T1, T2, T3) and host state (page
   cache, provider connection pools) changes across rounds. Launch jitter was
   small (2.5–14.0 ms) but is not the only order effect.
8. **Grading sequence.** Graders ran sequentially after each round, in the fixed
   order Pi-Bolt, JCode, Hax. Grading is deterministic and isolated, so order
   should not matter; it is disclosed because it is not randomised.
9. **Missing JCode trace.** JCode's CLI emitted only a final JSON result, so its
   tool-level activity is unknown. Token and time accounting are complete because
   all model traffic crossed the relay; the *shape* of its work is not observed.
10. **Nominal cost.** Provider list price is not incremental subscription cash.
    The dollar figures rank resource consumption under a published price list,
    not what the operator was billed.
11. **Model nondeterminism.** Sampling makes agent trajectories stochastic. Even
    with pinned model and effort, a rerun would not reproduce the diffs.

None of these can be removed by re-weighting the existing data. They are the
reason the result is presented as a screening exercise with a LOW confidence
label, not a ranking.
