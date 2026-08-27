from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bybit_workbench.research.entry_crowding_v5 import (
    AccountRatioPoint,
    CrowdingResearchConfig,
    RatioSeriesIndex,
    _crowd_majority,
    _directional_change,
    _directional_edge,
    _directional_share,
    _quartile,
    _quartile_bounds,
    _write_json,
)


def _point(minute: int, buy: str, sell: str) -> AccountRatioPoint:
    return AccountRatioPoint(
        timestamp=datetime(2026, 8, 16, 12, minute, tzinfo=UTC),
        buy_ratio=Decimal(buy),
        sell_ratio=Decimal(sell),
    )


def test_config_keeps_crowding_research_diagnostic() -> None:
    config = CrowdingResearchConfig()
    assert config.endpoint == "https://api.bybit.kz"
    assert config.period == "5min"


def test_directional_share_mirrors_long_and_short() -> None:
    point = _point(0, "0.60", "0.40")
    assert _directional_share(point, "Long") == Decimal("60.00")
    assert _directional_share(point, "Short") == Decimal("40.00")


def test_directional_edge_mirrors_long_and_short() -> None:
    point = _point(0, "0.60", "0.40")
    assert _directional_edge(point, "Long") == Decimal("20.00")
    assert _directional_edge(point, "Short") == Decimal("-20.00")


def test_directional_change_uses_only_points_at_or_before_anchor() -> None:
    rows = (_point(0, "0.50", "0.50"), _point(5, "0.55", "0.45"))
    series = RatioSeriesIndex(tuple(item.timestamp for item in rows), rows)
    anchor = rows[1].timestamp + timedelta(seconds=30)
    assert _directional_change(
        series,
        direction="Long",
        anchor_at=anchor,
        window_minutes=5,
    ) == Decimal("5.00")


def test_crowd_majority_is_directional() -> None:
    assert _crowd_majority(Decimal("1")) == "aligned_majority"
    assert _crowd_majority(Decimal("-1")) == "opposed_majority"
    assert _crowd_majority(Decimal("0")) == "balanced"


def test_quartile_assignment() -> None:
    bounds = _quartile_bounds(
        [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5")]
    )
    assert _quartile(Decimal("1"), bounds) == "Q1"
    assert _quartile(Decimal("5"), bounds) == "Q4"


def test_write_json_serializes_path(tmp_path: Path) -> None:
    output = tmp_path / "summary.json"
    source = tmp_path / "dataset"
    _write_json(output, {"dataset_dir": source})
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["dataset_dir"] == str(source)
