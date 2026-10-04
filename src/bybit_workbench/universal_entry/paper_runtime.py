from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from bybit_workbench.domain.models import Candle

from .context_features import compare_context_value, extract_context_feature
from .contracts import EntryEvaluation, ObjectiveContext, TradeDirection
from .fingerprint import canonical_json, fingerprint
from .market_watch import compute_l53_zone, compute_r1_l53_stable_zone
from .runtime_loader import ActiveStrategyBundle


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _decimal(value: object, label: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{label} must be decimal") from None
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{label} is invalid")
    return result


def _fact_reference(attributes: Mapping[str, object], path: str) -> Decimal:
    if not path.startswith("fact."):
        raise ValueError("paper fact reference must start with fact.")
    value: object = attributes
    for part in path.removeprefix("fact.").split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise ValueError(f"paper fact reference missing: {path}")
        value = value[part]
    return _decimal(value, f"paper fact reference {path}", positive=True)


def _directional_move(entry: Decimal, price: Decimal, direction: str) -> Decimal:
    sign = Decimal("1") if direction == TradeDirection.LONG.value else Decimal("-1")
    return (price / entry - Decimal("1")) * Decimal("100") * sign


def _pnl(quantity: Decimal, entry: Decimal, exit_price: Decimal, direction: str) -> Decimal:
    sign = Decimal("1") if direction == TradeDirection.LONG.value else Decimal("-1")
    return quantity * (exit_price - entry) * sign


def _crossed_limit(direction: str, price: Decimal, limit_price: Decimal) -> bool:
    if direction == TradeDirection.LONG.value:
        return price <= limit_price
    return price >= limit_price


def _crossed_signed_offset(entry: Decimal, price: Decimal, offset_pct: Decimal) -> bool:
    trigger = entry * (Decimal("1") + offset_pct / Decimal("100"))
    return price >= trigger if offset_pct >= 0 else price <= trigger


def _policy_dict(bundle_value: object) -> dict[str, object]:
    value = json.loads(canonical_json(bundle_value))
    if not isinstance(value, dict):
        raise ValueError("policy payload must be an object")
    return {str(key): item for key, item in value.items()}


def _context_exit_triggered(
    policy: Mapping[str, object],
    contexts: Mapping[str, ObjectiveContext],
    observed_at: datetime,
) -> tuple[bool, dict[str, object]]:
    rows = policy.get("context_feature_policy")
    if not isinstance(rows, list):
        return False, {}
    active = [row for row in rows if isinstance(row, Mapping) and row.get("mode") != "OFF"]
    if not active:
        return False, {}
    trigger_mode = str(policy.get("context_trigger_mode") or "")
    if trigger_mode not in {"ANY", "ALL"}:
        raise ValueError("exit context_trigger_mode must be ANY or ALL")
    condition_results: list[bool] = []
    ranking_score = Decimal("0")
    ranking_seen = False
    evidence: list[dict[str, object]] = []
    for row in active:
        scope = str(row.get("scope") or "")
        mode = str(row.get("mode") or "")
        context_key = "dispatcher.global" if scope == "GLOBAL" else "dispatcher.coin"
        context = contexts.get(context_key)
        matched = False
        status = "MISSING"
        value: object | None = None
        if context is not None:
            max_age = int(str(row.get("max_age_seconds") or 0))
            age = (observed_at - context.observed_at).total_seconds()
            status = "FRESH" if 0 <= age <= max_age else "STALE"
            value, feature_status = extract_context_feature(
                context.payload.to_dict(),
                feature_id=str(row.get("feature_id") or ""),
                value_path=str(row.get("value_path") or ""),
            )
            if status == "FRESH" and feature_status != "VALID":
                status = "PARTIAL"
            condition = row.get("condition")
            if status == "FRESH" and isinstance(condition, Mapping):
                matched = compare_context_value(
                    value,
                    str(condition.get("operator") or ""),
                    condition.get("value"),
                )
        if mode == "CONDITION":
            condition_results.append(matched)
        elif mode == "RANKING":
            ranking_seen = True
            if matched:
                ranking_score += _decimal(row.get("weight"), "exit ranking weight")
        evidence.append(
            {
                "feature_id": row.get("feature_id"),
                "scope": scope,
                "mode": mode,
                "status": status,
                "value": value,
                "matched": matched,
                "weight": row.get("weight"),
            }
        )
    criteria = list(condition_results)
    ranking_minimum: Decimal | None = None
    if ranking_seen:
        ranking_policy = _mapping(policy.get("context_ranking_policy"), "exit ranking policy")
        if not bool(ranking_policy.get("enabled", False)):
            raise ValueError("exit RANKING rows require enabled context_ranking_policy")
        ranking_minimum = _decimal(ranking_policy.get("minimum_score"), "exit ranking minimum")
        criteria.append(ranking_score >= ranking_minimum)
    triggered = any(criteria) if trigger_mode == "ANY" else bool(criteria) and all(criteria)
    return triggered, {
        "trigger_mode": trigger_mode,
        "rows": evidence,
        "ranking_score": str(ranking_score) if ranking_seen else None,
        "ranking_minimum_score": str(ranking_minimum) if ranking_minimum is not None else None,
    }


class PaperTradeRuntime:
    """Real-market, zero-mutation Strategy simulation persisted in PostgreSQL."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._l53_candles: dict[str, deque[Candle]] = defaultdict(
            lambda: deque(maxlen=260)
        )

    def on_candle_closed(self, candle: Candle) -> None:
        """Advance causal L5-3 Exit geometry from a closed 5m candle only."""
        if candle.timeframe != "5" or not candle.is_closed:
            return
        history = self._l53_candles[candle.symbol]
        if history and candle.closed_at <= history[-1].closed_at:
            if candle.closed_at == history[-1].closed_at:
                return
            raise ValueError("paper L5-3 candle time regressed")
        history.append(candle)
        r1_zone = compute_r1_l53_stable_zone(tuple(history))
        pending = self._connection.execute(
            """SELECT paper_order_id,direction,payload
                 FROM strategy_entry.paper_orders
                WHERE state='PENDING' AND symbol=%s""",
            (candle.symbol,),
        ).fetchall()
        for pending_row in pending:
            pending_payload = dict(_mapping(pending_row[2], "paper pending payload"))
            if str(pending_payload.get("entry_lifetime_mode") or "") != "SIGNAL_VALIDITY":
                continue
            validity = _mapping(
                pending_payload.get("entry_validity"),
                "paper pending entry_validity",
            )
            if str(validity.get("operator") or "") != "R1_EXACT_SIGNAL":
                raise ValueError("paper SIGNAL_VALIDITY operator unsupported")
            direction = str(pending_row[1])
            expected = (
                None
                if r1_zone is None
                else (
                    r1_zone.support_top
                    if direction == TradeDirection.LONG.value
                    else r1_zone.resistance_bottom
                )
            )
            original = _decimal(
                validity.get("signal_entry_price"),
                "paper R1 original entry",
                positive=True,
            )
            if expected is None or expected != original:
                self._connection.execute(
                    """UPDATE strategy_entry.paper_orders
                          SET state='CANCELLED',updated_at=clock_timestamp()
                        WHERE paper_order_id=%s AND state='PENDING'""",
                    (str(pending_row[0]),),
                )
        zone = compute_l53_zone(tuple(history))
        if zone is None:
            return
        rows = self._connection.execute(
            """SELECT paper_position_id,direction,payload
                 FROM strategy_entry.paper_positions
                WHERE state='OPEN' AND leg_type='PRIMARY' AND symbol=%s""",
            (candle.symbol,),
        ).fetchall()
        for row in rows:
            payload = dict(_mapping(row[2], "paper position payload"))
            exit_policy = _mapping(payload.get("exit_policy"), "paper exit_policy")
            local_zone = _mapping(exit_policy.get("local_zone_exit"), "paper local_zone_exit")
            if not bool(local_zone.get("enabled", False)):
                continue
            if str(local_zone.get("geometry") or "") != "L5-3":
                raise ValueError("paper local_zone_exit unsupported geometry")
            direction = str(row[1])
            target = (
                zone.resistance_bottom
                if direction == TradeDirection.LONG.value
                else zone.support_top
            )
            previous = payload.get("dynamic_take_profit_price")
            if previous is not None and Decimal(str(previous)) == target:
                continue
            payload["dynamic_take_profit_price"] = str(target)
            payload["dynamic_take_profit_observed_at"] = zone.observed_at.isoformat()
            self._connection.execute(
                """UPDATE strategy_entry.paper_positions
                      SET payload=%s::jsonb
                    WHERE paper_position_id=%s AND state='OPEN'""",
                (canonical_json(payload), str(row[0])),
            )
            self._event(
                str(row[0]),
                zone.observed_at,
                "DYNAMIC_TP_MOVED",
                target,
                None,
                None,
                {"geometry": "L5-3", "target_inner": str(target)},
            )

    def create_order(
        self,
        evaluation: EntryEvaluation,
        bundle: ActiveStrategyBundle,
        *,
        now: datetime,
    ) -> str | None:
        intent = evaluation.paper_intent
        card = bundle.card
        activation = bundle.activation
        exit_plan = bundle.exit_plan

        entry_policy = card.entry_policy.to_dict()
        execution_policy = _mapping(
            entry_policy.get("execution_policy"), "paper execution_policy"
        )
        capital_policy = card.capital_policy.to_dict()
        protection_policy = card.protection_policy.to_dict()
        signal_payload = intent.payload.to_dict()
        signal_fact = _mapping(signal_payload.get("signal_fact"), "paper signal_fact")
        attrs = _mapping(signal_fact.get("attributes"), "paper signal attributes")

        reference_path = str(execution_policy.get("reference_value_path") or "")
        if not reference_path.startswith("fact."):
            raise ValueError("paper execution reference_value_path must start with fact.")
        reference_value: object = attrs
        for part in reference_path.removeprefix("fact.").split("."):
            if not isinstance(reference_value, Mapping) or part not in reference_value:
                raise ValueError(f"paper execution reference path missing: {reference_path}")
            reference_value = reference_value[part]
        reference = _decimal(reference_value, "paper reference", positive=True)

        requested_amount = _decimal(
            capital_policy.get("requested_amount"), "paper requested_amount", positive=True
        )
        leverage = int(str(capital_policy.get("leverage") or 0))
        if leverage <= 0:
            raise ValueError("paper leverage must be positive")

        order_type = str(execution_policy.get("order_type") or "").upper()
        lifetime_mode: str | None = None
        if order_type == "MARKET":
            offset = Decimal("0")
            ttl: int | None = None
        elif order_type == "LIMIT_OFFSET":
            offset = _decimal(
                execution_policy.get("entry_offset_pct"),
                "paper execution offset",
                positive=True,
            )
            lifetime_mode = str(
                execution_policy.get("entry_lifetime_mode") or "TIME_TTL"
            ).upper()
            if lifetime_mode == "TIME_TTL":
                ttl = int(str(execution_policy.get("entry_limit_ttl_seconds") or 0))
                if ttl <= 0:
                    raise ValueError("paper TIME_TTL LIMIT_OFFSET requires positive TTL")
            elif lifetime_mode == "SIGNAL_VALIDITY":
                if execution_policy.get("entry_limit_ttl_seconds") not in (None, ""):
                    raise ValueError("paper SIGNAL_VALIDITY cannot carry numeric TTL")
                if str(execution_policy.get("entry_validity_operator") or "") != "R1_EXACT_SIGNAL":
                    raise ValueError("paper SIGNAL_VALIDITY requires R1_EXACT_SIGNAL")
                ttl = None
            else:
                raise ValueError(f"unsupported paper entry lifetime: {lifetime_mode}")
        else:
            raise ValueError(f"unsupported paper order type: {order_type}")

        initial = _mapping(
            protection_policy.get("initial_protection"),
            "paper initial_protection",
        )
        stop_enabled = bool(initial.get("stop_loss_enabled", True))
        target_enabled = bool(initial.get("take_profit_enabled", True))
        stop_loss_pct = (
            _decimal(initial.get("stop_loss_pct"), "paper initial stop", positive=True)
            if stop_enabled
            else None
        )
        take_profit_pct_raw = initial.get("take_profit_pct")
        take_profit_reference_path = str(
            initial.get("take_profit_reference_path") or ""
        ).strip()
        take_profit_pct: Decimal | None = None
        take_profit_price: Decimal | None = None
        if target_enabled:
            if (take_profit_pct_raw in (None, "")) == (not take_profit_reference_path):
                raise ValueError(
                    "paper enabled initial target requires exactly one percent or fact reference"
                )
            take_profit_pct = (
                _decimal(take_profit_pct_raw, "paper initial target", positive=True)
                if not take_profit_reference_path
                else None
            )
            take_profit_price = (
                _fact_reference(attrs, take_profit_reference_path)
                if take_profit_reference_path
                else None
            )
        payload: dict[str, object] = {
            "source": "universal_entry_paper",
            "strategy_attempt_id": intent.strategy_attempt_id,
            "signal_id": intent.signal_id,
            "strategy_id": intent.strategy_id,
            "strategy_version": intent.strategy_version,
            "strategy_config_fingerprint": intent.strategy_config_fingerprint,
            "entry_plan_fingerprint": intent.entry_plan_fingerprint,
            "exit_plan_fingerprint": intent.exit_plan_fingerprint,
            "strategy_activation_id": activation.activation_id,
            "calculated_entry_price": attrs.get("calculated_entry_price"),
            "entry_reference_source": attrs.get("entry_reference_source"),
            "strategy_entry_offset_pct_signed": attrs.get("entry_offset_pct_signed"),
            "strategy_context_features": attrs.get("strategy_context_features"),
            "stake_usdt": str(requested_amount),
            "leverage": leverage,
            "side": "Buy" if intent.direction is TradeDirection.LONG else "Sell",
            "price": str(reference),
            "entry_offset_pct": str(offset),
            "entry_limit_ttl_seconds": ttl,
            "entry_lifetime_mode": lifetime_mode,
            "entry_time_in_force": (
                None
                if order_type == "MARKET"
                else str(execution_policy.get("time_in_force") or "GTC").upper()
            ),
            "entry_validity": (
                None
                if lifetime_mode != "SIGNAL_VALIDITY"
                else {
                    "operator": str(execution_policy.get("entry_validity_operator") or ""),
                    "signal_entry_price": str(reference),
                    "signal_target_price": attrs.get("r1_opposite_inner_target"),
                }
            ),
            "entry_policy": "universal_entry_paper",
            "policy_version": intent.strategy_version,
            "initial_protection": {
                "stop_loss_enabled": stop_enabled,
                "take_profit_enabled": target_enabled,
                **(
                    {"stop_loss_pct": str(stop_loss_pct)}
                    if stop_loss_pct is not None
                    else {}
                ),
                **(
                    {
                        "take_profit_price": str(take_profit_price),
                        "take_profit_reference_path": take_profit_reference_path,
                    }
                    if take_profit_price is not None
                    else (
                        {"take_profit_pct": str(take_profit_pct)}
                        if take_profit_pct is not None
                        else {}
                    )
                ),
                "trigger_by": str(initial.get("trigger_by") or ""),
                "tpsl_mode": str(initial.get("tpsl_mode") or ""),
                "strategy_id": intent.strategy_id,
                "strategy_version": intent.strategy_version,
                "strategy_config_fingerprint": intent.strategy_config_fingerprint,
                "exit_plan_fingerprint": intent.exit_plan_fingerprint,
            },
        }
        if attrs.get("r1_opposite_inner_target") not in (None, ""):
            payload["dynamic_take_profit_price"] = str(
                _decimal(
                    attrs.get("r1_opposite_inner_target"),
                    "paper R1 initial dynamic target",
                    positive=True,
                )
            )
        payload["exit_policy"] = card.exit_policy.to_dict()
        payload["lifecycle_policy"] = card.lifecycle_policy.to_dict()
        payload["paper_model"] = {
            "market_fill": "NEXT_PUBLIC_TRADE",
            "limit_fill": "FIRST_PUBLIC_TRADE_CROSS_AT_LIMIT_PRICE",
            "fees": "NOT_INCLUDED_IN_GROSS_PNL",
        }

        open_row = self._connection.execute(
            """SELECT paper_position_id,direction,entry_price,quantity,best_price,
                      mfe_pct,mae_pct
                 FROM strategy_entry.paper_positions
                WHERE state='OPEN' AND leg_type='PRIMARY'
                  AND strategy_id=%s AND strategy_version=%s AND symbol=%s
                ORDER BY opened_at DESC,paper_position_id DESC LIMIT 1""",
            (intent.strategy_id, intent.strategy_version, intent.symbol),
        ).fetchone()
        forced_flip = False
        if open_row is not None:
            current_direction = str(open_row[1])
            if current_direction == intent.direction.value:
                return None
            old_entry = _decimal(open_row[2], "paper flip old entry", positive=True)
            old_qty = _decimal(open_row[3], "paper flip old quantity", positive=True)
            old_best = _decimal(open_row[4], "paper flip old best", positive=True)
            old_mfe = _decimal(open_row[5], "paper flip old mfe")
            old_mae = _decimal(open_row[6], "paper flip old mae")
            old_move = _directional_move(old_entry, reference, current_direction)
            old_gross = _pnl(old_qty, old_entry, reference, current_direction)
            best = (
                max(old_best, reference)
                if current_direction == TradeDirection.LONG.value
                else min(old_best, reference)
            )
            self._connection.execute(
                """UPDATE strategy_entry.paper_positions
                      SET state='CLOSED',closed_at=%s,exit_price=%s,
                          exit_reason='OPPOSITE_ENTRY_FORCED_FLIP',
                          best_price=%s,mfe_pct=%s,mae_pct=%s,
                          gross_pnl_usdt=%s,gross_return_pct=%s,
                          updated_at=clock_timestamp()
                    WHERE paper_position_id=%s AND state='OPEN'""",
                (
                    intent.observed_at,
                    reference,
                    best,
                    max(old_mfe, old_move),
                    min(old_mae, old_move),
                    old_gross,
                    old_move,
                    str(open_row[0]),
                ),
            )
            self._event(
                str(open_row[0]),
                intent.observed_at,
                "OPPOSITE_ENTRY_FORCED_FLIP",
                reference,
                old_gross,
                old_move,
                {"next_direction": intent.direction.value, "execution_kind": "TAKER"},
            )
            forced_flip = True
            order_type = "MARKET"
            offset = Decimal("0")
            ttl = None
            lifetime_mode = None
            payload["entry_offset_pct"] = "0"
            payload["entry_limit_ttl_seconds"] = None
            payload["entry_lifetime_mode"] = None
            payload["entry_time_in_force"] = None
            payload["entry_validity"] = None
            payload["execution_kind"] = "TAKER_FLIP"

        pending_row = self._connection.execute(
            """SELECT paper_order_id,direction
                 FROM strategy_entry.paper_orders
                WHERE state='PENDING'
                  AND strategy_id=%s AND strategy_version=%s AND symbol=%s
                ORDER BY requested_at DESC,paper_order_id DESC LIMIT 1""",
            (intent.strategy_id, intent.strategy_version, intent.symbol),
        ).fetchone()
        if pending_row is not None:
            if str(pending_row[1]) == intent.direction.value:
                return str(pending_row[0])
            self._connection.execute(
                """UPDATE strategy_entry.paper_orders
                      SET state='CANCELLED',updated_at=clock_timestamp()
                    WHERE paper_order_id=%s AND state='PENDING'""",
                (str(pending_row[0]),),
            )

        limit_price: Decimal | None = None
        expires_at: datetime | None = None
        if order_type == "LIMIT_OFFSET":
            if intent.direction is TradeDirection.LONG:
                limit_price = reference * (Decimal("1") - offset / Decimal("100"))
            else:
                limit_price = reference * (Decimal("1") + offset / Decimal("100"))
            if ttl is not None:
                expires_at = intent.observed_at + timedelta(seconds=ttl)

        paper_order_id = (
            "paper-order-"
            + fingerprint({"strategy_attempt_id": intent.strategy_attempt_id})[:32]
        )
        self._connection.execute(
            """INSERT INTO strategy_entry.paper_orders(
                   paper_order_id,execution_request_id,strategy_activation_id,
                   strategy_id,strategy_version,strategy_config_fingerprint,
                   entry_plan_fingerprint,exit_plan_fingerprint,signal_id,
                   strategy_attempt_id,entry_decision_id,symbol,direction,state,
                   order_type,requested_at,reference_price,limit_price,expires_at,payload)
               VALUES(%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s,'PENDING',
                      %s,%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT(strategy_attempt_id) DO NOTHING""",
            (
                paper_order_id,
                activation.activation_id,
                intent.strategy_id,
                intent.strategy_version,
                intent.strategy_config_fingerprint,
                intent.entry_plan_fingerprint,
                exit_plan.exit_plan_fingerprint,
                intent.signal_id,
                intent.strategy_attempt_id,
                intent.symbol,
                intent.direction.value,
                order_type,
                intent.observed_at,
                reference,
                limit_price,
                expires_at,
                canonical_json(payload),
            ),
        )
        if forced_flip:
            self._fill_specific_order(
                paper_order_id,
                reference,
                intent.observed_at,
            )
        return paper_order_id

    def _fill_specific_order(
        self,
        paper_order_id: str,
        fill_price: Decimal,
        observed_at: datetime,
    ) -> None:
        row = self._connection.execute(
            """SELECT paper_order_id,execution_request_id,strategy_activation_id,
                      strategy_id,strategy_version,strategy_config_fingerprint,
                      entry_plan_fingerprint,exit_plan_fingerprint,signal_id,
                      symbol,direction,order_type,requested_at,limit_price,expires_at,payload
                 FROM strategy_entry.paper_orders
                WHERE paper_order_id=%s AND state='PENDING'""",
            (paper_order_id,),
        ).fetchone()
        if row is None:
            return
        self._open_filled_order(row, fill_price, observed_at)


    def on_public_trade(
        self,
        *,
        symbol: str,
        price: Decimal,
        observed_at: datetime,
        contexts: Mapping[str, ObjectiveContext] | None = None,
    ) -> None:
        self._fill_orders(symbol, price, observed_at)
        self._update_positions(symbol, price, observed_at, contexts or {})

    def _open_filled_order(
        self,
        row: tuple[object, ...],
        fill_price: Decimal,
        observed_at: datetime,
    ) -> None:
        payload = _mapping(row[15], "paper order payload")
        stake = _decimal(payload.get("stake_usdt"), "stake_usdt", positive=True)
        leverage = int(str(payload.get("leverage")))
        notional = stake * Decimal(leverage)
        quantity = notional / fill_price
        position_id = "paper-pos-" + fingerprint({"paper_order_id": row[0]})[:32]
        self._connection.execute(
            """UPDATE strategy_entry.paper_orders
                  SET state='FILLED',filled_at=%s,filled_price=%s,updated_at=clock_timestamp()
                WHERE paper_order_id=%s AND state='PENDING'""",
            (observed_at, fill_price, row[0]),
        )
        self._connection.execute(
            """INSERT INTO strategy_entry.paper_positions(
                   paper_position_id,paper_order_id,parent_position_id,leg_type,
                   strategy_activation_id,strategy_id,strategy_version,
                   strategy_config_fingerprint,entry_plan_fingerprint,
                   exit_plan_fingerprint,signal_id,symbol,direction,state,opened_at,
                   entry_price,stake_usdt,leverage,notional_usdt,quantity,best_price,payload)
               VALUES(%s,%s,NULL,'PRIMARY',%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',
                      %s,%s,%s,%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT(paper_position_id) DO NOTHING""",
            (
                position_id,
                row[0],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
                row[10],
                observed_at,
                fill_price,
                stake,
                leverage,
                notional,
                quantity,
                fill_price,
                canonical_json(dict(payload)),
            ),
        )
        self._event(
            position_id,
            observed_at,
            "OPENED",
            fill_price,
            None,
            Decimal("0"),
            {
                "order_type": row[11],
                "execution_kind": dict(payload).get("execution_kind", "MAKER"),
            },
        )

    def _fill_orders(self, symbol: str, price: Decimal, observed_at: datetime) -> None:
        rows = self._connection.execute(
            """SELECT paper_order_id,execution_request_id,strategy_activation_id,
                      strategy_id,strategy_version,strategy_config_fingerprint,
                      entry_plan_fingerprint,exit_plan_fingerprint,signal_id,
                      symbol,direction,order_type,requested_at,limit_price,expires_at,payload
                 FROM strategy_entry.paper_orders
                WHERE state='PENDING' AND symbol=%s
                ORDER BY requested_at,paper_order_id""",
            (symbol,),
        ).fetchall()
        for row in rows:
            requested_at = row[12].astimezone(UTC)
            if observed_at < requested_at:
                continue
            expires_at = None if row[14] is None else row[14].astimezone(UTC)
            if expires_at is not None and observed_at > expires_at:
                self._connection.execute(
                    """UPDATE strategy_entry.paper_orders
                          SET state='EXPIRED',updated_at=clock_timestamp()
                        WHERE paper_order_id=%s AND state='PENDING'""",
                    (row[0],),
                )
                continue
            direction = str(row[10])
            order_type = str(row[11])
            payload = _mapping(row[15], "paper order payload")
            if str(payload.get("entry_lifetime_mode") or "") == "SIGNAL_VALIDITY":
                validity = _mapping(
                    payload.get("entry_validity"),
                    "paper pending entry_validity",
                )
                target = _decimal(
                    validity.get("signal_target_price"),
                    "paper R1 pending target",
                    positive=True,
                )
                target_hit = (
                    direction == TradeDirection.LONG.value and price >= target
                ) or (
                    direction == TradeDirection.SHORT.value and price <= target
                )
                if target_hit:
                    self._connection.execute(
                        """UPDATE strategy_entry.paper_orders
                              SET state='CANCELLED',updated_at=clock_timestamp()
                            WHERE paper_order_id=%s AND state='PENDING'""",
                        (row[0],),
                    )
                    continue
            fill_price = price
            if order_type == "LIMIT_OFFSET":
                limit_price = _decimal(row[13], "paper limit", positive=True)
                if not _crossed_limit(direction, price, limit_price):
                    continue
                fill_price = limit_price
            self._open_filled_order(row, fill_price, observed_at)

    def _update_positions(
        self,
        symbol: str,
        price: Decimal,
        observed_at: datetime,
        contexts: Mapping[str, ObjectiveContext],
    ) -> None:
        rows = self._connection.execute(
            """SELECT paper_position_id,parent_position_id,leg_type,direction,opened_at,
                      entry_price,stake_usdt,leverage,notional_usdt,quantity,best_price,
                      mfe_pct,mae_pct,active_stop_price,trailing_active,hedge_opened,payload
                 FROM strategy_entry.paper_positions
                WHERE state='OPEN' AND symbol=%s
                ORDER BY opened_at,paper_position_id""",
            (symbol,),
        ).fetchall()
        for row in rows:
            self._update_one(row, price, observed_at, contexts)

    def _update_one(
        self,
        row: tuple[object, ...],
        price: Decimal,
        observed_at: datetime,
        contexts: Mapping[str, ObjectiveContext],
    ) -> None:
        position_id = str(row[0])
        leg_type = str(row[2])
        direction = str(row[3])
        if not isinstance(row[4], datetime):
            raise ValueError("paper opened_at must be datetime")
        opened_at = row[4].astimezone(UTC)
        entry = _decimal(row[5], "paper entry", positive=True)
        notional = _decimal(row[8], "paper notional", positive=True)
        quantity = _decimal(row[9], "paper quantity", positive=True)
        best = _decimal(row[10], "paper best", positive=True)
        old_mfe = _decimal(row[11], "paper mfe")
        old_mae = _decimal(row[12], "paper mae")
        active_stop = None if row[13] is None else _decimal(row[13], "active stop", positive=True)
        trailing_active = bool(row[14])
        hedge_opened = bool(row[15])
        payload = dict(_mapping(row[16], "paper position payload"))
        move = _directional_move(entry, price, direction)
        mfe = max(old_mfe, move)
        mae = min(old_mae, move)
        best = max(best, price) if direction == TradeDirection.LONG.value else min(best, price)
        exit_policy = _mapping(payload.get("exit_policy"), "paper exit_policy")
        lifecycle_policy = _mapping(payload.get("lifecycle_policy"), "paper lifecycle_policy")
        reason: str | None = None

        stop_pct: Decimal | None = None
        tp_pct: Decimal | None = None
        tp_price: Decimal | None = None
        if leg_type == "PRIMARY":
            initial = _mapping(payload.get("initial_protection"), "paper initial protection")
            if bool(initial.get("stop_loss_enabled", True)):
                stop_pct = _decimal(initial.get("stop_loss_pct"), "paper stop", positive=True)
            if bool(initial.get("take_profit_enabled", True)):
                if initial.get("take_profit_price") not in (None, ""):
                    tp_price = _decimal(
                        initial.get("take_profit_price"), "paper take profit price", positive=True
                    )
                else:
                    tp_pct = _decimal(
                        initial.get("take_profit_pct"), "paper take profit", positive=True
                    )
            dynamic_target = payload.get("dynamic_take_profit_price")
            if dynamic_target not in (None, ""):
                tp_price = _decimal(
                    dynamic_target, "paper dynamic take profit price", positive=True
                )
                tp_pct = None
        else:
            hedge_leg = _mapping(payload.get("hedge_leg_policy"), "paper hedge leg policy")
            hedge_stop = _mapping(hedge_leg.get("stop_loss"), "paper hedge stop")
            hedge_take = _mapping(hedge_leg.get("take_profit"), "paper hedge take profit")
            if bool(hedge_stop.get("enabled", False)):
                stop_pct = _decimal(hedge_stop.get("percent"), "paper hedge stop", positive=True)
            if bool(hedge_take.get("enabled", False)):
                tp_pct = _decimal(
                    hedge_take.get("percent"), "paper hedge take profit", positive=True
                )
        if stop_pct is not None and move <= -stop_pct:
            reason = "HARD_STOP"
        elif tp_pct is not None and move >= tp_pct:
            reason = "TAKE_PROFIT"
        elif tp_price is not None and (
            (direction == TradeDirection.LONG.value and price >= tp_price)
            or (direction == TradeDirection.SHORT.value and price <= tp_price)
        ):
            reason = "TAKE_PROFIT"

        be = _mapping(exit_policy.get("break_even"), "paper break_even")
        if reason is None and bool(be.get("enabled", False)):
            activation = _decimal(be.get("activation_profit_pct"), "BE activation")
            buffer_pct = _decimal(be.get("buffer_pct", 0), "BE buffer")
            if mfe >= activation:
                if direction == TradeDirection.LONG.value:
                    active_stop = entry * (Decimal("1") + buffer_pct / Decimal("100"))
                else:
                    active_stop = entry * (Decimal("1") - buffer_pct / Decimal("100"))

        trailing = _mapping(exit_policy.get("trailing"), "paper trailing")
        if reason is None and bool(trailing.get("enabled", False)):
            activation = _decimal(trailing.get("activation_profit_pct"), "trailing activation")
            distance = _decimal(trailing.get("distance_pct"), "trailing distance", positive=True)
            if mfe >= activation:
                trailing_active = True
                if direction == TradeDirection.LONG.value:
                    trail_stop = best * (Decimal("1") - distance / Decimal("100"))
                    active_stop = (
                        trail_stop if active_stop is None else max(active_stop, trail_stop)
                    )
                else:
                    trail_stop = best * (Decimal("1") + distance / Decimal("100"))
                    active_stop = (
                        trail_stop if active_stop is None else min(active_stop, trail_stop)
                    )
        if reason is None and active_stop is not None:
            stop_hit = (direction == TradeDirection.LONG.value and price <= active_stop) or (
                direction == TradeDirection.SHORT.value and price >= active_stop
            )
            if stop_hit:
                reason = "PROTECTION_STOP"

        time_exit = _mapping(exit_policy.get("time_exit"), "paper time_exit")
        if reason is None and bool(time_exit.get("enabled", False)):
            horizon = int(str(time_exit.get("horizon_minutes") or 0))
            if horizon > 0 and observed_at >= opened_at + timedelta(minutes=horizon):
                reason = "TIME_EXIT"

        if reason is None:
            context_triggered, context_evidence = _context_exit_triggered(
                exit_policy, contexts, observed_at
            )
            if context_triggered:
                payload["last_exit_context_evidence"] = context_evidence
                reason = "CONTEXT_EXIT"

        if leg_type == "PRIMARY" and not hedge_opened:
            hedge = _mapping(lifecycle_policy.get("hedge_policy"), "paper hedge_policy")
            if bool(hedge.get("enabled", False)):
                trigger = _mapping(hedge.get("trigger"), "paper hedge trigger")
                offset = _decimal(trigger.get("offset_pct_signed"), "hedge trigger offset")
                if _crossed_signed_offset(entry, price, offset):
                    self._open_hedge(
                        position_id,
                        direction,
                        observed_at,
                        price,
                        notional,
                        payload,
                        hedge,
                    )
                    hedge_opened = True

        if reason is not None:
            gross = _pnl(quantity, entry, price, direction)
            self._connection.execute(
                """UPDATE strategy_entry.paper_positions
                      SET state='CLOSED',closed_at=%s,exit_price=%s,exit_reason=%s,
                          best_price=%s,mfe_pct=%s,mae_pct=%s,active_stop_price=%s,
                          trailing_active=%s,hedge_opened=%s,gross_pnl_usdt=%s,
                          gross_return_pct=%s,payload=%s::jsonb,updated_at=clock_timestamp()
                    WHERE paper_position_id=%s AND state='OPEN'""",
                (
                    observed_at,
                    price,
                    reason,
                    best,
                    mfe,
                    mae,
                    active_stop,
                    trailing_active,
                    hedge_opened,
                    gross,
                    move,
                    canonical_json(payload),
                    position_id,
                ),
            )
            self._event(position_id, observed_at, "CLOSED", price, gross, move, {"reason": reason})
            return
        self._connection.execute(
            """UPDATE strategy_entry.paper_positions
                  SET best_price=%s,mfe_pct=%s,mae_pct=%s,active_stop_price=%s,
                      trailing_active=%s,hedge_opened=%s,payload=%s::jsonb,
                      updated_at=clock_timestamp()
                WHERE paper_position_id=%s AND state='OPEN'""",
            (
                best,
                mfe,
                mae,
                active_stop,
                trailing_active,
                hedge_opened,
                canonical_json(payload),
                position_id,
            ),
        )

    def _open_hedge(
        self,
        primary_position_id: str,
        primary_direction: str,
        opened_at: datetime,
        price: Decimal,
        primary_notional: Decimal,
        primary_payload: Mapping[str, object],
        hedge: Mapping[str, object],
    ) -> None:
        capital = _mapping(hedge.get("capital"), "paper hedge capital")
        size_pct = _decimal(
            capital.get("size_percent_of_primary"), "hedge size_percent", positive=True
        )
        leverage = int(str(capital.get("leverage")))
        hedge_notional = primary_notional * size_pct / Decimal("100")
        quantity = hedge_notional / price
        direction = (
            TradeDirection.SHORT.value
            if primary_direction == TradeDirection.LONG.value
            else TradeDirection.LONG.value
        )
        primary = self._connection.execute(
            """SELECT strategy_activation_id,strategy_id,strategy_version,
                      strategy_config_fingerprint,entry_plan_fingerprint,
                      exit_plan_fingerprint,signal_id,stake_usdt,symbol
                 FROM strategy_entry.paper_positions WHERE paper_position_id=%s""",
            (primary_position_id,),
        ).fetchone()
        if primary is None:
            raise RuntimeError("primary paper position disappeared")
        position_id = (
            "paper-hedge-" + fingerprint({"primary_position_id": primary_position_id})[:32]
        )
        hedge_payload = dict(primary_payload)
        hedge_payload["hedge_leg_policy"] = dict(hedge)
        # Hedge SL/TP/trailing are evaluated from the hedge leg policy itself.
        hedge_payload["exit_policy"] = {
            "break_even": {"enabled": False},
            "trailing": dict(_mapping(hedge.get("trailing"), "hedge trailing")),
            "time_exit": {"enabled": False},
            "context_feature_policy": [],
        }
        self._connection.execute(
            """INSERT INTO strategy_entry.paper_positions(
                   paper_position_id,paper_order_id,parent_position_id,leg_type,
                   strategy_activation_id,strategy_id,strategy_version,
                   strategy_config_fingerprint,entry_plan_fingerprint,
                   exit_plan_fingerprint,signal_id,symbol,direction,state,opened_at,
                   entry_price,stake_usdt,leverage,notional_usdt,quantity,best_price,payload)
               VALUES(%s,NULL,%s,'HEDGE',%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',
                      %s,%s,%s,%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT(paper_position_id) DO NOTHING""",
            (
                position_id,
                primary_position_id,
                primary[0],
                primary[1],
                primary[2],
                primary[3],
                primary[4],
                primary[5],
                primary[6],
                primary[8],
                direction,
                opened_at,
                price,
                _decimal(primary[7], "primary stake", positive=True) * size_pct / Decimal("100"),
                leverage,
                hedge_notional,
                quantity,
                price,
                canonical_json(hedge_payload),
            ),
        )
        self._event(position_id, opened_at, "HEDGE_OPENED", price, None, None, {})

    def _event(
        self,
        position_id: str,
        occurred_at: datetime,
        event_type: str,
        price: Decimal | None,
        pnl: Decimal | None,
        return_pct: Decimal | None,
        payload: Mapping[str, object],
    ) -> None:
        self._connection.execute(
            """INSERT INTO strategy_entry.paper_position_events(
                   paper_position_id,occurred_at,event_type,price,pnl_usdt,return_pct,payload)
               VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)""",
            (
                position_id,
                occurred_at.astimezone(UTC),
                event_type,
                price,
                pnl,
                return_pct,
                canonical_json(payload),
            ),
        )
