from __future__ import annotations

import json
from pathlib import Path

from bybit_workbench.research.orderbook_full_v9 import (
    _outcome_counts,
    build_binary_state_rows,
    build_quartile_rows,
    enrich_row,
    resolve_output_dir,
)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "first_0_5_vs_1_0": "favorable_first",
        "first_1_0_vs_1_0": "favorable_first",
        "support_wall_distance_bps_p0s": 10.0,
        "adverse_wall_distance_bps_p0s": 20.0,
        "support_wall_notional_p0s": 200.0,
        "adverse_wall_notional_p0s": 100.0,
        "directional_imbalance_5bps_p0s": 0.2,
        "directional_imbalance_5bps_change_m30_to_touch": 0.1,
        "directional_imbalance_10bps_p0s": 0.1,
        "directional_imbalance_25bps_p0s": 0.1,
        "directional_imbalance_50bps_p0s": 0.1,
        "directional_imbalance_10bps_change_m30_to_touch": 0.1,
        "directional_imbalance_25bps_change_m30_to_touch": 0.1,
        "directional_imbalance_50bps_change_m30_to_touch": 0.1,
        "support_wall_notional_ratio_m30_to_touch": 1.1,
        "spread_bps_p0s": 2.0,
        "spread_change_m30_to_touch_bps": -0.1,
    }
    row.update(overrides)
    return row


def test_enrich_row_builds_physical_wall_and_imbalance_states() -> None:
    row = enrich_row(_row())
    assert row["support_wall_closer"] == "true"
    assert row["support_wall_larger"] == "true"
    assert row["both_wall_advantages"] == "true"
    assert row["near_imbalance_positive"] == "true"
    assert row["near_imbalance_improving"] == "true"
    assert row["support_wall_distance_advantage_bps_p0s"] == 10.0
    assert row["support_wall_notional_ratio_to_adverse_p0s"] == 2.0


def test_enrich_row_marks_adverse_book_as_false() -> None:
    row = enrich_row(
        _row(
            support_wall_distance_bps_p0s=30.0,
            adverse_wall_distance_bps_p0s=10.0,
            support_wall_notional_p0s=50.0,
            adverse_wall_notional_p0s=100.0,
            directional_imbalance_5bps_p0s=-0.2,
            directional_imbalance_5bps_change_m30_to_touch=-0.1,
        )
    )
    assert row["both_wall_advantages"] == "false"
    assert row["near_imbalance_positive_or_improving"] == "false"


def test_outcome_counts_keeps_neither_separate() -> None:
    rows = [
        _row(),
        _row(first_0_5_vs_1_0="adverse_first"),
        _row(first_0_5_vs_1_0="neither"),
    ]
    stats = _outcome_counts(rows, "first_0_5_vs_1_0")
    assert stats["count"] == 3
    assert stats["favorable"] == 1
    assert stats["adverse"] == 1
    assert stats["neither"] == 1
    assert stats["decisive_favorable_percent"] == 50.0


def test_binary_state_report_compares_true_and_false() -> None:
    good = enrich_row(_row())
    bad = enrich_row(
        _row(
            first_1_0_vs_1_0="adverse_first",
            support_wall_distance_bps_p0s=30.0,
            adverse_wall_distance_bps_p0s=10.0,
        )
    )
    report = build_binary_state_rows([good, bad])
    closer_true = next(
        row for row in report if row["state"] == "support_wall_closer" and row["value"] == "true"
    )
    assert closer_true["first_1_0_vs_1_0_favorable_percent"] == 100.0


def test_quartile_report_preserves_all_four_buckets() -> None:
    rows = []
    for index in range(8):
        rows.append(
            enrich_row(
                _row(
                    directional_imbalance_5bps_p0s=float(index),
                    first_1_0_vs_1_0="favorable_first" if index >= 4 else "adverse_first",
                )
            )
        )
    report = build_quartile_rows(rows)
    feature_rows = [
        row for row in report if row["feature"] == "directional_imbalance_5bps_p0s"
    ]
    assert {row["quartile"] for row in feature_rows} == {"Q1", "Q2", "Q3", "Q4"}


def test_resolve_output_dir_reuses_incomplete_run(tmp_path: Path) -> None:
    p37 = tmp_path / "p37"
    p37.mkdir()
    run = tmp_path / "reports" / "entry_research_v12" / "UNIUSDT_20260816_000000"
    run.mkdir(parents=True)
    (run / "run_state.json").write_text(
        json.dumps({"complete": False, "p37_dir": str(p37)}), encoding="utf-8"
    )
    assert resolve_output_dir(tmp_path, "UNIUSDT", p37, None) == run
