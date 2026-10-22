"""Deterministic stdlib unit tests for CRIPTA four-factor *pilot* (no live/data server writes)."""
import csv
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "research/microstructure/experiments/trade_4factor_joint_pilot_v1.py"
SPEC = importlib.util.spec_from_file_location("trade_4factor_joint_pilot_v1", MODULE)
SUT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUT)


class PilotContractTest(unittest.TestCase):
    symbol = "APTUSDT"
    day = "2026-05-19"
    minutes = 120

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / self.symbol / "public_trades" / f"{self.symbol}{self.day}.csv.gz"
        self.raw.parent.mkdir(parents=True)
        self.start = dt.datetime(2026, 5, 19, tzinfo=dt.UTC).timestamp()
        self.manifest = self.root / "source_manifest.json"

    def make_data(self, bad_side=False, reversed_ts=False):
        with gzip.open(self.raw, "wt", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=("timestamp", "symbol", "side", "price", "foreignNotional")
            )
            writer.writeheader()
            for minute in range(self.minutes):
                t = self.start + 60 * minute
                price = f"{100 + minute * 0.01:.2f}"
                rows = [
                    (t + 1, "Buy", "10"),
                    (t + 1, "Buy", "40"),
                    (t + 2, "Buy", "15"),
                    (t + 3, "Sell", "5"),
                ]
                if reversed_ts and minute == 1:
                    rows[1] = (t - 1, "Buy", "40")
                for j, (ts, side, amount) in enumerate(rows):
                    writer.writerow({"timestamp": f"{ts:.4f}", "symbol": self.symbol,
                                     "side": (
                                         "Garbage" if bad_side and minute == 0 and j == 0
                                         else side
                                     ),
                                     "price": price, "foreignNotional": amount})
        digest = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        self.manifest.write_text(json.dumps({"source_files": [
            {"symbol": self.symbol, "date_utc": self.day, "compressed_sha256": digest}
        ]}))
        return digest

    def test_physical_factors_and_unseen_price_labels(self):
        self.make_data()
        rows, labels, processed = SUT.compute(
            self.symbol, self.day, self.minutes, SUT.Decimal(int(self.start)), self.raw
        )
        self.assertEqual(processed, 480)
        self.assertEqual(len(rows), 120)
        self.assertTrue(all(r["factor_4_large_trade_share"] is None for r in rows[:60]))
        self.assertEqual(rows[60]["prior60_n"], 240)
        self.assertAlmostEqual(rows[60]["prior60_p99"], 40)
        self.assertAlmostEqual(rows[60]["factor_4_large_trade_share"], 40 / 70)
        self.assertEqual(rows[60]["factor_8_max_timestamp_side_group_streak"], 2)
        self.assertEqual(rows[60]["timestamp_side_groups"], 3)
        self.assertEqual(rows[60]["tied_adjacent_fill_timestamps"], 1)
        self.assertEqual(rows[60]["factor_27_median_positive_gap_ms"], 1000)
        self.assertEqual(rows[60]["factor_28_minute_fill_notional_p90_usdt"], 15)
        self.assertEqual(rows[60]["factor_2_trade_rate"], 4)
        self.assertEqual(rows[60]["factor_5_delta_usdt"], 60)
        self.assertEqual(rows[60]["event_minute_utc"], "2026-05-19T01:00:00+00:00")
        self.assertEqual(rows[60]["event_window_end_exclusive_utc"], "2026-05-19T01:01:00+00:00")
        self.assertIsNone(rows[60]["available_after_utc"])
        self.assertEqual(labels["5"]["eligible"], 115)
        self.assertEqual(labels["15"]["eligible"], 105)
        self.assertEqual(labels["60"]["eligible"], 60)
        self.assertEqual(labels["5"]["positive"], 115)
        self.assertNotIn("forward_return", rows[60])

    def test_source_hash_and_date_guard(self):
        sha = self.make_data()
        prior_raw, prior_man = SUT.RAW, SUT.MAN
        SUT.RAW, SUT.MAN = self.root, self.manifest
        try:
            start, selected, confirmed = SUT.read_source_scope(self.symbol, self.day, 120)
            self.assertEqual(start, SUT.Decimal(int(self.start)))
            self.assertEqual(selected, self.raw)
            self.assertEqual(confirmed, sha)
            with self.assertRaises(ValueError) as error:
                SUT.read_source_scope(self.symbol, "2026-07-01", 120)
            self.assertIn("OUTSIDE_FROZEN_FIRST_MONTH", str(error.exception))
            with gzip.open(self.raw, "ab") as f:
                f.write(b"tamper")
            with self.assertRaises(ValueError) as error:
                SUT.read_source_scope(self.symbol, self.day, 120)
            self.assertIn("SOURCE_SHA256_MISMATCH", str(error.exception))
        finally:
            SUT.RAW, SUT.MAN = prior_raw, prior_man

    def test_bad_side_fails_closed(self):
        self.make_data(bad_side=True)
        with self.assertRaisesRegex(ValueError, "RAW_SYMBOL_OR_SIDE"):
            SUT.compute(self.symbol, self.day, self.minutes, SUT.Decimal(int(self.start)), self.raw)

    def test_nonmonotone_fails_closed(self):
        self.make_data(reversed_ts=True)
        with self.assertRaisesRegex(ValueError, "RAW_TS_NONMONOTONE"):
            SUT.compute(self.symbol, self.day, self.minutes, SUT.Decimal(int(self.start)), self.raw)


if __name__ == "__main__":
    unittest.main()
