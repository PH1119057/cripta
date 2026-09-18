from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import ExitPlan, FrozenPolicy, TradeDirection


class CursorLike(Protocol):
    def fetchone(self) -> Mapping[str, object] | None: ...

    def fetchall(self) -> Sequence[Mapping[str, object]]: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} must be an object")
    return value


def _required_text(row: Mapping[str, object], field: str) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise RuntimeError(f"StrategyPosition exact lineage missing {field}")
    return value


def load_exit_binding(
    connection: ConnectionLike,
    *,
    strategy_position_id: str,
) -> tuple[StrategyPosition, ExitPlan]:
    row = connection.execute(
        """SELECT p.position_id,p.account_ref,p.exchange_position_key,p.position_idx,
                  p.strategy_id,p.strategy_version,p.strategy_config_fingerprint,
                  p.strategy_activation_id,p.signal_id,p.strategy_attempt_id,
                  p.entry_decision_id,p.entry_execution_request_id,
                  p.entry_plan_fingerprint,p.exit_plan_fingerprint,p.entry_command_id,
                  p.symbol,p.side,p.actual_avg_fill,p.actual_qty,p.fill_at,
                  ep.exit_plan_version,ep.plan_json
             FROM runtime.position_ownership p
             JOIN strategy_entry.exit_plans ep
               ON ep.exit_plan_fingerprint=p.exit_plan_fingerprint
              AND ep.strategy_id=p.strategy_id
              AND ep.strategy_version=p.strategy_version
              AND ep.strategy_config_fingerprint=p.strategy_config_fingerprint
            WHERE p.position_id=%s
              AND p.state IN ('OPEN','RECONCILIATION_REQUIRED')
              AND p.bot_instance_id='universal-entry'""",
        (strategy_position_id,),
    ).fetchone()
    if row is None:
        raise KeyError(
            f"logically open Universal StrategyPosition not found: {strategy_position_id}"
        )

    fill_at = row.get("fill_at")
    if not isinstance(fill_at, datetime):
        raise RuntimeError("StrategyPosition fill_at is invalid")
    side = _required_text(row, "side")
    if side == "Buy":
        direction = TradeDirection.LONG
    elif side == "Sell":
        direction = TradeDirection.SHORT
    else:
        raise RuntimeError(f"unsupported StrategyPosition side: {side}")

    raw_position_idx = row.get("position_idx")
    try:
        position_idx = int(str(raw_position_idx))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("StrategyPosition position_idx is invalid") from exc

    position = StrategyPosition(
        strategy_position_id=_required_text(row, "position_id"),
        account_ref=_required_text(row, "account_ref"),
        exchange_position_key=_required_text(row, "exchange_position_key"),
        position_idx=position_idx,
        strategy_id=_required_text(row, "strategy_id"),
        strategy_version=_required_text(row, "strategy_version"),
        strategy_config_fingerprint=_required_text(row, "strategy_config_fingerprint"),
        strategy_activation_id=_required_text(row, "strategy_activation_id"),
        signal_id=_required_text(row, "signal_id"),
        strategy_attempt_id=_required_text(row, "strategy_attempt_id"),
        entry_decision_id=_required_text(row, "entry_decision_id"),
        entry_execution_request_id=_required_text(row, "entry_execution_request_id"),
        entry_plan_fingerprint=_required_text(row, "entry_plan_fingerprint"),
        exit_plan_fingerprint=_required_text(row, "exit_plan_fingerprint"),
        entry_command_id=_required_text(row, "entry_command_id"),
        symbol=_required_text(row, "symbol"),
        direction=direction,
        actual_avg_fill=Decimal(str(row.get("actual_avg_fill"))),
        actual_qty=Decimal(str(row.get("actual_qty"))),
        fill_at=fill_at.astimezone(UTC),
    )

    raw_plan = _mapping(row.get("plan_json"), "ExitPlan.plan_json")
    expected_identity = (
        position.strategy_id,
        position.strategy_version,
        position.strategy_config_fingerprint,
        position.exit_plan_fingerprint,
    )
    actual_identity = (
        str(raw_plan.get("strategy_id") or ""),
        str(raw_plan.get("strategy_version") or ""),
        str(raw_plan.get("strategy_config_fingerprint") or ""),
        str(raw_plan.get("exit_plan_fingerprint") or ""),
    )
    if actual_identity != expected_identity:
        raise RuntimeError("stored ExitPlan/StrategyPosition lineage mismatch")

    exit_policy = _mapping(raw_plan.get("exit_policy"), "ExitPlan.exit_policy")
    protection_policy = _mapping(raw_plan.get("protection_policy"), "ExitPlan.protection_policy")
    plan = ExitPlan(
        strategy_id=position.strategy_id,
        strategy_version=position.strategy_version,
        strategy_config_fingerprint=position.strategy_config_fingerprint,
        strategy_activation_id=position.strategy_activation_id,
        exit_plan_version=_required_text(row, "exit_plan_version"),
        exit_plan_fingerprint=position.exit_plan_fingerprint,
        exit_policy=FrozenPolicy.from_mapping(exit_policy),
        protection_policy=FrozenPolicy.from_mapping(protection_policy),
    )
    return position, plan


def prior_once_rule_ids(
    connection: ConnectionLike,
    *,
    strategy_position_id: str,
    exit_plan_fingerprint: str,
) -> frozenset[str]:
    rows = connection.execute(
        """SELECT DISTINCT rule_id
             FROM strategy_exit.exit_decisions
            WHERE strategy_position_id=%s
              AND exit_plan_fingerprint=%s
              AND repeat_policy='ONCE_PER_POSITION'""",
        (strategy_position_id, exit_plan_fingerprint),
    ).fetchall()
    return frozenset(str(row["rule_id"]) for row in rows)
