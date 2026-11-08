"""Event parsing and validation for deterministic replay scenarios."""
from __future__ import annotations

import json
from typing import Any


class EventError(ValueError):
    """Raised when a scenario stream is malformed."""


REQUIRED_FIELDS = {
    "SUBMIT_ENTRY": ("order_id", "symbol", "side", "qty", "price"),
    "ENTRY_ACK": ("order_id", "accepted"),
    "FILL": ("order_id", "fill_id", "qty", "price"),
    "CANCEL_REQUEST": ("order_id",),
    "CANCEL_RESULT": ("order_id", "accepted"),
    "STOP_ACK": ("client_stop_id", "accepted"),
    "STOP_CALLBACK": ("client_stop_id", "version", "state"),
    "RESTART": (),
    "END": (),
}


def validate_event(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise EventError(f"event must be an object, got {type(raw).__name__}")
    etype = raw.get("type")
    if etype not in REQUIRED_FIELDS:
        raise EventError(f"unknown event type: {etype!r}")
    if not isinstance(raw.get("seq"), int):
        raise EventError(f"event {etype} missing integer seq")
    for field in REQUIRED_FIELDS[etype]:
        if field not in raw:
            raise EventError(f"event {etype} missing field {field!r}")
    if etype == "SUBMIT_ENTRY":
        if raw["side"] not in ("BUY", "SELL"):
            raise EventError("side must be BUY or SELL")
        if not isinstance(raw["qty"], (int, float)) or raw["qty"] <= 0:
            raise EventError("qty must be positive")
    if etype == "FILL":
        if not isinstance(raw["qty"], (int, float)) or raw["qty"] <= 0:
            raise EventError("fill qty must be positive")
    return raw


def parse_events(text: str) -> list[dict]:
    """Parse JSONL text into a validated, seq-ordered event list."""
    out: list[dict] = []
    last_seq = -1
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EventError(f"line {lineno}: invalid JSON: {exc}") from exc
        ev = validate_event(raw)
        if ev["seq"] <= last_seq:
            raise EventError(f"line {lineno}: seq must increase ({ev['seq']} after {last_seq})")
        last_seq = ev["seq"]
        out.append(ev)
    return out


def parse_events_file(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        return parse_events(fh.read())
