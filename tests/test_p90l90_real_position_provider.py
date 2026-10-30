"""P90 REAL Exit enumerates only verified MAIN/SUB owned positions and plans."""
from __future__ import annotations

from dataclasses import replace

import pytest
from test_p90l90_real_observation import _scope

from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_exit import p90l90_real_position_provider as module
from bybit_workbench.universal_exit.p90l90_real_position_provider import (
    P90RealPositionOwnershipUnverified,
    P90RealPositionProvider,
)


def _row(position):
    return {
        "position_id": position.strategy_position_id,
        "state": "OPEN",
        "strategy_id": position.strategy_id,
        "strategy_version": VERSION,
        "identity_status": "PROVEN",
        "trading_account_id": position.trading_account_id,
        "exchange_position_key": position.exchange_position_key,
        "position_idx": 0,
    }


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def execute(self, query, parameters=()):
        self.queries.append((query, tuple(parameters)))
        return _Cursor(self.rows)


def _provider(db):
    return P90RealPositionProvider(
        db, account_by_strategy={MAIN_ID: "ta-main", SUB_ID: "ta-sub"},
    )


@pytest.mark.parametrize("sub", (False, True))
def test_exact_position_and_plan_are_returned_from_correct_account(
    monkeypatch, sub: bool,
):
    position, plan, *_ = _scope(sub=sub)
    db = _DB([_row(position)])
    calls = []

    def load(_connection, *, strategy_position_id):
        calls.append(strategy_position_id)
        return position, plan

    monkeypatch.setattr(module, "load_exit_binding", load)
    assert _provider(db)(position.symbol) == ((position, plan),)
    assert db.queries[0][1] == (position.symbol, position.trading_account_id)
    assert calls == [position.strategy_position_id]


def test_flat_is_empty_without_inventing_a_strategy_position() -> None:
    db = _DB([])
    assert _provider(db)("LTCUSDT") == ()
    assert db.queries == [
        (
            db.queries[0][0],
            ("LTCUSDT", "ta-main"),
        )
    ]


@pytest.mark.parametrize("tamper", (
    {"state": "RECONCILIATION_REQUIRED"},
    {"identity_status": "LEGACY_UNKNOWN"},
    {"trading_account_id": "ta-sub"},
    {"strategy_id": SUB_ID},
    {"strategy_version": "old-version"},
    {"exchange_position_key": "foreign-key"},
    {"position_idx": 1},
    {"position_id": ""},
))
def test_wrong_account_strategy_or_unresolved_state_fails_closed(
    tamper: dict[str, object],
):
    position, *_ = _scope()
    row = _row(position)
    row.update(tamper)
    with pytest.raises(P90RealPositionOwnershipUnverified):
        _provider(_DB([row]))("LTCUSDT")


def test_duplicate_open_owner_fails_closed() -> None:
    position, *_ = _scope()
    with pytest.raises(P90RealPositionOwnershipUnverified, match="DUPLICATE"):
        _provider(_DB([_row(position), _row(position)]))("LTCUSDT")


def test_wrong_plan_and_missing_protection_fail_closed(monkeypatch):
    position, plan, *_ = _scope()
    db = _DB([_row(position)])
    monkeypatch.setattr(
        module, "load_exit_binding",
        lambda *_args, **_kwargs: (
            replace(position, initial_protection_confirmed_at=None), plan
        ),
    )
    with pytest.raises(P90RealPositionOwnershipUnverified, match="PROTECTION"):
        _provider(db)("LTCUSDT")


def test_incomplete_or_colliding_account_bindings_rejected() -> None:
    for scope in (
        {MAIN_ID: "ta-main"},
        {MAIN_ID: "same", SUB_ID: "same"},
        {MAIN_ID: "", SUB_ID: "ta-sub"},
    ):
        with pytest.raises(P90RealPositionOwnershipUnverified):
            P90RealPositionProvider(_DB([]), account_by_strategy=scope)


def test_symbol_not_in_approved_real_universe_never_queries_db() -> None:
    db = _DB([])
    with pytest.raises(P90RealPositionOwnershipUnverified):
        _provider(db)("BTCUSDT")
    assert not db.queries
