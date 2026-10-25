"""Pure contract tests for the Sep-Oct P90L90 research-only input builder."""
from __future__ import annotations

import csv
import gzip
import io
import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.research import p90l90_29d_sources_v1 as source


def test_range_29_days_and_warmup():
    assert (date(2026, 10, 20) - date(2026, 9, 21)).days == 29
    assert len(list(source.dates(source.FIRST, source.LAST))) == 39
    assert (date(2026, 9, 21) - source.FIRST).days == 10
    assert source.ROOT.parent == source.PARENT


def test_native_trade_day_qc(tmp_path: Path):
    path = tmp_path / "LTCUSDT2026-09-11.csv.gz"
    with gzip.open(path, "wt", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=sorted(source.REQUIRED))
        writer.writeheader()
        writer.writerow({
            "timestamp": str(datetime(2026, 9, 11, 0, 0, 0,
                                       tzinfo=timezone.utc).timestamp()),
            "symbol": "LTCUSDT", "price": "100.5",
            "tickDirection": "PlusTick", "trdMatchID": "unique",
        })
    assert source.day_check(path, "LTCUSDT", date(2026, 9, 11))["rows"] == 1
    with pytest.raises(ValueError, match="off-day"):
        source.day_check(path, "LTCUSDT", date(2026, 9, 12))
    with pytest.raises(ValueError, match="off-day"):
        source.day_check(path, "DOTUSDT", date(2026, 9, 11))


def test_hour_close_only_causal_and_complete():
    start = int(source.MARKET_FIRST.timestamp())
    end = int(source.MARKET_END.timestamp())
    count = (end - start) // 3600
    hourly_rows = [[str((start+i*3600)*1000), "1", "2", "1", "123.4", "11", "12"]
                   for i in range(count)]
    payload = {"retCode": 0, "result": {"symbol": "BTCUSDT",
                                        "list": list(reversed(hourly_rows))}}

    class Response(io.BytesIO):
        pass

    with patch.object(source.urllib.request, "urlopen",
                      return_value=Response(json.dumps(payload).encode())):
        got, raw = source.hourly_symbol("BTCUSDT")
    assert len(got) == count
    assert min(got) == start+3600
    assert max(got) == end
    assert raw == payload
    assert got[start+3600] == "123.4"


def test_missing_hour_is_rejected():
    start = int(source.MARKET_FIRST.timestamp())
    payload = {"retCode": 0, "result": {"symbol": "ETHUSDT", "list": [
        [str(start*1000), "1", "2", "1", "200.0", "11", "12"],
    ]}}

    with patch.object(source.urllib.request, "urlopen",
                      return_value=io.BytesIO(json.dumps(payload).encode())):
        with pytest.raises(ValueError, match="incomplete"):
            source.hourly_symbol("ETHUSDT")
