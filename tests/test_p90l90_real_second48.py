"""Exact real SECOND48 uses confirmed Exchange close attribution, not PAPER."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION
from bybit_workbench.universal_entry.p90l90_real_second48 import (
    P90RealSecond48Unverified,
    read_real_second48,
)
from bybit_workbench.universal_entry.p90l90_strategy_embargo import StrategyHardStopEmbargoPolicy

AT = datetime(2026, 10, 29, 2, 0, tzinfo=UTC)
POLICY = StrategyHardStopEmbargoPolicy(
    enabled=True, hardstop_count=2, rolling_hours=48,
    new_entry_pause_hours=48, close_existing_positions=False,
    domain="STRATEGY_VERSION",
)


class _DB:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.params: tuple[object, ...] | None = None
        self.locked_account: str | None = None

    def execute(self, sql: str, params: tuple[object, ...]) -> SimpleNamespace:
        if "pg_advisory_xact_lock" in sql:
            assert len(params) == 1
            self.locked_account = str(params[0])
            return SimpleNamespace(fetchall=lambda: [])
        assert self.locked_account is not None
        assert "FROM runtime.position_ownership p" in sql
        assert "runtime.position_exit_attribution a" in sql
        assert "identity.trading_accounts ta" in sql
        self.params = params
        return SimpleNamespace(fetchall=lambda: self.rows)


def _row(
    event_id: str, at: datetime, *,
    mechanism: str = "INITIAL_HARD_STOP",
    account: str = "ta-main", kind: str = "MAIN",
) -> dict[str, object]:
    return {
        "position_id": f"position-{event_id}",
        "state": "CLOSED",
        "closed_at": at,
        "close_link_status": "EXACT",
        "identity_status": "PROVEN",
        "trading_account_id": account,
        "account_kind": kind,
        "attribution_id": f"attribution-{event_id}",
        "attribution_closed_at": at,
        "attribution_link_status": "EXACT",
        "exit_owner": "ALGORITHM",
        "exit_mechanism": mechanism,
        "exit_order_id": f"order-{event_id}",
    }


def _read(
    rows: list[dict[str, object]], *,
    strategy_id: str = MAIN_ID,
    account: str = "ta-main",
    now: datetime = AT,
) -> tuple[bool, dict[str, object]]:
    db = _DB(rows)
    result = read_real_second48(
        db,
        strategy_id=strategy_id,
        strategy_version=VERSION,
        strategy_config_fingerprint="fingerprint-p90-exact",
        trading_account_id=account,
        policy=POLICY,
        evaluated_at=now,
    )
    assert db.params == (strategy_id, VERSION, "fingerprint-p90-exact")
    assert db.locked_account == account
    return result


def test_two_actual_hardstops_within_rolling_48h_pause_only_new_entries() -> None:
    one = _row("1", AT - timedelta(hours=25))
    two = _row("2", AT - timedelta(minutes=5))
    allowed, snapshot = _read([one, two])
    assert not allowed
    assert snapshot["pause_until"] == (
        AT - timedelta(minutes=5) + timedelta(hours=48)
    ).isoformat()
    assert snapshot["strategy_id"] == MAIN_ID
    assert snapshot["policy"]["close_existing_positions"] is False
    assert _read([one, two], now=AT + timedelta(hours=49))[0]


def test_distant_hardstops_and_trailing_are_not_false_hardstop_pairs() -> None:
    old = _row("old", AT - timedelta(hours=80))
    recent = _row("recent", AT - timedelta(hours=2))
    assert _read([old, recent])[0]
    trail = _row(
        "protected", AT - timedelta(hours=1),
        mechanism="PROFIT_PROTECTION_STOP",
    )
    assert _read([recent, trail])[0]


def test_strategy_account_scope_does_not_borrow_main_exit_for_sub() -> None:
    one = _row("sub", AT - timedelta(hours=1), account="ta-sub", kind="SUB")
    assert _read([one], strategy_id=SUB_ID, account="ta-sub")[0]
    with pytest.raises(P90RealSecond48Unverified, match="ACCOUNT_LINEAGE"):
        _read([one], strategy_id=MAIN_ID, account="ta-main")


@pytest.mark.parametrize(
    "key,value",
    [
        ("attribution_id", None),
        ("attribution_link_status", "UNKNOWN"),
        ("close_link_status", "UNRESOLVED_EXACT_LINK"),
        ("exit_mechanism", "UNKNOWN"),
        ("identity_status", "LEGACY_UNKNOWN"),
        ("trading_account_id", "ta-other"),
        ("exit_order_id", None),
        ("closed_at", None),
    ],
)
def test_unproven_close_blocks_real_embargo_reconstruction(
    key: str, value: object,
) -> None:
    row = _row("bad", AT - timedelta(hours=1))
    row[key] = value
    with pytest.raises(P90RealSecond48Unverified):
        _read([row])


def test_open_position_is_allowed_but_reconciliation_required_blocks() -> None:
    open_row = _row("open", AT - timedelta(hours=2))
    open_row.update(
        state="OPEN", closed_at=None, attribution_id=None,
    )
    assert _read([open_row])[0]
    open_row["state"] = "RECONCILIATION_REQUIRED"
    with pytest.raises(P90RealSecond48Unverified, match="UNRESOLVED_POSITION"):
        _read([open_row])


def test_duplicate_attribution_and_wrong_policy_scope_fail_closed() -> None:
    row = _row("duplicate", AT - timedelta(hours=1))
    with pytest.raises(P90RealSecond48Unverified, match="DUPLICATE_POSITION"):
        _read([row, row])
    bad = StrategyHardStopEmbargoPolicy(
        enabled=True, hardstop_count=3, rolling_hours=48,
        new_entry_pause_hours=48, close_existing_positions=False,
        domain="STRATEGY_VERSION",
    )
    with pytest.raises(P90RealSecond48Unverified, match="POLICY_OR_SCOPE"):
        read_real_second48(
            _DB([]), strategy_id=MAIN_ID, strategy_version=VERSION,
            strategy_config_fingerprint="fp", trading_account_id="ta-main",
            policy=bad, evaluated_at=AT,
        )
