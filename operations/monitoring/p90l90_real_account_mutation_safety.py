"""Independent signed MAIN/SUB execution-mutation reattestation for P90 REAL.

Invoked ONLY under the P90 ExitExecution account advisory xact lock.
An ExitDecision and a healthy public WebSocket do not grant Exchange mutation.
A fresh credential-local authenticated signed position must still match the
evaluation's exact protection, size and trading account. No Exchange POST here.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)
from bybit_workbench.universal_exit.contracts import ExitActionKind, ExitEvaluation
from bybit_workbench.universal_exit.p90l90_real_private_router import (
    P90RealPrivateEvidenceRouter,
)

from .p90l90_real_live_source_ports import P90RealLiveSourcePorts

_ACCT = re.compile(r"ta-[0-9a-f]{32}\Z")
_FIELDS = {
    "exchange_stop_price": "stopLoss",
    "exchange_tp_price": "takeProfit",
    "exchange_trailing_distance": "trailingStop",
}


class P90RealIndependentMutationSafety:
    """Fail-closed current-vs-observed Exchange protection under account lock.

    The signed position producer is authenticated to a specific credential/
    Bybit UID; this module never reads or sends credentials. Before any
    dispatch this checks live original source epoch, active immutable
    owner/binding, nonterminal command ledger, and same account's current
    signed Exchange position. A stale or changed protection requires a NEW
    ExitObservation and decision rather than reuse.
    """

    def __init__(
        self,
        connection: Any,
        *,
        source: P90RealLiveSourcePorts,
        signed_private: P90RealPrivateEvidenceRouter,
        account_by_strategy: Mapping[str, str],
        connector_by_strategy: Mapping[str, str],
    ) -> None:
        if (
            not callable(getattr(connection, "execute", None))
            or not isinstance(source, P90RealLiveSourcePorts)
            or not isinstance(signed_private, P90RealPrivateEvidenceRouter)
            or set(account_by_strategy) != {MAIN_ID, SUB_ID}
            or set(connector_by_strategy) != {MAIN_ID, SUB_ID}
            or account_by_strategy[MAIN_ID] == account_by_strategy[SUB_ID]
            or connector_by_strategy[MAIN_ID] == connector_by_strategy[SUB_ID]
            or any(
                not isinstance(a, str) or _ACCT.fullmatch(a) is None
                for a in account_by_strategy.values()
            )
            or any(not isinstance(c, str) or not c
                   for c in connector_by_strategy.values())
        ):
            raise ValueError("P90_REAL_EXIT_INDEPENDENT_ACCOUNT_PROOFS_MISSING")
        self._db = connection
        self._source = source
        self._private = signed_private
        self._accounts = dict(account_by_strategy)
        self._connectors = dict(connector_by_strategy)

    def __call__(
        self,
        position: StrategyPosition,
        evaluation: ExitEvaluation,
        now: datetime,
    ) -> bool:
        """Never upgrade an unproven current Exchange state into authorization."""
        try:
            return self._check(position, evaluation, now)
        except Exception:
            # Exiting is the responsibility of the credential-local
            # reconciler when proof is unavailable; do not fabricate a
            # market close or silently queue a stale mutation.
            return False

    def _check(
        self,
        position: StrategyPosition,
        evaluation: ExitEvaluation,
        now: datetime,
    ) -> bool:
        details = evaluation.observation.attributes.to_dict().get("p90l90_real")
        decision = evaluation.decision
        if (
            decision is None
            or not isinstance(details, Mapping)
            or position.strategy_id not in ALLOWED_SYMBOLS
            or position.strategy_version != VERSION
            or position.symbol not in ALLOWED_SYMBOLS[position.strategy_id]
            or position.identity_status != "PROVEN"
            or position.trading_account_id != self._accounts[position.strategy_id]
            or not position.strategy_execution_binding_id
            or position.initial_protection_confirmed_at is None
            or position.position_idx != 0
            or evaluation.observation.strategy_position_id
               != position.strategy_position_id
            or decision.strategy_position_id != position.strategy_position_id
            or now.tzinfo is None or now.utcoffset() is None
            or not timedelta() <= now.astimezone(UTC)
               - evaluation.observation.received_at.astimezone(UTC)
               <= timedelta(seconds=5)
            or details.get("trading_account_id") != position.trading_account_id
            or details.get("strategy_execution_binding_id")
               != position.strategy_execution_binding_id
            or details.get("exchange_position_key") != position.exchange_position_key
            or details.get("pending_exit_mutation") is not False
        ):
            return False
        self._source._source_transaction()
        nonterminal = self._db.execute(
            """SELECT count(*) FROM runtime.trade_commands
                WHERE trading_account_id=%s
                  AND state NOT IN ('completed','failed','cancelled','rejected')""",
            (position.trading_account_id,),
        ).fetchone()
        if nonterminal != (0,):
            return False
        identity = self._db.execute(
            """SELECT strategy_execution_binding_id,trading_account_id
                FROM strategy_entry.strategy_execution_bindings
                WHERE strategy_execution_binding_id=%s
                  AND strategy_activation_id=%s
                  AND binding_state='ACTIVE'
                  AND execution_leg='PRIMARY'
                  AND execution_role='PRIMARY'
                  AND allocation_mode='DEDICATED_STRATEGY'
                  AND effective_from<=%s
                  AND (effective_to IS NULL OR effective_to>%s)""",
            (
                position.strategy_execution_binding_id,
                position.strategy_activation_id, now, now,
            ),
        ).fetchall()
        if identity != [
            (position.strategy_execution_binding_id, position.trading_account_id)
        ]:
            return False
        current = self._private.for_closed_boundary(
            position, evaluation.observation.event_at,
        )
        if (
            current.trading_account_id != position.trading_account_id
            or current.connector_id != self._connectors[position.strategy_id]
            or current.strategy_execution_binding_id
               != position.strategy_execution_binding_id
            or current.exchange_position_key != position.exchange_position_key
            or current.pending_exit_mutation is not False
            or not timedelta() <= now.astimezone(UTC) -
               current.observed_at.astimezone(UTC) <= timedelta(seconds=5)
        ):
            return False
        signed = current.position
        if (
            str(signed.get("symbol")) != position.symbol
            or str(signed.get("side")) != (
                "Buy" if position.direction.value == "LONG" else "Sell"
            )
            or str(signed.get("positionIdx")) != "0"
            or Decimal(str(signed.get("avgPrice"))) != position.actual_avg_fill
            or Decimal(str(signed.get("size"))) != position.actual_qty
        ):
            return False
        for key, signed_key in _FIELDS.items():
            if (
                Decimal(str(signed.get(signed_key))) !=
                Decimal(str(details.get(key)))
            ):
                return False
        mark = Decimal(str(signed.get("markPrice")))
        stop = Decimal(str(signed.get("stopLoss")))
        tp = Decimal(str(signed.get("takeProfit")))
        if (
            not mark.is_finite() or mark <= 0 or
            not stop.is_finite() or stop <= 0 or
            not tp.is_finite() or tp <= 0
        ):
            return False
        if position.direction.value == "LONG":
            if not stop < mark < tp:
                return False
        elif not tp < mark < stop:
            return False
        if decision.action_kind is ExitActionKind.SET_TRAILING:
            mutation = decision.requested_mutation.to_dict()
            active_price = Decimal(str(mutation["active_price"]))
            if (
                Decimal(str(signed["trailingStop"])) != 0
                or (mark >= active_price if position.direction.value == "LONG"
                    else mark <= active_price)
            ):
                return False
        elif decision.action_kind not in (
            ExitActionKind.SET_TP, ExitActionKind.CLOSE,
        ):
            return False
        return True
