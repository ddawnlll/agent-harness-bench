"""V9 execution-safety core: deterministic order lifecycle, position and
protective-stop state machines with an append-only audit trail.

This package performs no I/O and no network access. Every exchange
interaction is expressed as an event applied to :class:`v9.engine.Engine`.
"""
from .engine import Engine  # noqa: F401
from .events import parse_events, parse_events_file, EventError  # noqa: F401
from .gateway import RecordingBroker  # noqa: F401
