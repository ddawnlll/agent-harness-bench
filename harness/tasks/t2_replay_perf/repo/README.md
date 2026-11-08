# replay — deterministic market-event replay

A small, dependency-free engine that replays a historical market-event log:
limit order submissions and cancellations, price-time priority matching,
positions, cash, fees, PnL, order lifecycle counters and a per-event audit
digest. Output is a single canonical JSON document; the same input always
produces byte-identical output on any machine.

See `TASK.md` for the optimisation task and the full output contract.

## Layout

```
replay/events.py   deterministic synthetic generator + JSONL load/write
replay/book.py     single-symbol price-level order book
replay/engine.py   matching, positions/PnL, lifecycle counters, audit digest
replay/cli.py      python3 -m replay.cli --events FILE --out FILE
data/              committed fixtures (small, medium, adversarial_small)
tools/             gen_fixtures.py — regenerates the fixtures deterministically
tests/             public unittest suite + golden output for the small fixture
```

## Run the tests

```bash
python3 -m unittest discover -s tests -v
```

## Replay an event log

```bash
python3 -m replay.cli --events data/small.jsonl --out /tmp/small.json
```

The CLI prints a one-line summary and writes the canonical result document.
An invalid event stream exits with status 2 and writes nothing.

## Generate workloads

All generator parameters are driven by a seed, so a workload is reproducible
anywhere:

```bash
python3 -m replay.events --seed 4242 --profile large --out /tmp/large.jsonl
python3 -m replay.events --seed 7 --profile adversarial --out /tmp/adv.jsonl
python3 -m replay.events --seed 1 --n 50000 --out /tmp/custom.jsonl
```

Profiles (`small`, `medium`, `adversarial`, `large`) only change generation
parameters: event count, number of accounts, target book depth, tick size,
aggressiveness, duplicate timestamp/id rates and quantity distribution.

## Benchmark

```bash
python3 -m replay.events --seed 4242 --profile large --out /tmp/large.jsonl
for i in 1 2 3; do
  /usr/bin/time -f "%e s %M KB" python3 -m replay.cli \
      --events /tmp/large.jsonl --out /tmp/large.out.json
done
```

On the reference machine the unoptimised engine takes roughly 8 seconds for
the `large` profile.

## Regenerate fixtures

```bash
python3 tools/gen_fixtures.py
```

This rewrites `data/*.jsonl` and `tests/golden/small.json` deterministically.
The fixtures are committed so tests do not depend on generation at test time.
