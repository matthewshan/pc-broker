"""In-memory event log for operational events."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any

_MAX_EVENTS = 200

_events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)


def add_event(kind: str, message: str, **extra: Any) -> None:
    _events.appendleft(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "message": message,
            **extra,
        }
    )


def get_events(limit: int = 50) -> list[dict[str, Any]]:
    return list(_events)[:limit]
