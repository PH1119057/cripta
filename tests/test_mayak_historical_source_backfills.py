from __future__ import annotations

import csv
import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bybit_workbench.mayak.research import historical_mark_index_backfill as mark_index
from bybit_workbench.mayak.research import historical_oi_backfill as oi


def _write_baseline(path: Path, symbols: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "symbol",
                "direction",
                "touch_at",
                "original_entry_price",
                "scenario",
            ],
        )
        writer.writeheader()
        for index, symbol in enumerate(symbols):
            writer.writerow(
                {
                    "symbol": symbol,
                    "direction": "Long" if index % 2 == 0 else "Short",
                    "touch_at": f"2026-05-25T0{index + 1}:00:00+00:00",
                    "original_entry_price": str(100 + index),
                    "scenario": "BASELINE_0P00",
                }
            )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_oi_backfill_is_generic_and_writes_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    symbols = ("AAAUSDT", "BBBUSDT")
    baseline = tmp_path / "baseline.csv"
    _write_baseline(baseline, symbols)
    calls: list[tuple[str, datetime, datetime]] = []

    def fake_download(
        config: Any, *, start_at: datetime, end_at: datetime
    ) -> tuple[tuple[datetime, Decimal], ...]:
        symbol = str(config.symbol)
        calls.append((symbol, start_at, end_at))
        return (
            (start_at, Decimal("100")),
            (end_at, Decimal("101")),
        )

    monkeypatch.setattr(oi, "download_open_interest", fake_download)
    output = tmp_path / "oi"
    manifest = oi.run(
        baseline=baseline,
        symbols=symbols,
        output_dir=output,
        source_commit="a" * 40,
        workers=2,
        expected_signals=2,
    )
    assert sorted(item[0] for item in calls) == sorted(symbols)
    assert manifest["signals"] == 2
    assert manifest["symbols"] == list(symbols)
    assert manifest["outcome_used"] is False
    assert manifest["trading_effect"] == "NONE"
    assert manifest["baseline_sha256"] == _sha(baseline)
    for item in manifest["files"]:
        path = output / "oi_5m" / (str(item["symbol"]) + ".csv")
        assert item["sha256"] == _sha(path)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert [row["open_interest"] for row in rows] == ["100", "101"]


def test_oi_backfill_rejects_panel_mismatch(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.csv"
    _write_baseline(baseline, ("AAAUSDT",))
    with pytest.raises(ValueError, match="panel mismatch"):
        oi.run(
            baseline=baseline,
            symbols=("BBBUSDT",),
            output_dir=tmp_path / "out",
            source_commit="a" * 40,
            expected_signals=1,
        )


def test_mark_index_backfill_is_generic_and_hashes_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    symbols = ("AAAUSDT", "BBBUSDT")
    baseline = tmp_path / "baseline.csv"
    _write_baseline(baseline, symbols)
    calls: list[tuple[str, str, datetime, datetime]] = []

    def fake_download(
        path: Path,
        *,
        config: Any,
        endpoint_path: str,
        label: str,
        evaluation_start: datetime,
        evaluation_end: datetime,
    ) -> tuple[object, ...]:
        del label
        symbol = str(config.symbol)
        calls.append((symbol, endpoint_path, evaluation_start, evaluation_end))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["timestamp", "close_price"])
            writer.writeheader()
            writer.writerow({"timestamp": evaluation_start.isoformat(), "close_price": "100"})
        return (object(),)

    monkeypatch.setattr(mark_index, "_download_price_klines", fake_download)
    output = tmp_path / "mi"
    manifest = mark_index.run(
        baseline=baseline,
        symbols=symbols,
        output_dir=output,
        source_commit="b" * 40,
        workers=2,
        expected_signals=2,
    )
    assert len(calls) == 4
    assert {item[1] for item in calls} == {
        "/v5/market/mark-price-kline",
        "/v5/market/index-price-kline",
    }
    assert manifest["signals"] == 2
    assert manifest["symbols"] == list(symbols)
    assert manifest["outcome_used"] is False
    assert manifest["trading_effect"] == "NONE"
    assert manifest["baseline_sha256"] == _sha(baseline)
    for item in manifest["files"]:
        symbol_dir = output / "data" / str(item["symbol"])
        assert item["mark_sha256"] == _sha(symbol_dir / "mark_price_5m.csv")
        assert item["index_sha256"] == _sha(symbol_dir / "index_price_5m.csv")


def test_mark_index_backfill_rejects_signal_count_mismatch(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.csv"
    _write_baseline(baseline, ("AAAUSDT",))
    with pytest.raises(ValueError, match="signal count mismatch"):
        mark_index.run(
            baseline=baseline,
            symbols=("AAAUSDT",),
            output_dir=tmp_path / "out",
            source_commit="b" * 40,
            expected_signals=2,
        )


def test_backfill_windows_are_causal_and_utc_aligned(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.csv"
    _write_baseline(baseline, ("AAAUSDT",))
    _, oi_start, oi_end = oi._window(baseline)
    _, mark_start, mark_end = mark_index._window(baseline)
    touch = datetime(2026, 5, 25, 1, 0, tzinfo=UTC)
    assert oi_start == touch.replace(minute=0) - __import__("datetime").timedelta(hours=2)
    assert mark_start == oi_start
    assert oi_end > touch
    assert mark_end == touch
