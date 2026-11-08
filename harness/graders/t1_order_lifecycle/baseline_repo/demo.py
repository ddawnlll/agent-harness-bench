"""Run a JSONL scenario and print a deterministic summary."""
import json
import sys

from v9 import Engine, RecordingBroker, parse_events_file


def main(path: str) -> int:
    events = parse_events_file(path)
    engine = Engine(RecordingBroker())
    engine.run(events)
    summary = {
        "halted": engine.halted,
        "halt_reason": engine.halt_reason,
        "positions": {k: {"qty": v.qty, "avg_price": v.avg_price} for k, v in engine.positions.items()},
        "protected": {s: engine.is_protected(s) for s in engine.positions},
        "audit_digest": engine.audit.digest(),
        "records": len(engine.audit.records),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python3 demo.py <scenario.jsonl>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
