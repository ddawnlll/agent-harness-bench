"""Deterministic synthetic event generation plus JSONL loading/writing.

An *event* is a plain dict:

    submit: {"ts": int, "seq": int, "type": "submit", "order_id": int,
             "account": str, "symbol": str, "side": "buy"|"sell",
             "price": int, "qty": int}
    cancel: {"ts": int, "seq": int, "type": "cancel", "order_id": int}

``ts`` is a logical timestamp in microseconds (many events may share it),
``seq`` is a strictly increasing sequence number and breaks ties, so the
canonical processing order is ``sorted(events, key=lambda e: (e["ts"], e["seq"]))``.

Generation is fully deterministic for a given seed: the same seed always
produces byte-identical JSONL.  No wall clock and no OS entropy are used.
"""

import json
import random

DEFAULT_SYMBOLS = ("AAA", "BBB", "CCC")
DEFAULT_QTY_CHOICES = (10, 20, 25, 50, 100)

EVENT_FIELDS = {
    "submit": ("ts", "seq", "type", "order_id", "account", "symbol", "side",
               "price", "qty"),
    "cancel": ("ts", "seq", "type", "order_id"),
}


class EventError(ValueError):
    """Raised when an event stream is malformed."""


PROFILES = {
    # Visible fixtures.  Small enough to commit to git.
    "small": dict(
        n=600, symbols=DEFAULT_SYMBOLS, accounts=6, target_live=25,
        tick=5, spread=6, aggressive_ratio=0.30, cancel_bias=0.30,
        dup_ts_prob=0.05, dup_id_prob=0.0, qty_choices=(10, 25, 50),
    ),
    "medium": dict(
        n=4000, symbols=DEFAULT_SYMBOLS, accounts=12, target_live=90,
        tick=5, spread=8, aggressive_ratio=0.35, cancel_bias=0.32,
        dup_ts_prob=0.10, dup_id_prob=0.0,
    ),
    "adversarial": dict(
        # Deep book, one-tick spread, lots of shared timestamps, huge and tiny
        # quantities (heavy partial fills), duplicate ids and cancels of dead
        # orders.
        n=1600, symbols=DEFAULT_SYMBOLS, accounts=10, target_live=400,
        tick=1, spread=400, aggressive_ratio=0.40, cancel_bias=0.45,
        dup_ts_prob=0.65, dup_id_prob=0.10,
        qty_choices=(1, 3, 7, 13, 1000),
    ),
    "large": dict(
        n=32000, symbols=DEFAULT_SYMBOLS, accounts=72, target_live=1200,
        tick=5, spread=8, aggressive_ratio=0.35, cancel_bias=0.30,
        dup_ts_prob=0.10, dup_id_prob=0.0,
    ),
}


def profile_kwargs(name):
    if name not in PROFILES:
        raise KeyError("unknown profile %r (have: %s)"
                       % (name, ", ".join(sorted(PROFILES))))
    kwargs = dict(PROFILES[name])
    kwargs.pop("n", None)
    return kwargs


def generate_events(seed, n, symbols=DEFAULT_SYMBOLS, accounts=8,
                    target_live=40, tick=5, spread=8, aggressive_ratio=0.3,
                    cancel_bias=0.3, dup_ts_prob=0.0, dup_id_prob=0.0,
                    qty_choices=DEFAULT_QTY_CHOICES, base_price=100000):
    """Generate ``n`` deterministic events.

    The generator keeps a working set of roughly ``target_live`` live order
    ids so that the book depth stays realistic instead of growing without
    bound.  Orders that were already filled or cancelled may still be
    cancelled later (the replay rejects those), which is intentional.
    """
    rng = random.Random(seed)
    symbols = tuple(symbols)
    account_ids = ["acct%02d" % i for i in range(accounts)]
    qty_choices = tuple(qty_choices)

    # Each symbol has an independent mid price that performs a small random
    # walk; orders are quoted around it.
    mid = {sym: base_price + 137 * i for i, sym in enumerate(symbols)}

    events = []
    live = []          # ids we believe are resting: (order_id, account, symbol)
    ts = 1000000
    seq = 0
    next_id = 1
    issued = []

    for _ in range(n):
        # Mostly duplicated timestamps: clocks are coarse compared to the
        # event rate.
        if rng.random() < dup_ts_prob:
            step = 0
        else:
            step = rng.choice((0, 1, 1, 2, 3, 7))
        ts += step
        seq += 1

        do_cancel = False
        if live and len(live) > target_live:
            do_cancel = rng.random() < 0.80
        elif len(live) < max(1, target_live // 2):
            do_cancel = False
        else:
            do_cancel = rng.random() < cancel_bias

        if do_cancel:
            idx = rng.randrange(len(live))
            order_id, _account, _symbol = live.pop(idx)
            events.append({"ts": ts, "seq": seq, "type": "cancel",
                           "order_id": order_id})
            continue

        sym = symbols[rng.randrange(len(symbols))]
        account = account_ids[rng.randrange(len(account_ids))]
        side = "buy" if rng.random() < 0.5 else "sell"
        money = mid[sym]
        if rng.random() < aggressive_ratio:
            # Marketable order: crosses the current mid by one tick.
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
        live.append((order_id, account, sym))

        # Small random walk of the reference price.
        money = mid[sym] + rng.choice((-tick, 0, 0, tick))
        if money < tick * 10:
            money = tick * 10
        mid[sym] = money

    return events


def dumps_event(event):
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def write_events(path, events):
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(dumps_event(event))
            handle.write("\n")


def validate_event(event, index):
    if not isinstance(event, dict):
        raise EventError("event %d is not an object" % index)
    etype = event.get("type")
    if etype not in EVENT_FIELDS:
        raise EventError("event %d has unknown type %r" % (index, etype))
    for field in EVENT_FIELDS[etype]:
        if field not in event:
            raise EventError("event %d (%s) missing field %r"
                             % (index, etype, field))
    for field in ("ts", "seq", "order_id"):
        if not isinstance(event[field], int):
            raise EventError("event %d field %r must be int" % (index, field))
    if etype == "submit":
        if event["side"] not in ("buy", "sell"):
            raise EventError("event %d has bad side %r"
                             % (index, event["side"]))
        for field in ("price", "qty"):
            if not isinstance(event[field], int) or event[field] <= 0:
                raise EventError("event %d field %r must be a positive int"
                                 % (index, field))
        for field in ("account", "symbol"):
            if not isinstance(event[field], str):
                raise EventError("event %d field %r must be str"
                                 % (index, field))
    return event


def load_events(path):
    """Load a JSONL event file and return events in canonical order."""
    events = []
    with open(path, "r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError as exc:
                raise EventError("%s:%d: invalid JSON (%s)"
                                 % (path, lineno, exc))
            events.append(validate_event(event, lineno))
    events.sort(key=lambda e: (e["ts"], e["seq"]))
    return events


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate deterministic synthetic market events.")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--profile", default=None,
                        help="one of: %s" % ", ".join(sorted(PROFILES)))
    parser.add_argument("--n", type=int, default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if args.profile is None and args.n is None:
        parser.error("pass --profile or --n")
    kwargs = profile_kwargs(args.profile) if args.profile else {}
    n = args.n if args.n is not None else PROFILES[args.profile]["n"]
    events = generate_events(args.seed, n, **kwargs)
    write_events(args.out, events)
    print("wrote %d events to %s" % (len(events), args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
