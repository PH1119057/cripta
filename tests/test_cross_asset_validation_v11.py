from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bybit_workbench.research.cross_asset_validation_v11 import (
    _bool_value,
    _delta,
    _outcome_metrics,
    _segment_metrics,
    _write_markdown,
)


class CrossAssetValidationV11Tests(unittest.TestCase):
    def test_outcome_metrics_separates_all_and_decisive_one_percent(self) -> None:
        rows = [
            {
                "first_0_5_vs_1_0": "favorable_first",
                "first_1_0_vs_1_0": "favorable_first",
            },
            {
                "first_0_5_vs_1_0": "adverse_first",
                "first_1_0_vs_1_0": "adverse_first",
            },
            {
                "first_0_5_vs_1_0": "favorable_first",
                "first_1_0_vs_1_0": "neither",
            },
        ]
        metrics = _outcome_metrics(rows)
        self.assertEqual(metrics["signals"], 3)
        self.assertAlmostEqual(metrics["first_0_5_vs_1_0_favorable_percent"], 66.6667)
        self.assertAlmostEqual(
            metrics["first_1_0_vs_1_0_decisive_favorable_percent"], 50.0
        )

    def test_segment_metrics_keeps_three_fixed_slices(self) -> None:
        rows = [
            {
                "segment": "1",
                "first_0_5_vs_1_0": "favorable_first",
                "first_1_0_vs_1_0": "favorable_first",
            },
            {
                "segment": "3",
                "first_0_5_vs_1_0": "adverse_first",
                "first_1_0_vs_1_0": "adverse_first",
            },
        ]
        metrics = _segment_metrics(rows)
        self.assertEqual([item["segment"] for item in metrics], [1, 2, 3])
        self.assertEqual(metrics[1]["signals"], 0)

    def test_bool_value_accepts_csv_truthy_values(self) -> None:
        self.assertTrue(_bool_value("True"))
        self.assertTrue(_bool_value("1"))
        self.assertFalse(_bool_value("False"))

    def test_delta_is_validation_minus_frozen_benchmark(self) -> None:
        current = {"rate": 85.0}
        benchmark = {"rate": 82.3}
        self.assertEqual(_delta(current, benchmark, "rate"), 2.7)

    def test_markdown_writer_records_period_and_core_metrics(self) -> None:
        summary = {
            "validation": {
                "symbol": "LINKUSDT",
                "evaluation_start": "2026-05-18T00:00:00+00:00",
                "evaluation_end": "2026-08-16T00:00:00+00:00",
                "period_matches_requested": True,
                "p30": {
                    "candidates": 100,
                    "hit_plus_0_5_pct_rate": 80.0,
                },
                "p31_pressure_then_reversal": {
                    "first_0_5_vs_1_0_favorable_percent": 67.0,
                },
                "p33_pause_60m": {
                    "first_0_5_vs_1_0_favorable_percent": 70.0,
                },
                "core": {
                    "signals": 20,
                    "first_0_5_vs_1_0_favorable_percent": 82.0,
                    "first_1_0_vs_1_0_decisive_favorable_percent": 64.0,
                },
                "core_by_30d_segment": [
                    {
                        "segment": 1,
                        "signals": 7,
                        "first_0_5_vs_1_0_favorable_percent": 80.0,
                        "first_1_0_vs_1_0_decisive_favorable_percent": 60.0,
                    },
                    {
                        "segment": 2,
                        "signals": 6,
                        "first_0_5_vs_1_0_favorable_percent": 83.0,
                        "first_1_0_vs_1_0_decisive_favorable_percent": 65.0,
                    },
                    {
                        "segment": 3,
                        "signals": 7,
                        "first_0_5_vs_1_0_favorable_percent": 83.0,
                        "first_1_0_vs_1_0_decisive_favorable_percent": 67.0,
                    },
                ],
                "orderbook_support_net_positive_10bps_30s": {
                    "first_0_5_vs_1_0_favorable_percent": 90.0,
                },
            },
            "uni_frozen_benchmark": {
                "p30": {"candidates": 973, "hit_plus_0_5_pct_rate": 79.65},
                "p31_pressure_then_reversal": {
                    "first_0_5_vs_1_0_favorable_percent": 66.97,
                },
                "p33_pause_60m": {
                    "first_0_5_vs_1_0_favorable_percent": 69.1,
                },
                "core": {
                    "signals": 113,
                    "first_0_5_vs_1_0_favorable_percent": 82.3,
                    "first_1_0_vs_1_0_decisive_favorable_percent": 63.55,
                },
                "orderbook_support_net_positive_10bps_30s": {
                    "first_0_5_vs_1_0_favorable_percent": 100.0,
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "validation.md"
            _write_markdown(path, summary)
            text = path.read_text(encoding="utf-8")
        self.assertIn("LINKUSDT", text)
        self.assertIn("Core +0.5/-1", text)
        self.assertIn("Exact requested period: **True**", text)


if __name__ == "__main__":
    unittest.main()
