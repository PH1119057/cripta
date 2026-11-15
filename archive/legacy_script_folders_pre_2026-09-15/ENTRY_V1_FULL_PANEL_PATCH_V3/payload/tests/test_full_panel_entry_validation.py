from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from bybit_workbench.research.full_panel_entry_validation import (
    AssetPaths,
    _aggregate_context,
    _aggregate_pipeline,
    build_asset_summary,
)


class FullPanelEntryValidationTests(unittest.TestCase):
    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _write_csv(self, path: Path, rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = list(rows[0])
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def _fixture(self, root: Path) -> AssetPaths:
        paths = AssetPaths.from_root(root)
        self._write_json(
            paths.p30 / "comparison.json",
            {
                "evaluation_start": "2026-05-18T00:00:00+00:00",
                "evaluation_end": "2026-08-16T00:00:00+00:00",
            },
        )
        self._write_csv(
            paths.p30 / "p30_local_soft_hourly" / "signals.csv",
            [
                {"first_0_5_vs_1_0": "favorable_first"},
                {"first_0_5_vs_1_0": "adverse_first"},
                {"first_0_5_vs_1_0": "favorable_first"},
                {"first_0_5_vs_1_0": "adverse_first"},
            ],
        )
        self._write_json(
            paths.p31 / "summary.json",
            {
                "summary": {
                    "all": {"exact_first_0_5_vs_1_0_favorable_percent": 50.0}
                }
            },
        )
        exact_rows = [
            {
                "direction": "Long",
                "flow_state": "pressure_then_reversal",
                "first_0_5_vs_1_0": "favorable_first",
                "first_1_0_vs_1_0": "favorable_first",
            },
            {
                "direction": "Long",
                "flow_state": "pressure_then_reversal",
                "first_0_5_vs_1_0": "adverse_first",
                "first_1_0_vs_1_0": "adverse_first",
            },
            {
                "direction": "Short",
                "flow_state": "neutral_or_mixed",
                "first_0_5_vs_1_0": "favorable_first",
                "first_1_0_vs_1_0": "neither",
            },
            {
                "direction": "Short",
                "flow_state": "neutral_or_mixed",
                "first_0_5_vs_1_0": "adverse_first",
                "first_1_0_vs_1_0": "adverse_first",
            },
        ]
        self._write_csv(paths.p33 / "signals_adverse_path.csv", exact_rows)
        self._write_json(
            paths.p33 / "summary.json",
            {
                "mae": {
                    "mae_before_plus_0_5_for_favorable_signals_pct": {
                        "median": 0.3,
                        "p75": 0.5,
                        "p80": 0.6,
                        "p90": 0.8,
                        "p95": 0.9,
                    }
                }
            },
        )

        p34_rows = [
            {
                **exact_rows[0],
                "accepted_after_failure_embargo": "True",
                "oi_60m_quartile": "Q1",
                "oi_accel_quartile": "Q1",
                "oi_state": "rising",
                "oi_price_regime_60m": "aligned",
            },
            {
                **exact_rows[1],
                "accepted_after_failure_embargo": "False",
                "oi_60m_quartile": "Q4",
                "oi_accel_quartile": "Q4",
                "oi_state": "falling",
                "oi_price_regime_60m": "opposed",
            },
            {
                **exact_rows[2],
                "accepted_after_failure_embargo": "True",
                "oi_60m_quartile": "Q2",
                "oi_accel_quartile": "Q2",
                "oi_state": "rising",
                "oi_price_regime_60m": "aligned",
            },
            {
                **exact_rows[3],
                "accepted_after_failure_embargo": "True",
                "oi_60m_quartile": "Q3",
                "oi_accel_quartile": "Q3",
                "oi_state": "falling",
                "oi_price_regime_60m": "opposed",
            },
        ]
        self._write_csv(paths.p34 / "signals_open_interest.csv", p34_rows)

        p35_rows = []
        for index, row in enumerate(p34_rows):
            p35_rows.append(
                {
                    **row,
                    "crowd_majority": "aligned" if index % 2 == 0 else "opposed",
                    "crowd_edge_quartile": f"Q{index + 1}",
                    "crowd_change_60m_quartile": f"Q{index + 1}",
                    "crowd_accel_quartile": f"Q{index + 1}",
                }
            )
        self._write_csv(paths.p35 / "signals_crowding.csv", p35_rows)

        p36_rows = []
        for index, row in enumerate(p35_rows):
            p36_rows.append(
                {
                    **row,
                    "basis_state": "aligned_premium" if index % 2 == 0 else "opposed_discount",
                    "basis_level_quartile": f"Q{index + 1}",
                    "basis_change_60m_quartile": f"Q{index + 1}",
                    "basis_accel_quartile": f"Q{index + 1}",
                    "oi_tail_danger": "False" if index != 1 else "True",
                }
            )
        self._write_csv(paths.p36 / "signals_basis.csv", p36_rows)

        core_rows = [
            {
                "direction": "Long",
                "segment": "1",
                "first_0_5_vs_1_0": "favorable_first",
                "first_1_0_vs_1_0": "favorable_first",
                "support_wall_closer": "true",
                "support_wall_larger": "true",
                "both_wall_advantages": "true",
                "near_imbalance_positive": "true",
                "near_imbalance_improving": "true",
                "near_imbalance_positive_or_improving": "true",
                "near_imbalance_positive_and_improving": "true",
                "adverse_taker_dominant_30s": "true",
                "price_favorable_or_flat_30s": "true",
                "adverse_flow_but_price_holds_30s": "true",
                "support_net_positive_10bps_30s": "true",
                "support_refill_present_10bps_30s": "true",
                "support_refill_present_25bps_30s": "true",
            },
            {
                "direction": "Short",
                "segment": "2",
                "first_0_5_vs_1_0": "adverse_first",
                "first_1_0_vs_1_0": "neither",
                "support_wall_closer": "false",
                "support_wall_larger": "false",
                "both_wall_advantages": "false",
                "near_imbalance_positive": "false",
                "near_imbalance_improving": "false",
                "near_imbalance_positive_or_improving": "false",
                "near_imbalance_positive_and_improving": "false",
                "adverse_taker_dominant_30s": "false",
                "price_favorable_or_flat_30s": "false",
                "adverse_flow_but_price_holds_30s": "false",
                "support_net_positive_10bps_30s": "false",
                "support_refill_present_10bps_30s": "false",
                "support_refill_present_25bps_30s": "false",
            },
        ]
        self._write_csv(paths.p39 / "orderbook_features.csv", core_rows)
        self._write_csv(paths.p40 / "absorption_features.csv", core_rows)
        return paths

    def test_asset_summary_preserves_pepe_execution_symbol_and_display_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._fixture(Path(directory))
            asset, context, layers = build_asset_summary(paths, symbol="1000PEPEUSDT")
        self.assertEqual(asset["research_symbol"], "1000PEPEUSDT")
        self.assertEqual(asset["display_symbol"], "PEPE")
        self.assertTrue(asset["period_ok"])
        self.assertEqual(asset["p30_baseline_05"], 50.0)
        self.assertAlmostEqual(asset["pause60_05"], 66.6667)
        self.assertEqual(asset["core_10_neither"], 1)
        self.assertTrue(any(row["layer"] == "P40_ABSORPTION" for row in context))
        self.assertTrue(any(row["layer"] == "oi_no_tail_core" for row in layers))

    def test_pipeline_aggregation_is_asset_balanced_and_keeps_pooled_secondary(self) -> None:
        rows = [
            {
                "layer": "pause_60m",
                "signals": 1000,
                "favorable": 700,
                "rate_05": 70.0,
                "uplift_05_pp": 10.0,
            },
            {
                "layer": "pause_60m",
                "signals": 10,
                "favorable": 4,
                "rate_05": 40.0,
                "uplift_05_pp": -5.0,
            },
        ]
        result = _aggregate_pipeline(rows)[0]
        self.assertAlmostEqual(result["pooled_rate_05"], 69.703, places=3)
        self.assertEqual(result["median_asset_rate_05"], 55.0)
        self.assertEqual(result["improved_assets"], 1)
        self.assertEqual(result["worsened_assets"], 1)

    def test_context_aggregation_counts_transfer_without_best_quartile_selection(self) -> None:
        rows = [
            {
                "layer": "BASIS",
                "feature": "basis_accel_quartile",
                "value": "Q4",
                "signals": 20,
                "rate_05": 70.0,
                "uplift_05_pp": 5.0,
            },
            {
                "layer": "BASIS",
                "feature": "basis_accel_quartile",
                "value": "Q4",
                "signals": 30,
                "rate_05": 60.0,
                "uplift_05_pp": -3.0,
            },
        ]
        result = _aggregate_context(rows)[0]
        self.assertEqual(result["feature"], "basis_accel_quartile")
        self.assertEqual(result["value"], "Q4")
        self.assertEqual(result["improved_assets"], 1)
        self.assertEqual(result["worsened_assets"], 1)
        self.assertEqual(result["median_uplift_05_pp"], 1.0)


if __name__ == "__main__":
    unittest.main()
