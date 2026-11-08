"""Command line entry point: python3 -m replay.cli --events FILE --out FILE."""

import argparse
import json
import sys

from .engine import Engine
from .events import EventError, load_events


def dump_result(result, path):
    text = json.dumps(result, sort_keys=True, separators=(",", ":"))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.write("\n")


def replay(events_path, out_path):
    events = load_events(events_path)
    symbols = sorted({event["symbol"] for event in events
                      if event["type"] == "submit"})
    if not symbols:
        symbols = []
    engine = Engine(symbols)
    engine.process(events)
    result = engine.result()
    dump_result(result, out_path)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="replay.cli",
        description="Replay a historical market-event log and write the "
                    "canonical result document.")
    parser.add_argument("--events", required=True,
                        help="input JSONL event file")
    parser.add_argument("--out", required=True,
                        help="output JSON result file")
    args = parser.parse_args(argv)

    try:
        result = replay(args.events, args.out)
    except EventError as exc:
        print("invalid event stream: %s" % exc, file=sys.stderr)
        return 2
    print("replayed %d events, %d trades, digest %s"
          % (result["event_count"], result["trade_count"],
             result["audit"]["digest"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
