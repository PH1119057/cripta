"""P90 REAL restart must compare two signed GETs without requiring frozen market price."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from bybit_workbench.universal_entry.p90l90_real_open_restart import (
    P90RealOpenRestartUnverified,
    _stable_position,
    _verify_cross_snapshot_position,
)

AT = datetime(2026, 11, 6, 18, 30, tzinfo=UTC)
ACCOUNT = "ta-" + "a" * 32
CONNECTOR = "ac-" + "b" * 32
UID = "12345678"
POSITION_KEY = "synthetic-position-key"


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _DB:
    def __init__(self, generations):
        self._generations = generations
        self.calls = []

    def execute(self, sql, params=()):
        assert "runtime.account_state_generations" in sql
        generation_id, account, connector = params
        self.calls.append((generation_id, account, connector))
        return _Result(self._generations.get(generation_id))


def _route():
    return cast(Any, SimpleNamespace(
        trading_account_id=ACCOUNT,
        account_connector_id=CONNECTOR,
        registered_exchange_uid=UID,
    ))


def _position():
    return cast(Any, SimpleNamespace(
        exchange_position_key=POSITION_KEY,
    ))


def _row(*, mark="100.0", size="1", stop="90", tp="110", trailing="0.35"):
    return {
        "symbol": "APTUSDT",
        "side": "Buy",
        "positionIdx": 0,
        "size": size,
        "avgPrice": "100",
        "stopLoss": stop,
        "takeProfit": tp,
        "trailingStop": trailing,
        "markPrice": mark,
    }


def _account_proof(
    *, generation="g-account", mark="100.0", observed_seconds=2,
):
    return cast(Any, SimpleNamespace(
        private_generation_id=generation,
        observed_at=AT + timedelta(seconds=observed_seconds),
        positions=(_row(mark=mark),),
    ))


def _signed(
    *, generation="g-position", mark="101.0", pending=False, observed_seconds=4,
):
    return cast(Any, SimpleNamespace(
        trading_account_id=ACCOUNT,
        connector_id=CONNECTOR,
        signed_provider_uid=UID,
        generation_id=generation,
        exchange_position_key=POSITION_KEY,
        position_idx=0,
        pending_exit_mutation=pending,
        observed_at=AT + timedelta(seconds=observed_seconds),
        position=_row(mark=mark),
    ))


def _db(*, account_completed=AT, signed_completed=None):
    signed_completed = signed_completed or AT + timedelta(seconds=3)
    return _DB({
        "g-account": ("COMPLETE", account_completed - timedelta(seconds=1), account_completed),
        "g-position": ("COMPLETE", signed_completed - timedelta(seconds=1), signed_completed),
    })


def test_market_price_and_newer_complete_generation_may_advance_between_signed_reads():
    db = _db()
    account = _account_proof(mark="100.0")
    signed = _signed(mark="101.7")
    _verify_cross_snapshot_position(
        db,
        position=_position(),
        route=_route(),
        account_proof=account,
        account_row=account.positions[0],
        signed=signed,
    )
    assert [call[0] for call in db.calls] == ["g-account", "g-position"]


@pytest.mark.parametrize(("field", "value"), [
    ("size", "2"),
    ("avgPrice", "100.1"),
    ("stopLoss", "89"),
    ("takeProfit", "111"),
    ("trailingStop", "0.70"),
])
def test_stable_position_or_protection_drift_still_fails_closed(field, value):
    db = _db()
    account = _account_proof()
    signed = _signed()
    changed = dict(signed.position)
    changed[field] = value
    signed.position = changed
    with pytest.raises(
        P90RealOpenRestartUnverified,
        match="P90_REAL_RESTART_POSITION_SIGNED_INVENTORY_DRIFT",
    ):
        _verify_cross_snapshot_position(
            db,
            position=_position(),
            route=_route(),
            account_proof=account,
            account_row=account.positions[0],
            signed=signed,
        )


def test_position_generation_may_not_regress_behind_account_wide_snapshot():
    db = _db(
        account_completed=AT + timedelta(seconds=1),
        signed_completed=AT,
    )
    account = _account_proof()
    signed = _signed()
    with pytest.raises(
        P90RealOpenRestartUnverified,
        match="P90_REAL_RESTART_POSITION_SIGNED_GENERATION_REGRESSED",
    ):
        _verify_cross_snapshot_position(
            db,
            position=_position(),
            route=_route(),
            account_proof=account,
            account_row=account.positions[0],
            signed=signed,
        )


def test_unknown_or_wrong_generation_is_not_accepted_as_fresh_proof():
    db = _db()
    signed = _signed(generation="g-foreign")
    account = _account_proof()
    with pytest.raises(
        P90RealOpenRestartUnverified,
        match="P90_REAL_RESTART_POSITION_GENERATION_EVIDENCE_INVALID",
    ):
        _verify_cross_snapshot_position(
            db,
            position=_position(),
            route=_route(),
            account_proof=account,
            account_row=account.positions[0],
            signed=signed,
        )


def test_pending_mutation_or_identity_drift_still_blocks_before_snapshot_compare():
    db = _db()
    account = _account_proof()
    with pytest.raises(
        P90RealOpenRestartUnverified,
        match="P90_REAL_RESTART_POSITION_SIGNED_IDENTITY_DRIFT",
    ):
        _verify_cross_snapshot_position(
            db,
            position=_position(),
            route=_route(),
            account_proof=account,
            account_row=account.positions[0],
            signed=_signed(pending=True),
        )
    assert db.calls == []


def test_mark_price_remains_required_shape_even_though_cross_read_value_is_volatile():
    raw = _row()
    del raw["markPrice"]
    with pytest.raises(
        P90RealOpenRestartUnverified,
        match="P90_REAL_RESTART_SIGNED_POSITION_SHAPE_INCOMPLETE",
    ):
        _stable_position(raw)

def test_account_wide_end_of_read_may_be_up_to_thirty_seconds_after_generation():
    db = _db(
        account_completed=AT,
        signed_completed=AT + timedelta(seconds=20),
    )
    account = _account_proof(observed_seconds=29)
    signed = _signed(observed_seconds=21)
    _verify_cross_snapshot_position(
        db,
        position=_position(),
        route=_route(),
        account_proof=account,
        account_row=account.positions[0],
        signed=signed,
    )


def test_account_wide_generation_older_than_producer_composite_budget_still_blocks():
    db = _db(
        account_completed=AT,
        signed_completed=AT + timedelta(seconds=20),
    )
    account = _account_proof(observed_seconds=31)
    signed = _signed(observed_seconds=21)
    with pytest.raises(
        P90RealOpenRestartUnverified,
        match="P90_REAL_RESTART_POSITION_GENERATION_EVIDENCE_INVALID",
    ):
        _verify_cross_snapshot_position(
            db,
            position=_position(),
            route=_route(),
            account_proof=account,
            account_row=account.positions[0],
            signed=signed,
        )


def test_position_specific_generation_keeps_fifteen_second_start_time_budget():
    db = _db(
        account_completed=AT,
        signed_completed=AT,
    )
    account = _account_proof(observed_seconds=20)
    signed = _signed(observed_seconds=16)
    with pytest.raises(
        P90RealOpenRestartUnverified,
        match="P90_REAL_RESTART_POSITION_GENERATION_EVIDENCE_INVALID",
    ):
        _verify_cross_snapshot_position(
            db,
            position=_position(),
            route=_route(),
            account_proof=account,
            account_row=account.positions[0],
            signed=signed,
        )

