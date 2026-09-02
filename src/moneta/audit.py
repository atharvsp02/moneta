from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AuditLog:
    run_id: str
    events: list[dict] = field(default_factory=list)

    def record(self, event: str, **payload) -> dict:
        entry = {"ts": _now(), "run_id": self.run_id, "event": event, **payload}
        self.events.append(entry)
        return entry

    def write_jsonl(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for entry in self.events:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return path

    def counts(self) -> dict:
        out: dict[str, int] = {}
        for entry in self.events:
            out[entry["event"]] = out.get(entry["event"], 0) + 1
        return out
