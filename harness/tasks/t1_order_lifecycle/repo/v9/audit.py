"""Append-only audit trail with canonical hashing.

The audit is the reconstruction source of truth: every state-changing outcome
is recorded as a typed record with a monotonically increasing sequence number.
"""
from __future__ import annotations

import hashlib
import json


class Audit:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def append(self, record_type: str, **fields) -> dict:
        rec = {"seq": len(self.records) + 1, "type": record_type}
        rec.update(fields)
        self.records.append(rec)
        return rec

    def of_type(self, record_type: str) -> list[dict]:
        return [r for r in self.records if r["type"] == record_type]

    def canonical_jsonl(self) -> str:
        return "\n".join(
            json.dumps(r, sort_keys=True, separators=(",", ":")) for r in self.records
        )

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_jsonl().encode("utf-8")).hexdigest()

    def last_snapshot(self) -> dict | None:
        for rec in reversed(self.records):
            if rec["type"] == "STATE_SNAPSHOT":
                return rec["state"]
        return None
