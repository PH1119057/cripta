from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from bybit_workbench.mayak.research.component_resource_smoke import run


def _write(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def _inputs(tmp_path: Path, *, missing_spot_1m: bool = False) -> dict[str, Path]:
    keys = ("k1", "k2")
    base = tmp_path / "base.csv"
    base_fields = [
        "signal_key",
        "symbol",
        "direction",
        "plus_010_vs_minus_100",
        "plus_110_vs_minus_100",
        "derivatives_15m_large_trade_share",
        "relative_60m_panel_median_return_pct",
        "market_median_return_5m_pct",
        "derivatives_15m_acceleration_usd_per_min2",
        "derivatives_15m_net_usd",
    ]
    base_rows = []
    for index, key in enumerate(keys):
        base_rows.append(
            {
                "signal_key": key,
                "symbol": "AAVEUSDT",
                "direction": "Long" if index == 0 else "Short",
                "plus_010_vs_minus_100": "reached_plus_0p10_before_minus_1p00",
                "plus_110_vs_minus_100": "reached_plus_1p10",
                "derivatives_15m_large_trade_share": "0.2",
                "relative_60m_panel_median_return_pct": "0.1",
                "market_median_return_5m_pct": "0.1",
                "derivatives_15m_acceleration_usd_per_min2": "1.0",
                "derivatives_15m_net_usd": "1000",
            }
        )
    _write(base, base_fields, base_rows)

    positioning = tmp_path / "positioning.csv"
    _write(
        positioning,
        ["signal_key", "positioning_open_interest_acceleration_5m_pct_per_min2"],
        [
            {
                "signal_key": key,
                "positioning_open_interest_acceleration_5m_pct_per_min2": "0.01",
            }
            for key in keys
        ],
    )
    basis = tmp_path / "basis.csv"
    _write(
        basis,
        ["signal_key", "basis_mark_index_premium_pct"],
        [{"signal_key": key, "basis_mark_index_premium_pct": "0.02"} for key in keys],
    )
    spot = tmp_path / "spot.csv"
    _write(
        spot,
        ["signal_key", "spot_60m_return_pct", "spot_1m_net_usd"],
        [
            {
                "signal_key": key,
                "spot_60m_return_pct": "0.3",
                "spot_1m_net_usd": "" if missing_spot_1m else "500",
            }
            for key in keys
        ],
    )
    liquidity = tmp_path / "liquidity.csv"
    _write(
        liquidity,
        [
            "signal_key",
            "liquidity_bid_usd",
            "liquidity_ask_usd",
            "liquidity_bid_depth_10bps_usd",
            "liquidity_ask_depth_10bps_usd",
            "liquidity_ask_change_5m_pct",
        ],
        [
            {
                "signal_key": key,
                "liquidity_bid_usd": "1000",
                "liquidity_ask_usd": "1000",
                "liquidity_bid_depth_10bps_usd": "300",
                "liquidity_ask_depth_10bps_usd": "200",
                "liquidity_ask_change_5m_pct": "2.0",
            }
            for key in keys
        ],
    )
    ratio = tmp_path / "ratio.csv"
    _write(
        ratio,
        ["signal_key", "positioning_long_short_imbalance"],
        [{"signal_key": key, "positioning_long_short_imbalance": "0.1"} for key in keys],
    )
    replay = tmp_path / "replay.json"
    replay.write_text(
        json.dumps(
            {
                "selected_signal_count": 2,
                "outcomes_used_by_replay": False,
                "project_commit": "a" * 40,
                "symbols": ["AAVEUSDT", "BTCUSDT", "ETHUSDT"],
            }
        ),
        encoding="utf-8",
    )
    return {
        "input_csv": base,
        "replay_manifest": replay,
        "positioning_csv": positioning,
        "basis_csv": basis,
        "spot_csv": spot,
        "liquidity_csv": liquidity,
        "account_ratio_csv": ratio,
    }


def test_resource_smoke_checks_all_frozen_features_without_oos_verdict(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    out = tmp_path / "out"
    manifest = run(
        **inputs,
        output_dir=out,
        source_commit="b" * 40,
        expected_symbols=("AAVEUSDT",),
        expected_signals=2,
    )
    assert manifest["status"] == "PASS"
    assert manifest["candidate_count"] == 12
    assert manifest["coverage_failures"] == []
    assert manifest["oos_verdict_produced"] is False
    assert manifest["threshold_selection"] is False
    assert manifest["retuning"] is False
    assert manifest["coin_market_rating_fitted"] is False
    assert manifest["trading_effect"] == "NONE"
    with (out / "RESOURCE_SMOKE_FEATURE_COVERAGE.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    assert len(rows) == 12
    assert {row["coverage_gate"] for row in rows} == {"PASS"}
    assert all("verdict" not in row for row in rows)


def test_resource_smoke_fails_closed_on_candidate_coverage(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path, missing_spot_1m=True)
    out = tmp_path / "out"
    with pytest.raises(RuntimeError, match="spot_1m_net_usd"):
        run(
            **inputs,
            output_dir=out,
            source_commit="b" * 40,
            expected_symbols=("AAVEUSDT",),
            expected_signals=2,
        )
    manifest = json.loads((out / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "FAIL"
    assert "spot_1m_net_usd" in manifest["coverage_failures"]
    assert manifest["oos_verdict_produced"] is False
