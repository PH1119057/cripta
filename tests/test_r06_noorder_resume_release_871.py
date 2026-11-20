"""R06 NO_ORDER release checkpoint bridge must not depend on stale account ARM.

The verified installer leaves execution permissions and older account ARM rows
unchanged. Gate OFF plus zero ACTIVE global owner ARM means no effective NEW
Entry authority. The R06 release bridge must continue without mutation or
requiring flat Exchange positions. This test is inert, no DB or Bybit.
"""
from __future__ import annotations

import pytest

from operations.monitoring import r06_entry_resume as resume


class _Result:
    def __init__(self, value: object):
        self.value = value

    def fetchone(self) -> tuple[object]:
        return (self.value,)


class _Conn:
    def __init__(
        self,
        *,
        gate: bool = False,
        global_arms: int = 0,
        permissions: int = 2,
        account_arms: int = 5,
        pending: int = 0,
        uncertain: int = 0,
    ) -> None:
        self.gate = gate
        self.global_arms = global_arms
        self.permissions = permissions
        self.account_arms = account_arms
        self.pending = pending
        self.uncertain = uncertain
        self.queries: list[str] = []

    def execute(self, sql: str, _params=None) -> _Result:
        assert sql.lstrip().startswith("SELECT ")
        self.queries.append(sql)
        if "control.execution_gates" in sql:
            return _Result(self.gate)
        if "strategy_entry.execution_permissions" in sql:
            return _Result(self.permissions)
        if "control.live_arm_sessions" in sql:
            return _Result(self.global_arms)
        if "control.account_live_arm_sessions" in sql:
            return _Result(self.account_arms)
        if "strategy_entry.execution_requests" in sql:
            return _Result(self.pending)
        if "runtime.position_ownership" in sql:
            assert "RECONCILIATION_REQUIRED" in sql
            return _Result(self.uncertain)
        raise AssertionError(sql)


def test_noorder_release_resume_accepts_live_like_old_permissions_and_arm() -> None:
    connection = _Conn(
        gate=False,
        global_arms=0,
        permissions=2,
        account_arms=5,
        pending=0,
        uncertain=0,
    )
    resume._require_safe_existing_checkpoint_bridge(connection)
    assert connection.queries
    assert all(q.lstrip().startswith("SELECT ") for q in connection.queries)


@pytest.mark.parametrize("unsafe", (
    {"gate": True},
    {"global_arms": 1},
    {"pending": 1},
    {"uncertain": 1},
))
def test_noorder_bridge_blocks_effective_entry_or_uncertain(unsafe: dict) -> None:
    connection = _Conn(**unsafe)
    with pytest.raises(
        resume.R06ResumeBlocked,
        match="R06_RESUME_RELEASE_GATE_NOT_SAFE",
    ):
        resume._require_safe_existing_checkpoint_bridge(connection)


def test_noorder_bridge_does_not_require_exchange_flat() -> None:
    connection = _Conn(permissions=100, account_arms=25)
    resume._require_safe_existing_checkpoint_bridge(connection)
    assert not any("state='OPEN'" in sql for sql in connection.queries)
