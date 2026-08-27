from __future__ import annotations

import csv
from pathlib import Path

from bybit_workbench.research.universal_entry_path_replay import (
    _generic_summary,
    _load_signals,
    _raw_archive_map,
    _signals_path,
)


def test_loads_universal_signal_contract(tmp_path: Path) -> None:
    path = tmp_path / "signals.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["symbol", "direction", "entry_at", "entry_price"]
        )
        writer.writeheader()
        writer.writerow(
            {
                "symbol": "UNIUSDT",
                "direction": "Long",
                "entry_at": "2026-05-18T00:00:00+00:00",
                "entry_price": "3.5",
            }
        )
    signals = _load_signals(path, "UNIUSDT")
    assert len(signals) == 1
    assert signals[0].entry_price == 3.5


def test_discovers_raw_archives(tmp_path: Path) -> None:
    public = tmp_path / "public_trades"
    public.mkdir()
    archive = public / "UNIUSDT2026-05-18.csv.gz"
    archive.touch()
    assert _raw_archive_map(tmp_path, "UNIUSDT") == {"2026-05-18": archive}


def test_empty_generic_summary_accepts_new_symbols() -> None:
    summary = _generic_summary([])
    assert [row["adverse_offset_pct"] for row in summary] == [0.0, 0.1, 0.2]
    assert all(row["signals"] == 0 for row in summary)


def test_discovers_pipeline_nested_signals(tmp_path: Path) -> None:
    path = tmp_path / "entry" / "AAVEUSDT" / "signals.csv"
    path.parent.mkdir(parents=True)
    path.touch()
    assert _signals_path(tmp_path, "AAVEUSDT") == path
