from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any


class LiveStateStore:
    def __init__(self, persist_path: Path) -> None:
        self._persist_path = persist_path
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {
            "status": "booting",
            "meta": {},
            "kpis": {},
            "series": [],
            "taxis": [],
            "recentTrips": [],
            "generatedAt": "",
        }

    def replace(self, state: dict[str, Any]) -> None:
        payload = copy.deepcopy(state)
        with self._lock:
            self._state = payload
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

