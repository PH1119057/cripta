"""READ-ONLY exact MAIN/SUB REAL Exit position-to-plan ownership adapter.

This supplies only the durable StrategyPosition + immutable ExitPlan binding.
It does not prove current Exchange state, attached TP/SL or native continuity:
signed private, live WS and position protection are separate required ports.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import ExitPlan
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)

from .loader import load_exit_binding


class _Cursor(Protocol):
    def fetchall(self) -> Sequence[Mapping[str, object]]: ...


class _Connection(Protocol):
    def execute(self, sql: str, params: Sequence[object] = ()) -> _Cursor: ...


class P90RealPositionOwnershipUnverified(RuntimeError):
    """Exact Strategy/TradingAccount/Exchange position cannot be proven."""


class P90RealPositionProvider:
    """Account-scoped DB binding; never grant trading rights from a row alone."""

    def __init__(
        self,
        connection: _Connection,
        *,
        account_by_strategy: Mapping[str, str],
    ) -> None:
        if (
            set(account_by_strategy) != {MAIN_ID, SUB_ID}
            or any(
                not isinstance(value, str) or not value.strip()
                for value in account_by_strategy.values()
            )
            or len(set(account_by_strategy.values())) != 2
        ):
            raise P90RealPositionOwnershipUnverified(
                "P90_REAL_POSITION_INDEPENDENT_MAIN_SUB_BINDINGS_REQUIRED"
            )
        self._connection = connection
        self._accounts = dict(account_by_strategy)

    def __call__(self, symbol: str) -> tuple[tuple[StrategyPosition, ExitPlan], ...]:
        strategy_ids = [
            strategy_id
            for strategy_id, symbols in ALLOWED_SYMBOLS.items()
            if symbol in symbols
        ]
        if len(strategy_ids) != 1:
            raise P90RealPositionOwnershipUnverified(
                "P90_REAL_POSITION_SYMBOL_OWNER_UNVERIFIED"
            )
        strategy_id = strategy_ids[0]
        account = self._accounts[strategy_id]
        rows = self._connection.execute(
            """SELECT position_id,state,strategy_id,strategy_version,
                      identity_status,trading_account_id,
                      exchange_position_key,position_idx
                 FROM runtime.position_ownership
                WHERE symbol=%s AND trading_account_id=%s
                  AND state IN ('OPEN','RECONCILIATION_REQUIRED')
                ORDER BY fill_at,position_id""",
            (symbol, account),
        ).fetchall()
        if len(rows) > 1:
            raise P90RealPositionOwnershipUnverified(
                "P90_REAL_POSITION_DUPLICATE_PHYSICAL_OWNER"
            )
        if not rows:
            return ()
        row = rows[0]
        expected_key = exchange_position_key(account, symbol, 0)
        if (
            not isinstance(row, Mapping)
            or row.get("state") != "OPEN"
            or row.get("strategy_id") != strategy_id
            or row.get("strategy_version") != VERSION
            or row.get("identity_status") != "PROVEN"
            or row.get("trading_account_id") != account
            or row.get("exchange_position_key") != expected_key
            or str(row.get("position_idx")) != "0"
            or not str(row.get("position_id") or "")
        ):
            raise P90RealPositionOwnershipUnverified(
                "P90_REAL_POSITION_ACCOUNT_OR_STATE_UNVERIFIED"
            )
        position, plan = load_exit_binding(
            self._connection,
            strategy_position_id=str(row["position_id"]),
        )
        if (
            position.strategy_position_id != row["position_id"]
            or position.strategy_id != strategy_id
            or position.strategy_version != VERSION
            or position.trading_account_id != account
            or position.identity_status != "PROVEN"
            or position.symbol != symbol
            or position.position_idx != 0
            or position.exchange_position_key != expected_key
            or position.initial_protection_confirmed_at is None
            or plan.exit_plan_fingerprint != position.exit_plan_fingerprint
            or plan.strategy_activation_id != position.strategy_activation_id
        ):
            raise P90RealPositionOwnershipUnverified(
                "P90_REAL_POSITION_EXIT_PLAN_OR_PROTECTION_UNVERIFIED"
            )
        return ((position, plan),)
