"""REAL Exit pending proof is account scoped, durable and deny-by-default."""
from __future__ import annotations

from dataclasses import replace

import pytest
from test_p90l90_real_observation import _scope

from bybit_workbench.universal_exit.p90l90_real_pending import (
    p90_real_exit_no_pending,
)


class _Cursor:
    def __init__(self, result: tuple[object, ...] | dict[str, object] | None) -> None:
        self._result = result

    def fetchone(self):
        return self._result


class _DB:
    def __init__(self, *, owned: int = 1, pending: bool = False,
                 orphan: bool = False, broken: int | None = None) -> None:
        self.answers: list[tuple[object, ...] | dict[str, object] | None] = [
            {"number": owned}, {"pending": pending}, {"pending": orphan},
        ]
        self.queries: list[tuple[str, tuple[object, ...]]] = []
        self.broken = broken

    def execute(self, statement, parameters=()):
        self.queries.append((statement, tuple(parameters)))
        index = len(self.queries) - 1
        if index == self.broken:
            raise RuntimeError("read-only DB error must deny")
        return _Cursor(self.answers[index])


def _subject(*, sub: bool = False):
    position, _, _, _, _, _ = _scope(sub=sub)
    return position


@pytest.mark.parametrize("sub", (False, True))
def test_exact_account_owner_without_pending_exit_has_read_only_proof(sub: bool) -> None:
    position = _subject(sub=sub)
    db = _DB()
    assert p90_real_exit_no_pending(
        db, position=position, trading_account_id=position.trading_account_id or "",
    ) is True
    assert len(db.queries) == 3
    for sql, parameters in db.queries:
        assert position.strategy_position_id in parameters
        assert "UPDATE " not in sql and "DELETE " not in sql
        assert "INSERT " not in sql
    assert "identity_status='PROVEN'" in db.queries[0][0]
    assert "r.execution_request_id IS NULL" in db.queries[1][0]
    assert "c.state IS DISTINCT FROM 'completed'" in db.queries[2][0]


@pytest.mark.parametrize(("owned", "pending", "orphan"), (
    (0, False, False),
    (2, False, False),
    (1, True, False),
    (1, False, True),
))
def test_absent_duplicate_pending_or_orphan_denied(
    owned: int, pending: bool, orphan: bool,
) -> None:
    position = _subject()
    db = _DB(owned=owned, pending=pending, orphan=orphan)
    assert p90_real_exit_no_pending(
        db, position=position, trading_account_id="ta-main",
    ) is False


@pytest.mark.parametrize("broken", [0, 1, 2])
def test_any_db_read_error_denies(broken: int) -> None:
    position = _subject()
    assert p90_real_exit_no_pending(
        _DB(broken=broken), position=position, trading_account_id="ta-main",
    ) is False


def test_cross_account_or_missing_binding_denied_before_sql() -> None:
    position = _subject()
    for invalid, account in [
        (position, "ta-sub"),
        (replace(position, identity_status="LEGACY_UNKNOWN",
                 trading_account_id=None, strategy_execution_binding_id=None,
                 execution_leg=None), "ta-main"),
    ]:
        db = _DB()
        assert p90_real_exit_no_pending(
            db, position=invalid, trading_account_id=account,
        ) is False
        assert not db.queries


def test_unknown_query_shape_cannot_appear_clear() -> None:
    position = _subject()
    db = _DB()
    db.answers[1] = (0,)  # numeric zero is NOT proof of a real bool False
    assert p90_real_exit_no_pending(
        db, position=position, trading_account_id="ta-main",
    ) is False
