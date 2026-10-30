"""One exact MAIN/SUB private-IPC evidence provider for P90 REAL Exit.

This composes the existing read-only AF_UNIX producer contract, with no
credential, Exchange order, PAPER fallback, or cross-account substitution.
It may supply the independent signed-private port to native/closed Exit
adapters; it does NOT prove source continuity, grant Entry admission,
materialize ExitExecutionRequests, or enable a REAL Observer epoch.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

from .p90l90_real_observation import ScopedPrivatePositionEvidence
from .p90l90_real_private_ipc import P90PrivateIpcUnverified, P90PrivateProofClient


class P90RealPrivateEvidenceRouter:
    """Never accept a caller-requested alternate TradingAccount/connector."""

    def __init__(
        self,
        *,
        account_by_strategy: Mapping[str, str],
        connector_by_strategy: Mapping[str, str],
        timeout_seconds: float = 10.0,
    ) -> None:
        if (
            set(account_by_strategy) != {MAIN_ID, SUB_ID}
            or set(connector_by_strategy) != {MAIN_ID, SUB_ID}
            or len(set(account_by_strategy.values())) != 2
            or len(set(connector_by_strategy.values())) != 2
            or any(
                not isinstance(account, str) or not account
                for account in account_by_strategy.values()
            )
            or any(
                not isinstance(connector, str) or not connector
                for connector in connector_by_strategy.values()
            )
        ):
            raise P90PrivateIpcUnverified(
                "P90_PRIVATE_IPC_MAIN_SUB_BINDINGS_UNPROVEN"
            )
        self._clients = {
            strategy_id: P90PrivateProofClient(
                account=account_by_strategy[strategy_id],
                connector=connector_by_strategy[strategy_id],
                timeout_seconds=timeout_seconds,
            )
            for strategy_id in (MAIN_ID, SUB_ID)
        }
        self._accounts = dict(account_by_strategy)
        self._connectors = dict(connector_by_strategy)

    def _client(self, position: StrategyPosition) -> P90PrivateProofClient:
        strategy_id = position.strategy_id
        if (
            strategy_id not in self._clients
            or position.strategy_version != VERSION
            or position.symbol not in ALLOWED_SYMBOLS[strategy_id]
            or position.identity_status != "PROVEN"
            or position.trading_account_id != self._accounts[strategy_id]
            or not position.strategy_execution_binding_id
        ):
            raise P90PrivateIpcUnverified(
                "P90_PRIVATE_IPC_REAL_POSITION_ACCOUNT_UNPROVEN"
            )
        return self._clients[strategy_id]

    def _verified_result(
        self, position: StrategyPosition, result: ScopedPrivatePositionEvidence,
    ) -> ScopedPrivatePositionEvidence:
        strategy_id = position.strategy_id
        if (
            result.trading_account_id != self._accounts[strategy_id]
            or result.connector_id != self._connectors[strategy_id]
            or result.strategy_execution_binding_id
            != position.strategy_execution_binding_id
            or result.exchange_position_key != position.exchange_position_key
            or result.symbol != position.symbol
            or result.position_idx != position.position_idx
            or result.pending_exit_mutation
        ):
            raise P90PrivateIpcUnverified(
                "P90_PRIVATE_IPC_ROUTED_EVIDENCE_SCOPE_INVALID"
            )
        return result

    def __call__(
        self,
        position: StrategyPosition,
        cursor: TradeCursor,
    ) -> ScopedPrivatePositionEvidence:
        result = self._client(position)(position, cursor)
        return self._verified_result(position, result)

    def for_closed_boundary(
        self,
        position: StrategyPosition,
        closed_at: datetime,
    ) -> ScopedPrivatePositionEvidence:
        result = self._client(position).for_closed_boundary(position, closed_at)
        return self._verified_result(position, result)
