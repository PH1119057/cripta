from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(slots=True)
class MinuteContinuityTracker:
    start_minute: datetime | None = None
    last_minute: datetime | None = None
    expected_snapshots: int = 0
    actual_snapshots: int = 0
    missing_snapshots: int = 0
    max_gap_minutes: int = 0

    def advance(self, minute: datetime) -> dict[str, object]:
        if self.last_minute is not None and minute < self.last_minute:
            raise ValueError("continuity minute cannot move backwards")
        previous = self.last_minute
        gap_started_at: datetime | None = None
        gap_ended_at: datetime | None = None
        if previous is None:
            self.start_minute = minute
            delta_minutes = 1
        else:
            delta_minutes = max(1, int((minute - previous).total_seconds() // 60))
            if delta_minutes > 1:
                gap_started_at = previous + timedelta(minutes=1)
                gap_ended_at = minute - timedelta(minutes=1)
        self.expected_snapshots += delta_minutes
        self.actual_snapshots += 1
        self.missing_snapshots += max(0, delta_minutes - 1)
        self.max_gap_minutes = max(self.max_gap_minutes, delta_minutes)
        self.last_minute = minute
        coverage = (
            self.actual_snapshots / self.expected_snapshots
            if self.expected_snapshots
            else 1.0
        )
        return {
            "scope_started_at": (
                self.start_minute.isoformat() if self.start_minute else None
            ),
            "regular_minute": minute.isoformat(),
            "expected_snapshots": self.expected_snapshots,
            "actual_snapshots": self.actual_snapshots,
            "missing_snapshots": self.missing_snapshots,
            "max_gap_minutes": self.max_gap_minutes,
            "last_gap_minutes": delta_minutes,
            "gap_started_at": gap_started_at.isoformat() if gap_started_at else None,
            "gap_ended_at": gap_ended_at.isoformat() if gap_ended_at else None,
            "gap_missing_minutes": max(0, delta_minutes - 1),
            "coverage_pct": round(coverage * 100, 6),
        }
