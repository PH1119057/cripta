from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from bybit_workbench.research.exit_architecture_oos_v19 import (
    ArchitectureConfig,
    ArchitectureResult,
    _expected_source_check,
    _max_drawdown,
    _p47e_crosscheck,
    _profit_factor,
    summarise_results,
)


class ExitArchitectureOosV19Tests(unittest.TestCase):
    def test_frozen_config_values(self) -> None:
        config = ArchitectureConfig()
        self.assertEqual(config.initial_stop_pct, 1.0)
        self.assertEqual(config.early_activation_pct, 0.10)
        self.assertEqual(config.activation_1p10_pct, 1.10)
        self.assertEqual(config.full_runner_mfe_giveback_pct, 1.50)
        self.assertEqual(config.split_core_fraction, 0.50)
        self.assertEqual(config.split_runner_mfe_giveback_pct, 4.00)

    def test_expected_source_check_accepts_frozen_holdout(self) -> None:
        actual = {
            "BTCUSDT": 119,
            "ETHUSDT": 130,
            "XRPUSDT": 125,
            "1000PEPEUSDT": 117,
            "SOLUSDT": 91,
            "DOGEUSDT": 143,
            "ADAUSDT": 111,
        }
        check = _expected_source_check(actual)
        self.assertTrue(check["all_match"])
        self.assertEqual(check["actual_pooled"], 836)

    def test_expected_source_check_fails_closed_on_mismatch(self) -> None:
        check = _expected_source_check({"BTCUSDT": 119})
        self.assertFalse(check["all_match"])

    def test_profit_factor(self) -> None:
        self.assertEqual(_profit_factor([1.0, 2.0, -1.0]), 3.0)
        self.assertIsNone(_profit_factor([0.0, 1.0]))

    def test_max_drawdown(self) -> None:
        self.assertEqual(_max_drawdown([1.0, -0.5, -1.0, 2.0]), -1.5)

    def test_summary_uses_all_and_decision_grade_separately(self) -> None:
        start = datetime(2026, 5, 18, tzinfo=UTC)
        rows = (
            ArchitectureResult(
                symbol="BTCUSDT",
                touch_at=start,
                policy_id="A_SIMPLE_TAKE_1P00",
                exit_reason="initial_stop",
                exit_at=start + timedelta(minutes=1),
                exit_move_pct=-1.0,
                completed_horizon=True,
                early_activated=False,
                activation_1p10_reached=False,
                max_favorable_pct=0.0,
            ),
            ArchitectureResult(
                symbol="BTCUSDT",
                touch_at=start + timedelta(hours=1),
                policy_id="A_SIMPLE_TAKE_1P00",
                exit_reason="core_take",
                exit_at=start + timedelta(hours=1, minutes=2),
                exit_move_pct=1.0,
                completed_horizon=True,
                early_activated=True,
                activation_1p10_reached=True,
                max_favorable_pct=1.1,
            ),
            ArchitectureResult(
                symbol="BTCUSDT",
                touch_at=start + timedelta(hours=2),
                policy_id="A_SIMPLE_TAKE_1P00",
                exit_reason="data_end",
                exit_at=start + timedelta(hours=3),
                exit_move_pct=0.25,
                completed_horizon=False,
                early_activated=True,
                activation_1p10_reached=False,
                max_favorable_pct=0.5,
            ),
        )
        summary = summarise_results(
            rows,
            policy_id="A_SIMPLE_TAKE_1P00",
            scope="HOLDOUT7",
            sample_span_days=10.0,
        )
        self.assertEqual(summary["signals"], 3)
        self.assertEqual(summary["decision_grade"], 2)
        self.assertEqual(summary["censored"], 1)
        self.assertEqual(summary["gross_all_signal_sum_pct"], 0.25)
        self.assertEqual(summary["gross_decision_grade_sum_pct"], 0.0)
        self.assertEqual(summary["activation_1p10_reached"], 1)

    def test_p47e_crosscheck_not_applicable_without_report(self) -> None:
        with TemporaryDirectory() as tmp:
            result = _p47e_crosscheck(
                Path(tmp),
                {"signals": 836, "gross_all_signal_sum_pct": 65.8524},
            )
        self.assertFalse(result["applicable"])

    def test_p47e_crosscheck_matches_existing_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "reports" / "hourly_trend_oos_v1" / "HOLDOUT7_20260818"
            report.mkdir(parents=True)
            (report / "summary.json").write_text(
                '{"selected_policy_id":"CORE050_RUN050_BE_MFE_GB4.00",'
                '"holdout_pooled":{"signals":836,"gross_selected_policy_pct":65.8524}}',
                encoding="utf-8",
            )
            result = _p47e_crosscheck(
                root,
                {"signals": 836, "gross_all_signal_sum_pct": 65.8524},
            )
        self.assertTrue(result["applicable"])
        self.assertTrue(result["signals_match"])
        self.assertTrue(result["gross_match_within_1e_6"])


if __name__ == "__main__":
    unittest.main()
