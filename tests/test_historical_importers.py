import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bybit_workbench.historical import (
    CsvCandleSchema,
    load_candles_csv,
    load_candles_parquet,
)

HEADER = "opened_at,closed_at,open,high,low,close,volume\n"


class HistoricalCsvImporterTests(unittest.TestCase):
    def _write(self, body: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "candles.csv"
        path.write_text(body, encoding="utf-8")
        return path

    def test_imports_iso_and_epoch_milliseconds_as_exact_decimals(self) -> None:
        path = self._write(
            HEADER
            + "2025-01-01T00:00:00Z,2025-01-01T00:01:00+00:00,"
            + "100.010,101.020,99.990,100.500,12.345\n"
            + "1735689660000,1735689720000,100.500,102,100,101.250,8\n"
        )
        result = load_candles_csv(path, symbol="BTCUSDT", timeframe="1")
        self.assertEqual(len(result.candles), 2)
        self.assertEqual(str(result.candles[0].open), "100.010")
        self.assertEqual(str(result.candles[0].volume), "12.345")
        self.assertEqual(result.candles[1].opened_at.isoformat(), "2025-01-01T00:01:00+00:00")

    def test_rejects_unsorted_rows_instead_of_silently_reordering(self) -> None:
        path = self._write(
            HEADER
            + "2025-01-01T00:01:00Z,2025-01-01T00:02:00Z,100,101,99,100,1\n"
            + "2025-01-01T00:00:00Z,2025-01-01T00:01:00Z,100,101,99,100,1\n"
        )
        with self.assertRaisesRegex(ValueError, "strictly chronological"):
            load_candles_csv(path, symbol="BTCUSDT", timeframe="1")

    def test_rejects_naive_timestamps_and_non_finite_values(self) -> None:
        naive = self._write(HEADER + "2025-01-01T00:00:00,2025-01-01T00:01:00,100,101,99,100,1\n")
        with self.assertRaisesRegex(ValueError, "timezone"):
            load_candles_csv(naive, symbol="BTCUSDT", timeframe="1")
        non_finite = self._write(
            HEADER + "2025-01-01T00:00:00Z,2025-01-01T00:01:00Z,NaN,101,99,100,1\n"
        )
        with self.assertRaisesRegex(ValueError, "finite"):
            load_candles_csv(non_finite, symbol="BTCUSDT", timeframe="1")

    def test_supports_explicit_column_mapping_and_requires_headers(self) -> None:
        path = self._write(
            "start,end,o,h,l,c,v\n" + "2025-01-01T00:00:00Z,2025-01-01T00:01:00Z,100,101,99,100,1\n"
        )
        result = load_candles_csv(
            path,
            symbol="BTCUSDT",
            timeframe="1",
            schema=CsvCandleSchema("start", "end", "o", "h", "l", "c", "v"),
        )
        self.assertEqual(len(result.candles), 1)

    def test_gap_is_rejected_by_default_and_can_be_explicitly_inspected(self) -> None:
        path = self._write(
            HEADER
            + "2025-01-01T00:00:00Z,2025-01-01T00:01:00Z,100,101,99,100,1\n"
            + "2025-01-01T00:02:00Z,2025-01-01T00:03:00Z,100,101,99,100,1\n"
        )
        with self.assertRaisesRegex(ValueError, "contains gaps"):
            load_candles_csv(path, symbol="BTCUSDT", timeframe="1")
        dataset = load_candles_csv(
            path,
            symbol="BTCUSDT",
            timeframe="1",
            require_contiguous=False,
        )
        from bybit_workbench.historical import inspect_continuity

        self.assertEqual(len(inspect_continuity(dataset).gaps), 1)

    def test_parquet_adapter_uses_same_validation_contract(self) -> None:
        class FakeTable:
            column_names = [
                "opened_at",
                "closed_at",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]

            def to_pylist(self):
                return [
                    {
                        "opened_at": datetime(2025, 1, 1, tzinfo=UTC),
                        "closed_at": datetime(2025, 1, 1, 0, 1, tzinfo=UTC),
                        "open": Decimal("100.01"),
                        "high": Decimal("101"),
                        "low": Decimal("99"),
                        "close": Decimal("100.5"),
                        "volume": Decimal("1.25"),
                    }
                ]

        class FakeParquet:
            @staticmethod
            def read_table(path):
                return FakeTable()

        with patch(
            "bybit_workbench.historical.importers._load_pyarrow_parquet",
            return_value=FakeParquet,
        ):
            result = load_candles_parquet("fixture.parquet", symbol="BTCUSDT", timeframe="1")
        self.assertEqual(result.candles[0].open, Decimal("100.01"))


if __name__ == "__main__":
    unittest.main()
