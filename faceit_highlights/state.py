"""Remembers which matches were already processed so each one is only rendered once."""

from __future__ import annotations

import json
import time
from pathlib import Path

DONE = "done"
DETECTED = "detected"  # Highlights found, rendering turned off.
FAILED = "failed"
WAITING = "waiting_for_demo"
MAX_ATTEMPTS = 3


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict] = json.loads(path.read_text("utf-8")) if path.exists() else {}

    def get(self, key: str) -> dict:
        return self.data.get(key, {})

    def should_process(self, key: str) -> bool:
        entry = self.get(key)
        if entry.get("status") in (DONE, DETECTED):
            return False
        return not (entry.get("status") == FAILED and entry.get("attempts", 0) >= MAX_ATTEMPTS)

    def update(self, key: str, **fields) -> None:
        entry = self.data.setdefault(key, {})
        entry.update(fields, updated=time.strftime("%Y-%m-%d %H:%M:%S"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), "utf-8")
        tmp.replace(self.path)
