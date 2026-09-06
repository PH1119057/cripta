from __future__ import annotations

import csv
from pathlib import Path

import pytest

from bybit_workbench.mayak.research.component_analysis import (
    RESEARCH_LABEL,
    NumericSample,
    analyze,
    auc_higher_is_good,
    load_rows,
    run,
)


def test_auc_handles_ties_and_order() -> None:
    samples = [
        NumericSample(1, False),
        NumericSample(2, False),
        NumericSample(3, True),
        NumericSample(4, True),
    ]
    assert auc_higher_is_good(samples) == pytest.approx(1.0)


def _write_input(path: Path) -> None:
    fields = [
        "signal_key",
        "symbol",
        "direction",
        "plus_010_vs_minus_100",
        "plus_110_vs_minus_100",
        "derivatives_5m_net_usd",
        "derivatives_5m_turnover_usd",
        "relative_5m_relative_to_panel_pct",
        "market_up_share",
        "market_down_share",
    ]
    rows = []
    for i, (symbol, direction, good, raw) in enumerate(
        [
            ("UNIUSDT", "Long", True, 4),
            ("LINKUSDT", "Short", True, -4),
            ("BTCUSDT", "Long", True, 3),
            ("ETHUSDT", "Short", True, -3),
            ("XRPUSDT", "Long", False, -2),
            ("SOLUSDT", "Short", False, 2),
            ("DOGEUSDT", "Long", False, -3),
            ("ADAUSDT", "Short", False, 3),
        ]
    ):
        rows.append(
            {
                "signal_key": str(i),
                "symbol": symbol,
                "direction": direction,
                "plus_010_vs_minus_100": "reached_plus_0p10_before_minus_1p00"
                if good
                else "hit_minus_1p00_before_plus_0p10",
                "plus_110_vs_minus_100": "reached_plus_1p10" if good else "hit_minus_1p00",
                "derivatives_5m_net_usd": str(raw),
                "derivatives_5m_turnover_usd": str(abs(raw) * 10),
                "relative_5m_relative_to_panel_pct": str(raw / 10),
                "market_up_share": "0.8" if direction == "Long" and good else "0.2",
                "market_down_share": "0.8" if direction == "Short" and good else "0.2",
            }
        )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def test_direction_adjusted_feature_is_external_and_separates_synthetic_sample(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.csv"
    _write_input(source)
    rows = load_rows(source)
    summary, _ = analyze(rows)
    target = next(
        row
        for row in summary
        if row["outcome"] == "PLUS_110_VS_MINUS_100"
        and row["scope"] == "ALL9"
        and row["feature"] == "entry_aligned::derivatives_5m_net_usd"
    )
    assert target["auc_higher_is_good"] == pytest.approx(1.0)
    assert target["threshold_selected"] is False
    assert target["research_label"] == RESEARCH_LABEL


def test_manifest_marks_holdout_as_diagnostic_reuse(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    _write_input(source)
    manifest = run(source, tmp_path / "out", "a" * 40)
    assert manifest["signals"] == 8
    assert manifest["project_commit"] == "a" * 40
    assert len(manifest["analysis_code_sha256"]) == 64
    assert manifest["holdout_status"] == "DIAGNOSTIC_REUSE_NOT_FRESH_OOS"
    assert manifest["threshold_selection"] is False
    assert manifest["coin_market_rating_fitted"] is False
