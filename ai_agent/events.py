"""Typed events emitted while an Agent run is executing."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

EventKind = Literal[
    "run_started",
    "route_selected",
    "tool_started",
    "tool_finished",
    "observation",
    "final",
    "error",
]


@dataclass(frozen=True)
class AgentEvent:
    """A small, transport-neutral event suitable for CLI and Web rendering."""

    kind: EventKind
    payload: dict[str, Any]
    session_id: str | None = None
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    sequence: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "payload": self.payload,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "timestamp": self.timestamp,
        }
