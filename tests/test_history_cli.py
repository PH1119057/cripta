import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bybit_workbench.__main__ import main


class HistoryCliTests(unittest.TestCase):
    def test_inspect_history_outputs_reproducible_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.csv"
            path.write_text(
                "opened_at,closed_at,open,high,low,close,volume\n"
                "2025-01-01T00:00:00Z,2025-01-01T00:01:00Z,100,101,99,100,1\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "--inspect-history",
                        str(path),
                        "--symbol",
                        "BTCUSDT",
                        "--timeframe",
                        "1",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn('"contiguous": true', output.getvalue())
            self.assertIn('"fingerprint":', output.getvalue())

    def test_eligibility_requires_production_data_and_persists_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trade = root / "trade.csv"
            mark = root / "mark.csv"
            funding = root / "funding.csv"
            database = root / "journal.sqlite3"
            report = root / "eligibility.json"
            rows = ["opened_at,closed_at,open,high,low,close,volume"]
            for index in range(100):
                opened = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=index)
                closed = opened + timedelta(hours=1)
                rows.append(
                    f"{opened.isoformat()},{closed.isoformat()},100,102,98,101,10"
                )
            content = "\n".join(rows) + "\n"
            trade.write_text(content, encoding="utf-8")
            mark.write_text(content, encoding="utf-8")
            funding.write_text(
                "occurred_at,rate,mark_price\n"
                "2025-01-02T00:00:00Z,0.0001,101\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "--backtest", str(trade),
                        "--mark-history", str(mark),
                        "--funding-history", str(funding),
                        "--strategy", "user_algorithm_1",
                        "--symbol", "BTCUSDT",
                        "--timeframe", "60",
                        "--instrument-rules",
                        (
                            '{"symbol":"BTCUSDT","tick_size":"0.1",'
                            '"qty_step":"0.001","min_order_qty":"0.001",'
                            '"min_notional":"5","max_order_qty":"1000"}'
                        ),
                        "--parameters", '{"entry_lookback":20,"atr_period":5}',
                        "--database", str(database),
                        "--report-json", str(report),
                        "--eligibility",
                        "--strict-market-data",
                    ]
                )
            self.assertEqual(code, 0)
            summary = json.loads(output.getvalue())
            self.assertIsNotNone(summary["eligibility"]["report_id"])
            self.assertTrue(summary["eligibility"]["production_equivalent"])
            self.assertFalse(summary["eligibility"]["eligible_for_micro_live"])
            manifest = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["eligibility"]["report_id"], summary["eligibility"]["report_id"]
            )

    def test_strict_backtest_rejects_empty_funding_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trade = root / "trade.csv"
            mark = root / "mark.csv"
            funding = root / "funding.csv"
            rows = ["opened_at,closed_at,open,high,low,close,volume"]
            for index in range(30):
                opened = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=index)
                closed = opened + timedelta(hours=1)
                rows.append(f"{opened.isoformat()},{closed.isoformat()},100,102,98,101,10")
            content = "\n".join(rows) + "\n"
            trade.write_text(content, encoding="utf-8")
            mark.write_text(content, encoding="utf-8")
            funding.write_text("occurred_at,rate,mark_price\n", encoding="utf-8")
            error = io.StringIO()
            with redirect_stderr(error):
                code = main(
                    [
                        "--backtest", str(trade),
                        "--mark-history", str(mark),
                        "--funding-history", str(funding),
                        "--strategy", "user_algorithm_1",
                        "--symbol", "BTCUSDT",
                        "--timeframe", "60",
                        "--instrument-rules",
                        (
                            '{"symbol":"BTCUSDT","tick_size":"0.1",'
                            '"qty_step":"0.001","min_order_qty":"0.001",'
                            '"min_notional":"5","max_order_qty":"1000"}'
                        ),
                        "--parameters", '{"entry_lookback":20,"atr_period":5}',
                        "--strict-market-data",
                    ]
                )
            self.assertEqual(code, 5)
            self.assertIn("non-empty explicit funding series", error.getvalue())

    def test_backtest_exports_reproducible_research_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.csv"
            rows = ["opened_at,closed_at,open,high,low,close,volume"]
            for index in range(50):
                opened = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=index)
                closed = opened + timedelta(hours=1)
                rows.append(f"{opened.isoformat()},{closed.isoformat()},100,102,98,101,10")
            history.write_text("\n".join(rows) + "\n", encoding="utf-8")
            report = root / "report.json"
            trades = root / "trades.csv"
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "--backtest",
                        str(history),
                        "--strategy",
                        "user_algorithm_1",
                        "--symbol",
                        "BTCUSDT",
                        "--timeframe",
                        "60",
                        "--instrument-rules",
                        (
                            '{"symbol":"BTCUSDT","tick_size":"0.1",'
                            '"qty_step":"0.001","min_order_qty":"0.001",'
                            '"min_notional":"5","max_order_qty":"1000"}'
                        ),
                        "--parameters",
                        '{"entry_lookback":20,"atr_period":5}',
                        "--report-json",
                        str(report),
                        "--trades-csv",
                        str(trades),
                        "--walk-forward-training-bars",
                        "25",
                        "--walk-forward-test-bars",
                        "22",
                        "--stress-suite",
                        "--sensitivity-parameter",
                        "entry_lookback",
                        "--sensitivity-values",
                        "[21,22]",
                    ]
                )
            self.assertEqual(code, 0)
            manifest = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "backtest-report-v2")
            self.assertEqual(manifest["badge"], "Research only · Micro-Live blocked")
            self.assertEqual(manifest["instrument_rules"]["symbol"], "BTCUSDT")
            self.assertFalse(manifest["dataset"]["quality"]["production_equivalent"])
            self.assertTrue(trades.exists())
            self.assertEqual(len(manifest["walk_forward"]), 1)
            self.assertEqual(len(manifest["stress"]), 2)
            self.assertEqual(len(manifest["sensitivity"]), 2)
            self.assertEqual(len(manifest["tested_parameter_fingerprints"]), 3)
            rerun_output = io.StringIO()
            with redirect_stdout(rerun_output):
                rerun_code = main(["--rerun-report", str(report)])
            self.assertEqual(rerun_code, 0)
            self.assertTrue(json.loads(rerun_output.getvalue())["exact_binding_match"])


if __name__ == "__main__":
    unittest.main()
