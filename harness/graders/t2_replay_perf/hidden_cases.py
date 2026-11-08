"""Hidden workloads for the t2_replay_perf benchmark.

This module is intentionally independent of the candidate repository: it
regenerates event streams from seeded parameters that only the grader knows.
Nothing here is imported from the workspace under test, so tampering with
``replay/events.py`` cannot influence the hidden inputs.

The generated event schema is the documented one:

    submit: ts, seq, type, order_id, account, symbol, side, price, qty
    cancel: ts, seq, type, order_id
"""

import json
import random

# Parameters are tuned on the reference machine so that the pristine engine
# needs roughly: small ~0.03s, large ~8s, adversarial ~2s, fresh ~0.05s.
PROFILES = {
    "h_small": dict(
        n=700, symbols=("AAA", "BBB", "CCC"), accounts=6, target_live=40,
        tick=5, spread=8, aggressive_ratio=0.35, cancel_bias=0.35,
        dup_ts_prob=0.05, dup_id_prob=0.0, qty_choices=(10, 25, 50, 100),
        base_price=100000,
    ),
    "h_large": dict(
        n=32000, symbols=("AAA", "BBB", "CCC"), accounts=72, target_live=1200,
        tick=5, spread=8, aggressive_ratio=0.35, cancel_bias=0.30,
        dup_ts_prob=0.10, dup_id_prob=0.0,
        qty_choices=(10, 20, 25, 50, 100), base_price=250000,
    ),
    "h_adversarial": dict(
        n=10000, symbols=("AAA", "BBB", "CCC"), accounts=10, target_live=1200,
        tick=1, spread=300, aggressive_ratio=0.45, cancel_bias=0.45,
        dup_ts_prob=0.70, dup_id_prob=0.12,
        qty_choices=(1, 3, 7, 13, 1000), base_price=50000,
    ),
    "h_fresh": dict(
        n=900, symbols=("AAA", "BBB", "CCC"), accounts=8, target_live=60,
        tick=3, spread=12, aggressive_ratio=0.40, cancel_bias=0.40,
        dup_ts_prob=0.30, dup_id_prob=0.05,
        qty_choices=(5, 11, 40, 250), base_price=75000,
    ),
}

# Fixed seeds: the grader is deterministic and reproducible.  The candidate
# has never seen these workloads (they are not committed to the repo).
SEEDS = {
    "h_small": 9001001,
    "h_large": 9001002,
    "h_adversarial": 9001003,
    "h_fresh": 9001004,
}

PROFILE_ORDER = ("h_small", "h_large", "h_adversarial", "h_fresh")


def generate_events(seed, n, symbols=("AAA", "BBB", "CCC"), accounts=8,
                    target_live=40, tick=5, spread=8, aggressive_ratio=0.35,
                    cancel_bias=0.35, dup_ts_prob=0.0, dup_id_prob=0.0,
                    qty_choices=(10, 25, 50), base_price=100000):
    rng = random.Random(seed)
    symbols = tuple(symbols)
    account_ids = ["acct%02d" % i for i in range(accounts)]
    qty_choices = tuple(qty_choices)
    mid = {sym: base_price + 137 * i for i, sym in enumerate(symbols)}

    events = []
    live = []
    ts = 1000000
    seq = 0
    next_id = 1
    issued = []

    for _ in range(n):
        if rng.random() < dup_ts_prob:
            ts += 0
        else:
            ts += rng.choice((0, 1, 1, 2, 3, 7))
        seq += 1

        if live and len(live) > target_live:
            do_cancel = rng.random() < 0.80
        elif len(live) < max(1, target_live // 2):
            do_cancel = False
        else:
            do_cancel = rng.random() < cancel_bias

        if do_cancel:
            order_id = live.pop(rng.randrange(len(live)))
            events.append({"ts": ts, "seq": seq, "type": "cancel",
                           "order_id": order_id})
            continue

        sym = symbols[rng.randrange(len(symbols))]
        account = account_ids[rng.randrange(len(account_ids))]
        side = "buy" if rng.random() < 0.5 else "sell"
        money = mid[sym]
        if rng.random() < aggressive_ratio:
            price = money + tick if side == "buy" else money - tick
        else:
            offset = tick * (1 + rng.randrange(spread))
            price = money - offset if side == "buy" else money + offset
        if price <= 0:
            price = tick
        qty = qty_choices[rng.randrange(len(qty_choices))]

        if rng.random() < dup_id_prob and issued:
            order_id = issued[rng.randrange(len(issued))]
        else:
            order_id = next_id
            next_id += 1
            issued.append(order_id)

        events.append({
            "ts": ts, "seq": seq, "type": "submit", "order_id": order_id,
            "account": account, "symbol": sym, "side": side,
            "price": price, "qty": qty,
        })
        live.append(order_id)

        money = mid[sym] + rng.choice((-tick, 0, 0, tick))
        if money < tick * 10:
            money = tick * 10
        mid[sym] = money

    return events


def build_profile(name):
    if name not in PROFILES:
        raise KeyError(name)
    kwargs = dict(PROFILES[name])
    n = kwargs.pop("n")
    return generate_events(SEEDS[name], n, **kwargs)


def write_events(path, events):
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True,
                                    separators=(",", ":")))
            handle.write("\n")
