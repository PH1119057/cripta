"""Regression tests for exact frozen 2025 L5-3 geometry and old 2026 parity gate."""
from __future__ import annotations

import csv
import gzip
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/research/p90l90_bull2025_geometry_v1.py"


def load():
    spec = importlib.util.spec_from_file_location("bull2025_geometry", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_market_dates_symbols_output_and_old_source_reuse() -> None:
    m = load()
    assert m.SYMBOLS == ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")
    assert (m.FIRST, m.LAST) == ("2025-03-22", "2025-09-30")
    assert m.TOTAL_ROWS == 204446371
    assert m.EXPECTED_STATES == 193 * 288 - 199
    assert m.OUTPUTS == {
        "parity-2026": "l53_geometry_parity_2026_v1",
        "smoke-2025": "l53_geometry_smoke_2025_v1",
        "full": "l53_geometry_full_2025_v1",
    }
    assert m.RAW.as_posix().startswith("/data/cripta/research/runs/")
    source = SCRIPT.read_text()
    assert 'if os.geteuid() != 107:' in source
    assert 'core.process_symbol(symbol, raw_root, str(parts), first, last)' in source
    assert 'pool.submit(core.process_symbol, symbol, raw_root, str(parts), first, last)' in source
    assert 'sha(core) != original["source_snapshot_sha256"][CORE_REL]' in source
    assert "no strategy or Entry/Exit policy changes" in source


def write_gzip_rows(path: Path, rows: list[list[str]]) -> None:
    with gzip.open(path, "wt", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerows(rows)


def test_full_row_content_prefix_2026_comparison(tmp_path: Path) -> None:
    m = load()
    old = tmp_path / "old.csv.gz"
    new = tmp_path / "new.csv.gz"
    header = ["ts", "low", "high"]
    rows = [header] + [[str(i), "4.5", "7.25"] for i in range(160)]
    write_gzip_rows(old, rows + [["161", "0", "1"]])
    write_gzip_rows(new, rows)
    n, digest = m.prefix_parity(new, old)
    assert n == len(rows) and digest == m.sha(new)
    rows[-1][-1] = "9.9"
    write_gzip_rows(new, rows)
    with pytest.raises(RuntimeError, match="2026 frozen prefix parity failed"):
        m.prefix_parity(new, old)


def test_original_l53_geometry_process_symbol_uses_completed_bars(
    tmp_path: Path,
) -> None:
    """Tiny causal tick fixture, original CLEAN v2 code, no production files."""
    from scripts.research import p90l90_l53_clean_v2 as core

    raw = tmp_path / "raw" / "LTCUSDT" / "public_trades"
    parts = tmp_path / "parts"
    raw.mkdir(parents=True)
    parts.mkdir()
    date = "2025-03-22"
    file = raw / f"LTCUSDT{date}.csv.gz"
    begin = int(datetime(2025, 3, 22, tzinfo=UTC).timestamp())
    with gzip.open(file, "wt", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["timestamp", "price"])
        writer.writeheader()
        for i in range(288):
            writer.writerow({
                "timestamp": str(begin + i * 300 + 1),
                "price": str(100 + i % 13),
            })
    result = core.process_symbol("LTCUSDT", tmp_path / "raw", str(parts), date, date)
    assert result["files"] == 1
    assert result["trades"] == 288
    assert result["states"] == 288 - 199
    for kind in ("states", "touches"):
        f = parts / f"{kind}_LTCUSDT.csv.gz"
        assert f.exists() and result[f"{kind}_sha256"] == load().sha(f)
    with gzip.open(parts / "states_LTCUSDT.csv.gz", "rt") as stream:
        states = list(csv.DictReader(stream))
    assert len(states) == 89
    assert int(states[0]["state_effective_ts"]) == begin + 200 * 300
    assert int(states[-1]["state_effective_ts"]) == begin + 288 * 300
