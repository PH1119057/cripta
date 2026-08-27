from __future__ import annotations

import csv
from pathlib import Path

from bybit_workbench.research.confirmatory_holdout_p46 import (
    CANDIDATES,
    DEFAULT_SYMBOLS,
    HOLDOUT_END,
    HOLDOUT_START,
    P44_FEATURE,
    P451_FEATURE,
    _load_threshold_column,
)


def test_holdout_is_30_days_and_after_discovery() -> None:
    assert (HOLDOUT_END - HOLDOUT_START).days == 30
    assert HOLDOUT_START.isoformat() == "2026-08-19T00:00:00+00:00"


def test_candidate_set_is_preregistered() -> None:
    assert [item.name for item in CANDIDATES] == [
        "cooldown_60m",
        "p44_residual_q1",
        "zone_approach_slope_q1",
        "zone_second_retest",
        "zone_fourth_plus_retest",
    ]
    assert len(DEFAULT_SYMBOLS) == 9


def test_threshold_loader_selects_named_feature(tmp_path: Path) -> None:
    path = tmp_path / "thresholds.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "feature", "q25"])
        writer.writeheader()
        writer.writerow({"symbol": "UNIUSDT", "feature": P44_FEATURE, "q25": "-0.1"})
        writer.writerow({"symbol": "UNIUSDT", "feature": P451_FEATURE, "q25": "-0.2"})
    assert _load_threshold_column(path, feature=P44_FEATURE) == {"UNIUSDT": -0.1}
    assert _load_threshold_column(path, feature=P451_FEATURE) == {"UNIUSDT": -0.2}
