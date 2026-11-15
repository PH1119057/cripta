#!/usr/bin/env python3
"""Offline synthetic tests for historical_trade_minute_mart_v1.

No server, network, DB or trading runtime required. All inputs in memory.
Run: python3 -B -m unittest research/microstructure/tests/test_historical_trade_minute_mart_v1.py
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import pathlib
import sys
import unittest
from decimal import Decimal
from unittest.mock import patch

BASE = pathlib.Path(__file__).resolve().parents[1] / "experiments"
sys.path.insert(0, str(BASE))
import historical_trade_minute_mart_v1 as mart  # noqa: E402

DAY = dt.date(2026, 5, 18)
EPOCH = int(dt.datetime.combine(DAY, dt.time(), dt.UTC).timestamp())
HEADER = "timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional,RPI\n"


def row(t: str, side: str = "Buy", size: str = "1", price: str = "1",
        notional: str = "1", trade_id: str = "one", symbol: str = "TRXUSDT") -> str:
    return (
        f"{Decimal(EPOCH) + Decimal(t)},{symbol},{side},{size},{price},"
        f"PlusTick,{trade_id},0,{size},{notional},0\n"
    )


def primary(text: str):
    return mart.aggregate(csv.DictReader(io.StringIO(text)), "TRXUSDT", DAY)


class NativeTradeMartTests(unittest.TestCase):
    def test_grid_and_boundaries(self):
        content = HEADER + row("0", "Buy", "2", "1.0", "2", "a") + row(
            "60", "Sell", "3", "1.1", "3.3", "b"
        ) + row("86399.999", "Buy", "1", "1.2", "1.2", "c")
        records, stats = primary(content)
        self.assertEqual(1440, len(records))
        self.assertEqual(3, stats["source_trade_rows"])
        self.assertEqual(3, stats["recorded_trade_minutes"])
        self.assertEqual(1, records[0]["sample_count"])
        self.assertEqual(1, records[1]["sample_count"])
        self.assertEqual(1, records[-1]["sample_count"])
        self.assertIsNone(records[2]["file_order_open"])
        self.assertEqual("NO_RECORDED_TRADES", records[2]["price_order_quality"])
        self.assertEqual("2026-05-18T00:01:00+00:00", records[0]["derived_available_after_utc"])
        self.assertIsNone(records[0]["market_source_id"])
        self.assertIsNone(records[0]["original_receiver_available_after"])
        self.assertFalse(records[0]["native_closed_candle"])

    def test_signed_flow_cvd_and_vwap(self):
        content = HEADER + row("0.01", "Buy", "2", "1", "2", "a") + row(
            "0.1", "Sell", "1", "2", "2", "b"
        )
        records, _ = primary(content)
        self.assertEqual(Decimal("0"), Decimal(records[0]["delta_quote_usdt"]))
        self.assertEqual(Decimal("0"), Decimal(records[-1]["cvd_from_utc_day_start_quote_usdt"]))
        self.assertEqual(Decimal("3"), Decimal(records[0]["quantity_base"]))
        self.assertEqual(Decimal("1.333333333333333333333333333"), Decimal(records[0]["vwap_from_day_minute_base_weighted"]))
        self.assertEqual("1", records[0]["file_order_open"])
        self.assertEqual("2", records[0]["file_order_close"])
        self.assertEqual("2", records[0]["native_trades_high"])

    def test_tied_timestamp_quality(self):
        content = HEADER + row("1", trade_id="a") + row("1", trade_id="b")
        records, stats = primary(content)
        self.assertEqual("FILE_ORDER_TIE_UNVERIFIED", records[0]["price_order_quality"])
        self.assertEqual(1, stats["first_last_order_tie_minutes"])

    def test_independent_all_minutes(self):
        text = HEADER + row("0.1", "Buy", "2", "1", "2", "a") + row(
            "0.2", "Sell", "1", "2", "2", "b"
        ) + row("120", "Buy", "3", "1.5", "4.5", "c")
        records, _ = primary(text)
        with patch.object(mart.gzip, "open", return_value=io.StringIO(text)):
            qc = mart.independent_check(pathlib.Path("synthetic"), "TRXUSDT", DAY, records)
        self.assertEqual(1440 * 6, qc["independent_minute_cells"])
        self.assertEqual(0, qc["mismatches"])
        self.assertEqual(3, qc["native_rows"])

    def test_duplicate_id_fails(self):
        content = HEADER + row("1", trade_id="one") + row("1", trade_id="one")
        with self.assertRaisesRegex(ValueError, "DUPLICATE_ID"):
            primary(content)

    def test_nonmonotone_fails(self):
        content = HEADER + row("2", trade_id="one") + row("1", trade_id="two")
        with self.assertRaisesRegex(ValueError, "OUT_OF_ORDER"):
            primary(content)

    def test_off_day_fails(self):
        with self.assertRaisesRegex(ValueError, "OFF_DAY"):
            primary(HEADER + row("86400"))

    def test_missing_data_fails(self):
        with self.assertRaisesRegex(ValueError, "EMPTY_DAY"):
            primary(HEADER)

    def test_bad_identity_fails(self):
        with self.assertRaisesRegex(ValueError, "WRONG_SOURCE"):
            primary(HEADER + row("0", side="Unknown"))

    def test_bad_numeric_fails(self):
        with self.assertRaisesRegex(ValueError, "INVALID_price"):
            primary(HEADER + row("0", price="0"))
        with self.assertRaisesRegex(ValueError, "INVALID_notional"):
            primary(HEADER + row("0", notional="-1"))


if __name__ == "__main__":
    unittest.main()
