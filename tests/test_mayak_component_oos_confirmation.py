from __future__ import annotations

import csv
from pathlib import Path

from bybit_workbench.mayak.research.component_oos_confirmation import (
    CANDIDATES,
    EXPECTED_SIGNALS,
    FROZEN_SEEN_QUARTILES_SHA256,
    FROZEN_SEEN_SUMMARY_SHA256,
    REFERENCE_SYMBOLS,
    TEST_SYMBOLS,
    Candidate,
    FrozenQuartiles,
    evaluate_candidate,
)
from bybit_workbench.mayak.research.prepare_component_oos_inputs import run as prepare_inputs

ROOT = Path(__file__).parents[1]


def _synthetic_rows(*, good_high: bool) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for asset_index in range(10):
        symbol = f"TEST{asset_index}USDT"
        for index in range(30):
            rows.append(
                {
                    "signal_key": f"{symbol}|g|{index}",
                    "symbol": symbol,
                    "direction": "Long",
                    "plus_010_vs_minus_100": "reached_plus_0p10_before_minus_1p00",
                    "plus_110_vs_minus_100": "reached_plus_1p10",
                    "derivatives_15m_net_usd": str(4 + index / 100 if good_high else index / 100),
                }
            )
        for index in range(30):
            rows.append(
                {
                    "signal_key": f"{symbol}|b|{index}",
                    "symbol": symbol,
                    "direction": "Long",
                    "plus_010_vs_minus_100": "hit_minus_1p00_before_plus_0p10",
                    "plus_110_vs_minus_100": "hit_minus_1p00",
                    "derivatives_15m_net_usd": str(index / 100 if good_high else 4 + index / 100),
                }
            )
    return rows


def test_oos_registry_is_frozen_to_new15_and_twelve_candidates() -> None:
    assert len(TEST_SYMBOLS) == 15
    assert len(set(TEST_SYMBOLS)) == 15
    assert REFERENCE_SYMBOLS == ("BTCUSDT", "ETHUSDT")
    assert EXPECTED_SIGNALS == 14024
    assert len(CANDIDATES) == 12
    assert sum(item.outcome == "PLUS_010_VS_MINUS_100" for item in CANDIDATES) == 6
    assert sum(item.outcome == "PLUS_110_VS_MINUS_100" for item in CANDIDATES) == 6
    assert (
        next(
            item
            for item in CANDIDATES
            if item.feature == "entry_aligned::positioning_long_short_imbalance"
        ).expected_direction
        == "LOWER_IS_GOOD"
    )
    assert (
        FROZEN_SEEN_SUMMARY_SHA256
        == "da6a913ae403939b7136d180a7e3966a5bec0d3139f5d27c00180bc150a9c6ff"
    )
    assert (
        FROZEN_SEEN_QUARTILES_SHA256
        == "238f91d477fda5b04326c0a6dbc42bcda5b24457361054bcdb984cca6cdce9ad"
    )


def test_higher_is_good_can_be_confirmed_without_oos_retuning() -> None:
    candidate = Candidate(
        "PLUS_110_VS_MINUS_100", "derivatives_15m_net_usd", "HIGHER_IS_GOOD"
    )
    result, assets, quartiles = evaluate_candidate(
        _synthetic_rows(good_high=True),
        candidate,
        FrozenQuartiles(q1=1.0, q2=2.0, q3=3.0),
        seen_auc=0.55,
    )
    assert result["verdict"] == "CONFIRMED"
    assert result["directional_auc"] == 1.0
    assert result["same_direction_assets"] == 10
    assert result["frozen_q1_good_rate"] == 0.0
    assert result["frozen_q4_good_rate"] == 1.0
    assert all(row["cuts_source"] == "SEEN_ALL9_FROZEN_NOT_RECOMPUTED" for row in quartiles)
    assert all(row["sign_vs_frozen"] == "EXPECTED" for row in assets)


def test_inverse_candidate_is_confirmed_in_pre_registered_lower_direction() -> None:
    candidate = Candidate(
        "PLUS_110_VS_MINUS_100", "derivatives_15m_net_usd", "LOWER_IS_GOOD"
    )
    result, _, _ = evaluate_candidate(
        _synthetic_rows(good_high=False),
        candidate,
        FrozenQuartiles(q1=1.0, q2=2.0, q3=3.0),
        seen_auc=0.44,
    )
    assert result["raw_auc_higher_is_good"] == 0.0
    assert result["directional_auc"] == 1.0
    assert result["verdict"] == "CONFIRMED"


def test_pre_registered_direction_is_rejected_when_oos_reverses_cleanly() -> None:
    candidate = Candidate(
        "PLUS_110_VS_MINUS_100", "derivatives_15m_net_usd", "HIGHER_IS_GOOD"
    )
    result, _, _ = evaluate_candidate(
        _synthetic_rows(good_high=False),
        candidate,
        FrozenQuartiles(q1=1.0, q2=2.0, q3=3.0),
        seen_auc=0.55,
    )
    assert result["directional_auc"] == 0.0
    assert result["opposite_direction_assets"] == 10
    assert result["verdict"] == "REJECTED"


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_input_adapter_preserves_exact_keys_and_maps_existing_path_semantics(
    tmp_path: Path,
) -> None:
    symbol = "TESTUSDT"
    entry_root = tmp_path / "entry_root"
    floor_root = tmp_path / "floor"
    nofloor_root = tmp_path / "nofloor"
    entry_rows = [
        {
            "symbol": symbol,
            "direction": "Long",
            "entry_at": "2026-05-18T00:00:00+00:00",
            "entry_price": "100",
        },
        {
            "symbol": symbol,
            "direction": "Short",
            "entry_at": "2026-05-18T01:00:00+00:00",
            "entry_price": "101",
        },
    ]
    _write_csv(
        entry_root / "entry" / symbol / "signals.csv",
        ["symbol", "direction", "entry_at", "entry_price"],
        entry_rows,
    )
    path_fields = [
        "symbol",
        "direction",
        "touch_at",
        "original_entry_price",
        "adverse_offset_pct",
        "scenario",
        "fill_status",
        "protection_activation_at",
        "exit_reason",
        "exit_at",
        "trade_window_complete",
    ]
    floor_rows = [
        {
            "symbol": symbol,
            "direction": "Long",
            "touch_at": "2026-05-18T00:00:00+00:00",
            "original_entry_price": "100",
            "adverse_offset_pct": "0.0",
            "scenario": "BASELINE_0P00",
            "fill_status": "filled",
            "protection_activation_at": "2026-05-18T00:02:00+00:00",
            "exit_reason": "positive_floor",
            "exit_at": "2026-05-18T00:03:00+00:00",
            "trade_window_complete": "True",
        },
        {
            "symbol": symbol,
            "direction": "Short",
            "touch_at": "2026-05-18T01:00:00+00:00",
            "original_entry_price": "101",
            "adverse_offset_pct": "0.0",
            "scenario": "BASELINE_0P00",
            "fill_status": "filled",
            "protection_activation_at": "",
            "exit_reason": "initial_stop",
            "exit_at": "2026-05-18T01:04:00+00:00",
            "trade_window_complete": "True",
        },
    ]
    nofloor_rows = [
        {**floor_rows[0], "protection_activation_at": "", "exit_reason": "target"},
        {**floor_rows[1], "exit_reason": "initial_stop"},
    ]
    _write_csv(floor_root / symbol / "events.csv", path_fields, floor_rows)
    _write_csv(nofloor_root / symbol / "events.csv", path_fields, nofloor_rows)

    manifest = prepare_inputs(
        symbols=(symbol,),
        entry_root=entry_root,
        floor_root=floor_root,
        nofloor_root=nofloor_root,
        output_dir=tmp_path / "out",
        source_commit="a" * 40,
    )
    assert manifest["signals"] == 2
    assert manifest["unique_full_keys"] == 2
    assert manifest["unique_symbol_touch"] == 2
    assert manifest["plus010_counts"] == {
        "reached_plus_0p10_before_minus_1p00": 1,
        "hit_minus_1p00_before_plus_0p10": 1,
    }
    assert manifest["plus110_counts"] == {"reached_plus_1p10": 1, "hit_minus_1p00": 1}
    assert manifest["threshold_selection"] is False
    assert manifest["retuning"] is False
    assert manifest["trading_effect"] == "NONE"


def test_oos_contract_is_observation_only_and_registered() -> None:
    body = (ROOT / "docs/MAYAK_COMPONENT_OOS_CONFIRMATION_V1_RU.md").read_text(encoding="utf-8")
    authority = (ROOT / "docs/DOCUMENT_AUTHORITY_RU.md").read_text(encoding="utf-8")
    current_map = (ROOT / "docs/CURRENT_PROJECT_MAP_RU.md").read_text(encoding="utf-8")
    assert "MAYAK_TRADING_EFFECT       = NONE" in body
    assert "COIN_MARKET_RATING_FITTED  = NO" in body
    assert "RETUNING_ON_OOS            = NO" in body
    assert "MAYAK_COMPONENT_OOS_CONFIRMATION_V1_RU.md" in authority
    assert "OOS protocol V1" in current_map
