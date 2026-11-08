# Task: make the market-event replay engine fast without changing its results

## Context

`replay/` is a small, deterministic engine that replays historical
market-event logs: it loads a JSONL stream of order submissions and
cancellations, matches them with strict price-time priority, tracks
positions, cash, fees and PnL per account, maintains an audit trail and
writes one canonical JSON result document.

The engine is **correct** but it is far too slow on large workloads. A
production replay of a few tens of thousands of events takes many seconds,
and the cost grows faster than the event count. Your job is to make it
substantially faster while keeping every single output value exactly the
same.

## Goal

Optimise the replay path so that a large event log is replayed at least **3x
faster** (wall clock, same machine, same input) than the current code, with
**byte-identical** output.

The grading harness will:

1. run the public test suite in `tests/`;
2. generate hidden event logs (small, large, and an adversarial profile that
   stresses deep books, duplicate timestamps, duplicate ids, cancels of dead
   orders and heavy partial fills) with seeds you have never seen;
3. run the pristine original engine and your engine on **identical** input
   files, interleaved, several measured repeats, and take medians;
4. compare the two output documents for exact parity.

## Hard constraints

* **Output parity is absolute.** For the same input file, the output document
  must be byte-for-byte identical to what the original engine produces
  (canonical JSON, one line, trailing newline). This includes every trade,
  every account field, every lifecycle counter and the audit digest.
* **Do not change the economic or accounting semantics**: price-time
  priority, maker-price execution, maker/taker fee (`notional * 2 // 10000`
  charged to both sides), position and PnL definitions, order lifecycle
  rules, rejection rules. Only the implementation may change.
* **Do not change the external contract**: the CLI
  (`python3 -m replay.cli --events FILE --out FILE`), the event schema, the
  result schema, exit codes and the canonical serialization.
* **Do not delete, skip, rename or weaken the public tests.** They must all
  keep passing. You may *add* tests. Tests are compared against the pristine
  copies; modifying them is treated as cheating.
* **Do not hardcode outputs** for known fixtures or seeds, and do not cache
  or memoise results keyed by the input (the hidden inputs are new).
* **No network access, no third-party packages.** Python 3 standard library
  only (no numpy, pandas, orjson, ...).
* Do not touch `data/` (the committed fixtures are inputs, not outputs).
* Keep the code maintainable: no dead debug output, no monkeypatching timing
  or `sys.setrecursionlimit`-style tricks, no `time.sleep`.

## What you may change

Everything under `replay/` — you may restructure modules, replace data
structures, add helper modules, cache derived values *within a single run*,
etc. Keep `tests/` passing and keep the public API used by the tests
(`replay.book.Book`, `replay.engine.Engine`, `replay.events.*`) available.

## Commands

```bash
# public test suite (must pass)
python3 -m unittest discover -s tests -v

# replay a fixture
python3 -m replay.cli --events data/small.jsonl --out /tmp/small.json

# generate a large workload to benchmark locally (deterministic)
python3 -m replay.events --seed 4242 --profile large --out /tmp/large.jsonl
python3 -m replay.cli --events /tmp/large.jsonl --out /tmp/large.out.json

# also available: profiles small, medium, adversarial, large
python3 -m replay.events --seed 7 --profile adversarial --out /tmp/adv.jsonl
```

Timing hint: on this machine the `large` profile above takes roughly 8
seconds end-to-end with the current code. A well-optimised implementation
reaches well under 2 seconds.

## Event schema (input, JSONL, one object per line)

```json
{"ts": 1000003, "seq": 1, "type": "submit", "order_id": 1, "account": "acct03",
 "symbol": "BBB", "side": "sell", "price": 100147, "qty": 50}
{"ts": 1000010, "seq": 2, "type": "cancel", "order_id": 1}
```

* `ts` — integer logical timestamp (microseconds); many events may share it.
* `seq` — strictly increasing integer; canonical order is `(ts, seq)`.
* `type` — `"submit"` or `"cancel"`; cancels only carry `order_id`.
* `price` and `qty` are positive integers; `side` is `"buy"` or `"sell"`.

## Output contract (the thing that must not change)

One canonical JSON document, exactly one line plus a trailing newline,
serialized with sorted keys and `,` / `:` separators (i.e.
`json.dumps(result, sort_keys=True, separators=(",", ":"))`).

Top-level keys:

| key | meaning |
| --- | --- |
| `schema` | always `"replay.v1"` |
| `event_count` | number of processed events |
| `trade_count` | number of fills |
| `trades` | list of fills in execution order: `{ts, seq, symbol, price, qty, taker, maker, taker_account, maker_account}` |
| `accounts` | per account: `cash`, `fees`, `traded_qty`, `pnl`, `positions` (non-zero positions only) |
| `symbols` | per symbol: `best_bid`, `best_ask`, `checksum`, `mark`, `max_open_interest`, `mean_open_interest`, `open_orders`, `open_qty`, `traded_qty` |
| `lifecycle` | `submitted`, `cancelled`, `rejected`, `filled_orders`, `open_at_end`, `open_qty_at_end`, `trade_count`, `traded_qty`, `traded_notional`, `max_open_orders` |
| `audit` | `{"digest": <16 hex chars>, "entries": <int>}` |

Precise definitions (unchanged by this task):

* A submit is matched against the opposite side while its limit crosses the
  best opposite price; each fill executes at the **resting** order's price,
  best price first, FIFO inside a price level. Residual quantity rests.
* A cancel removes the residual of a live order; cancelling an unknown,
  already filled or already cancelled order is rejected. Reusing an
  `order_id` is rejected.
* `fee = price * qty * 2 // 10000`, charged to both counterparties.
  Buyer cash `-= price*qty + fee`, seller cash `+= price*qty - fee`.
* `mark` is the mid of the final book (`(best_bid + best_ask) // 2`) when
  both sides exist, else the last trade price, else the single-sided quote,
  else `0`. `pnl = cash + sum(position[symbol] * mark[symbol])`.
* `open_interest` for a symbol is the sum of all **positive** account
  positions in it; `max_open_interest` / `mean_open_interest` are the max and
  the integer mean (`sum // events`) of that value over all processed events.
* `checksum` in `symbols` is `sum(price * remaining)` over the live orders at
  the end of the replay.
* The **audit digest** is `sha256("\n".join(audit_lines)).hexdigest()[:16]`
  where one line is appended per processed event, after the event has been
  applied:

  ```
  ts,seq,type,order_id,status,checksum,resting,net
  ```

  with `status` one of `rested`, `partial`, `filled`, `cancelled`,
  `rejected_duplicate`, `rejected_unknown`, `rejected_unknown_symbol`;
  `checksum` = `sum(price * remaining)` over **all** live orders in all
  books at that moment; `resting` = number of live orders; `net` =
  `SYMBOL:qty` for each symbol in sorted order, `qty` being the current open
  interest (long side) of that symbol, joined with `;`.

Any implementation that produces the same digest, the same trades and the
same final state is accepted, however it is structured.

## Definition of done

* `python3 -m unittest discover -s tests -v` passes.
* For every hidden event log, the output document is byte-identical to the
  pristine engine's output.
* Large-profile median wall time is at least 3x lower than the pristine
  engine's, ideally 5x+.
* Peak memory does not regress meaningfully.
