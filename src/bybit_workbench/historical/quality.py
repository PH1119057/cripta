from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .validation import HistoricalDataset


@dataclass(frozen=True, slots=True)
class HistoricalGap:
    previous_closed_at: datetime
    next_opened_at: datetime

    @property
    def duration_seconds(self) -> float:
        return (self.next_opened_at - self.previous_closed_at).total_seconds()


@dataclass(frozen=True, slots=True)
class HistoricalQualityReport:
    candle_count: int
    gaps: tuple[HistoricalGap, ...]

    @property
    def is_contiguous(self) -> bool:
        return not self.gaps


def inspect_continuity(dataset: HistoricalDataset) -> HistoricalQualityReport:
    gaps = tuple(
        HistoricalGap(previous.closed_at, current.opened_at)
        for previous, current in zip(
            dataset.candles,
            dataset.candles[1:],
            strict=False,
        )
        if current.opened_at > previous.closed_at
    )
    return HistoricalQualityReport(len(dataset.candles), gaps)


def require_contiguous(dataset: HistoricalDataset) -> HistoricalQualityReport:
    report = inspect_continuity(dataset)
    if report.gaps:
        first = report.gaps[0]
        raise ValueError(
            "historical dataset contains gaps; first gap is "
            f"{first.previous_closed_at.isoformat()} -> {first.next_opened_at.isoformat()}"
        )
    return report
