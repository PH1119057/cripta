"""Independent MAIN/SUB position observation, non-trading and sampled only."""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bybit_workbench.account_position_observer import build_sample

P = Path(__file__).resolve().parents[1] / "operations/monitoring/account_position_runtime.py"
_spec = importlib.util.spec_from_file_location("account_position_runtime", P)
assert _spec and _spec.loader
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)
NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
MAIN = "ta-" + "a" * 32
SUB = "ta-" + "b" * 32


class _Cur:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _Db:
    def __init__(self, account: str, *, stale: bool = False) -> None:
        self.account = account
        self.stale = stale
        self.writes: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cur:
        if "FROM runtime.account_state_generations" in sql:
            assert params == (self.account,)
            assert "identity_status='PROVEN'" in sql
            assert "state='COMPLETE'" in sql
            assert "completed_at IS NOT NULL" in sql
            assert "ORDER BY completed_at DESC LIMIT 1" in sql
            return _Cur([{
                "state": "COMPLETE",
                "completed_at": NOW - timedelta(seconds=120 if self.stale else 1),
            }])
        if "FROM runtime.position_ownership p" in sql:
            assert params == (self.account,)
            return _Cur([{
                "position_id": "position", "symbol": "BTCUSDT",
                "position_idx": 0, "side": "Buy", "actual_avg_fill": Decimal(100),
                "payload_json": '{"markPrice":"101"}',
            }])
        if "FROM supervisor.account_position_snapshots" in sql:
            assert params[0] == self.account
            return _Cur([])
        if "INSERT INTO supervisor.account_position_snapshots" in sql:
            self.writes.append(params)
            return _Cur([])
        raise AssertionError(sql[:80])


def test_directional_mfe_mae_are_account_local_and_sampled_not_exact() -> None:
    a = build_sample(
        trading_account_id=MAIN, position_id="p", symbol="BTCUSDT",
        position_idx=0, side="Buy", entry_price="100",
        mark_price="101", previous_mfe=None, previous_mae=None,
    )
    b = build_sample(
        trading_account_id=SUB, position_id="p", symbol="BTCUSDT",
        position_idx=0, side="Sell", entry_price="100",
        mark_price="101", previous_mfe="4", previous_mae="-2",
    )
    assert a.return_pct == Decimal("1")
    assert a.sampled_mfe_pct == Decimal("1")
    assert a.sampled_mae_pct == Decimal(0)
    assert b.return_pct == Decimal("-1")
    assert b.sampled_mfe_pct == Decimal("4")
    assert b.sampled_mae_pct == Decimal("-2")
    assert a.completeness == b.completeness == "SAMPLED_PARTIAL"


def test_account_scoped_position_db_queries_do_not_mix_identical_slots() -> None:
    for account in (MAIN, SUB):
        db = _Db(account)
        got = module.collect_once(db, trading_account_id=account, now=NOW)
        assert got["sampled"] == 1
        assert got["state"] == "SAMPLED_PARTIAL"
        assert db.writes[0][0] == account


def test_stale_generation_does_not_write_unknown_position() -> None:
    db = _Db(SUB, stale=True)
    got = module.collect_once(db, trading_account_id=SUB, now=NOW)
    assert got["state"] == "UNKNOWN"
    assert not db.writes


@pytest.mark.parametrize("bad", ["", "ta-wrong"])
def test_unproven_account_identifier_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        module.collect_once(_Db(SUB), trading_account_id=bad, now=NOW)


def test_invalid_or_missing_mark_cannot_be_reported_as_sample() -> None:
    with pytest.raises(ValueError):
        build_sample(
            trading_account_id=MAIN, position_id="p", symbol="BTCUSDT",
            position_idx=0, side="Buy", entry_price="100",
            mark_price="NaN", previous_mfe=None, previous_mae=None,
        )
