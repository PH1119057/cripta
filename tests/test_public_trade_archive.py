from __future__ import annotations

from argparse import Namespace
from datetime import date
from pathlib import Path

import pytest

from research.server.dataset import archive_public_trades_daily as archive

ROOT = Path(__file__).resolve().parents[1]


def test_explicit_range_accepts_only_completed_day_contract() -> None:
    args = Namespace(start=date(2026, 9, 18), end=date(2026, 10, 1), lookback_days=3)
    assert archive.resolve_range(args) == (date(2026, 9, 18), date(2026, 10, 1))


def test_quarantined_symbols_are_not_defaults_or_unit_configuration() -> None:
    unit = (
        ROOT / "research/server/dataset/cripta-public-trade-archive.service"
    ).read_text(encoding="utf-8")
    for symbol in archive.QUARANTINED:
        assert symbol not in archive.DEFAULT_SYMBOLS
        assert symbol not in unit


def test_systemd_writer_is_exact_actor_and_exact_root() -> None:
    unit = (
        ROOT / "research/server/dataset/cripta-public-trade-archive.service"
    ).read_text(encoding="utf-8")
    assert "User=cripta" in unit
    assert "Group=cripta" in unit
    assert (
        "ReadWritePaths=/data/cripta/datasets/raw/bybit_public_trades_daily_v1"
        in unit
    )
    assert "ReadWritePaths=/data/cripta/datasets/raw\n" not in unit


def test_manifest_rejects_wrong_schema(tmp_path) -> None:
    path = tmp_path / "MANIFEST.json"
    path.write_text('{"schema_version":"wrong","entries":{}}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="manifest schema mismatch"):
        archive.load_manifest(path)
