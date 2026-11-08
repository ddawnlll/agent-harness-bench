# Results

All values below are copied from `results/publication-data.json`
(schema `agent-harness-bench/public-data/1`). MiB values are bytes ÷ 1048576,
rounded to 0.1; costs are USD list-price sums rounded to four decimals; other
figures are rounded as shown. The underlying exact integers are in the JSON.

## 1. Headline

Nine of nine trials completed. No timeouts, crashes, provider errors or OOM
kills were observed; every agent exited 0. Verified passes were **JCode 3/3,
Pi-Bolt 2/3, Hax 2/3**. The entire difference is one hidden safety case, `h07`
(stale-callback guard), whose P0 gate overrides a high numeric score. The
published recommendation is JCode with **LOW** confidence.

## 2. Per-trial results

| Task | Agent | Status | Score | Wall s | API s | CPU s | Peak MiB | Tokens | Reqs | Nominal USD |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T1 | Pi-Bolt | FAIL | 97.58 | 365.7 | 353.6 | 20.152 | 29.7 | 596,089 | 19 | 0.0281 |
| T1 | JCode | PASS | 98.00 | 206.1 | 197.8 | 8.622 | 25.0 | 859,476 | 19 | 0.0252 |
| T1 | Hax | FAIL | 97.58 | 192.3 | 185.5 | 9.203 | 14.2 | 798,804 | 26 | 0.0262 |
| T2 | Pi-Bolt | PASS | 100.00 | 439.2 | 285.7 | 160.320 | 98.8 | 1,172,075 | 35 | 0.0279 |
| T2 | JCode | PASS | 100.00 | 645.4 | 301.6 | 344.069 | 82.1 | 1,968,671 | 35 | 0.0372 |
| T2 | Hax | PASS | 100.00 | 977.6 | 375.0 | 610.338 | 88.9 | 2,278,973 | 49 | 0.0418 |
| T3 | Pi-Bolt | PASS | 100.00 | 262.4 | 247.9 | 22.061 | 89.8 | 534,826 | 17 | 0.0278 |
| T3 | JCode | PASS | 100.00 | 259.6 | 221.5 | 22.763 | 70.2 | 1,491,004 | 25 | 0.0331 |
| T3 | Hax | PASS | 100.00 | 376.9 | 307.0 | 48.034 | 74.5 | 1,812,075 | 36 | 0.0405 |

Quality points are the sum of the three scores: JCode 298.00, Pi-Bolt 297.58,
Hax 297.58, out of 300.

## 3. T1 — the decisive gate

| P0 gate | Pi-Bolt | JCode | Hax |
|---|---|---|---|
| `G1_PREMATURE_PROTECTION` | pass | pass | pass |
| `G2_FAIL_CLOSED` | pass | pass | pass |
| `G3_FILL_ATTRIBUTION` | pass | pass | pass |
| `G4_PROTECTION_LOSS` | **fail** | pass | **fail** |
| `G5_RESTART_FIDELITY` | pass | pass | pass |
| `G6_CANCEL_RACE` | pass | pass | pass |
| `G7_NATIVE_IDENTITY` | pass | pass | pass |
| `G8_TEST_INTEGRITY` | pass | pass | pass |
| `G9_NO_SHORTCUT` | pass | pass | pass |

The only failing case for Pi-Bolt and Hax is `h07`
(`stale_callback_guard`, safety, weight 3). The scenario: a protective stop has
been acknowledged at version 1 and is `ACTIVE`; a duplicate callback arrives
carrying version 0 and reports the stop as `CANCELLED`. Versions are monotonic,
so the stale callback must be ignored and the position must stay protected.
Pi-Bolt and Hax applied it and downgraded protection while the position remained
open — the fail-open behaviour the task exists to catch. The grader detail for
both is the observed post-callback state, `+ ACTIVE`; JCode kept the position
protected and passed.

This is not a flake. The T1 grader runs its hidden suite twice, and both runs
record the same `h07` failure for Pi-Bolt and Hax and no failure for JCode
(`determinism.agreement = true` for all three). The numeric consequence is
small — safety 22.58 of 25, a 2.42-point deduction — but the P0 rule is what
matters: a position declared protected while it is not cannot be a verified
completion. Reported as partial engineering progress, the two T1 patches still
earn 97.58.

Category scores for T1 were identical except maintainability: Pi-Bolt and Hax
10/10, JCode 8/10, which is why JCode's total is 98.00 rather than 100.

## 4. T2 — parity and speed

All three agents passed every T2 P0 gate, produced output identical to the
pristine engine on all four hidden workloads, and passed both the 53 public
tests and the 51-test pristine suite. Median timings in milliseconds:

| Workload | Pi-Bolt base → cand | Speed-up | JCode base → cand | Speed-up | Hax base → cand | Speed-up |
|---|---|---:|---|---:|---|---:|
| `h_large` | 7,987.73 → 1,599.68 | **4.993×** | 7,849.48 → 1,540.39 | **5.096×** | 8,374.57 → 1,487.78 | **5.629×** |
| `h_adversarial` | 2,723.92 → 461.09 | 5.908× | 2,147.44 → 778.73 | 2.758× | 2,025.15 → 419.65 | 4.826× |
| `h_small` | 169.36 → 166.42 | 1.018× | 175.67 → 231.59 | 0.759× | 173.71 → 148.86 | 1.167× |
| `h_fresh` | 192.46 → 216.15 | 0.890× | 242.56 → 291.58 | 0.832× | 240.79 → 141.06 | 1.707× |

The 3× large-workload target was cleared by all three. The small and fresh
profiles are honest counter-evidence: JCode was slower than the pristine engine
on both (0.759× and 0.832×), and Pi-Bolt was slower on `h_fresh` (0.890×). The
scoring rules treat sub-1.2× changes as inside a noise dead band, and performance
points depend on the large and adversarial profiles, so these results do not
change the grade. They do show that "faster" here means "faster on the profiles
the task targets", not uniformly faster.

Peak RSS on `h_large` fell for all three: ratio candidate/pristine 0.698
(Pi-Bolt), 0.691 (JCode), 0.725 (Hax) — roughly 30% lower. Determinism checks
reported exactly one distinct output per workload for baseline and candidate in
three repeats.

## 5. T3 — crash-safe coordination

| P0 gate | Pi-Bolt | JCode | Hax |
|---|---|---|---|
| `no_duplicate_ownership` | pass | pass | pass |
| `persistence_restart` | pass | pass | pass |
| `no_lost_completion` | pass | pass | pass |
| `bounded_retries` | pass | pass | pass |
| `non_memory_storage` | pass | pass | pass |
| `crash_recovery` | pass | pass | pass |
| `test_integrity` | pass | pass | pass |
| `corrupted_state_safe` | pass | pass | pass |
| `no_network_thirdparty` | pass | pass | pass |
| `idempotent_completion` | pass | pass | pass |

All 25 hidden cases passed for all three agents. The concurrency stress rounds
(8 workers × 200 jobs, twice) reported 200 claimed, 200 done, 200 results, zero
duplicates and zero errors in every round for every agent. Determinism agreement
was true. T3 separates the agents only on resources, not on correctness.

## 6. Multi-objective summary

| Agent | Verified | Quality /300 | Σ wall s | Mean success wall s | CPU s | Mean peak MiB | Max peak MiB | Tokens | Nominal USD | USD / verified |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Pi-Bolt | 2/3 | 297.58 | 1,067.3 | **350.8** | 202.533 | 72.8 | 98.8 | 2,302,990 | **0.0838** | 0.0419 |
| JCode | **3/3** | **298.00** | 1,111.1 | 370.4 | 375.454 | **59.1** | 82.1 | 4,319,151 | 0.0955 | **0.0318** |
| Hax | 2/3 | 297.58 | 1,546.9 | 677.3 | 667.575 | 59.2 | 88.9 | 4,888,852 | 0.1085 | 0.0543 |

`Mean success wall s` averages only verified passes: Pi-Bolt's 350.8 s is two
tasks, JCode's 370.4 s is three, Hax's 677.3 s is two, so the mean is not
comparable across different denominators.

Decision-specific readings published in the dataset:

| Criterion | Result |
|---|---|
| Best balanced | JCode |
| Best quality | JCode |
| Cheapest per verified task | JCode (0.0318 USD) |
| Fastest verified (mean successful wall) | Pi-Bolt (350.8 s) |
| Lowest mean peak memory | JCode (59.1 MiB) |

Token efficiency, also derived in the dataset:

| Agent | Tokens per quality point | Quality points per million tokens |
|---|---:|---:|
| Pi-Bolt | 7,739.1 | 129.21 |
| JCode | 14,493.8 | 69.00 |
| Hax | 16,432.1 | 60.86 |

Pi-Bolt used the fewest tokens; JCode used 1.88× as many as Pi-Bolt and Hax
2.12× as many. JCode's higher spend bought the third verified completion, which
is why cost per verified task still favours it.

The provider also reported an allowance figure (0.0503 / 0.0573 / 0.0651 USD).
Its relationship to subscription cash is not documented by the provider, so it
is published in the JSON but is not used for rankings.

## 7. Timing and resources

Round wall times (barrier release to the last grade) were 375.2 s (T1),
1,187.5 s (T2) and 443.5 s (T3); the experiment measured 2,006.2 s in total.
T2 dominates because the agents ran the replay benchmark repeatedly while
graders measured interleaved baselines.

CPU time is dominated by T2 for every agent: 160.3 s of Pi-Bolt's 202.5 s total,
344.1 s of JCode's 375.5 s, and 610.3 s of Hax's 667.6 s. T1 and T3 are
API-bound, not CPU-bound (8.6–48.0 s). Memory peaks occurred in T2 as well
(82–99 MiB); T1 stayed under 30 MiB for all agents.

Request counts total 261 chat completions (Pi-Bolt 71, JCode 79, Hax 111). One
model identifier was observed on all of them, and every request carried
provider-reported usage.

## 8. What this does and does not show

It shows that, on these three tasks and this model, three capable agents can all
produce high-scoring work, and that a single safety invariant can separate them
where aggregate scores cannot. It does not show that JCode is generally better
than the others: one trial per cell, three tasks, one provider, one host, and a
benchmark written by one competitor's author. See
[LIMITATIONS.md](LIMITATIONS.md).
