from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.research.universal_entry_pool import (
    PoolConfig,
    _fraction_end,
)


def test_fraction_end_uses_requested_part() -> None:
    start = datetime(2026, 5, 18, tzinfo=UTC)
    end = datetime(2026, 8, 16, tzinfo=UTC)
    selected = _fraction_end(start, end, Decimal("0.1"))
    assert selected == datetime(2026, 5, 27, tzinfo=UTC)


def test_pool_config_rejects_invalid_fraction(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fraction"):
        PoolConfig(tmp_path, tmp_path / "out", ("UNIUSDT",), 1, Decimal("0"))


def test_pool_config_is_symbol_agnostic(tmp_path: Path) -> None:
    config = PoolConfig(
        tmp_path,
        tmp_path / "out",
        ("AAVEUSDT", "XLMUSDT"),
        2,
        Decimal("0.1"),
    )
    assert config.symbols == ("AAVEUSDT", "XLMUSDT")
