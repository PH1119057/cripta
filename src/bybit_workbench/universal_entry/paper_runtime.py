from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .context_features import compare_context_value, extract_context_feature
from .contracts import EntryEvaluation, ObjectiveContext, TradeDirection
from .execution_bridge import BridgePolicyBundle, prepare_runtime_entry_command
from .fingerprint import canonical_json, fingerprint
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

    def create_order(
        self,
        evaluation: EntryEvaluation,
        bundle: ActiveStrategyBundle,
        *,
        now: datetime,
    ) -> str | None:
        request = evaluation.execution_request
        if request is None:
            return None
        card = bundle.card
        activation = bundle.activation
        entry_plan = bundle.entry_plan
        exit_plan = bundle.exit_plan
        policy_bundle = BridgePolicyBundle(
            strategy_card=_policy_dict(card),
            entry_plan=_policy_dict(entry_plan),
            exit_plan=_policy_dict(exit_plan),
            activation=_policy_dict(activation),
        )
        prepared = prepare_runtime_entry_command(request, policy_bundle, now=now)
        payload = dict(prepared.payload)
        payload["exit_policy"] = card.exit_policy.to_dict()
        payload["lifecycle_policy"] = card.lifecycle_policy.to_dict()
        payload["paper_model"] = {
            "market_fill": "NEXT_PUBLIC_TRADE",
            "limit_fill": "FIRST_PUBLIC_TRADE_CROSS_AT_LIMIT_PRICE",
            "fees": "NOT_INCLUDED_IN_GROSS_PNL",
        }
        reference = _decimal(payload["price"], "paper reference", positive=True)
        offset = _decimal(payload.get("entry_offset_pct", 0), "paper execution offset")
        order_type = "MARKET" if offset == 0 else "LIMIT_OFFSET"
        limit_price: Decimal | None = None
        expires_at: datetime | None = None
        if order_type == "LIMIT_OFFSET":
            if prepared.direction is TradeDirection.LONG:
                limit_price = reference * (Decimal("1") - offset / Decimal("100"))
            else:
                limit_price = reference * (Decimal("1") + offset / Decimal("100"))
            ttl = int(str(payload.get("entry_limit_ttl_seconds") or 0))
            if ttl <= 0:
                raise ValueError("paper LIMIT_OFFSET requires positive TTL")
            expires_at = request.requested_at + timedelta(seconds=ttl)
        paper_order_id = (
            "paper-order-"
            + fingerprint({"execution_request_id": request.execution_request_id})[:32]
        )
        self._connection.execute(
            """INSERT INTO strategy_entry.paper_orders(
                   paper_order_id,execution_request_id,strategy_activation_id,
                   strategy_id,strategy_version,strategy_config_fingerprint,
                   entry_plan_fingerprint,exit_plan_fingerprint,signal_id,
                   strategy_attempt_id,entry_decision_id,symbol,direction,state,
                   order_type,requested_at,reference_price,limit_price,expires_at,payload)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',
                      %s,%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT(execution_request_id) DO NOTHING""",
            (
                paper_order_id,
                request.execution_request_id,
                activation.activation_id,
                request.strategy_id,
                request.strategy_version,
                request.strategy_config_fingerprint,
                request.entry_plan_fingerprint,
                exit_plan.exit_plan_fingerprint,
                request.signal_id,
                request.strategy_attempt_id,
                request.entry_decision_id,
                request.symbol,
                request.direction.value,
                order_type,
                request.requested_at,
                reference,
                limit_price,
                expires_at,
                canonical_json(payload),
            ),
        )
        return paper_order_id

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
            fill_price = price
            if order_type == "LIMIT_OFFSET":
                limit_price = _decimal(row[13], "paper limit", positive=True)
                if not _crossed_limit(direction, price, limit_price):
                    continue
                fill_price = limit_price
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
                    direction,
                    observed_at,
                    fill_price,
                    stake,
                    leverage,
                    notional,
                    quantity,
                    fill_price,
                    canonical_json(payload),
                ),
            )
            self._event(position_id, observed_at, "OPENED", fill_price, None, None, {})

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
        if leg_type == "PRIMARY":
            initial = _mapping(payload.get("initial_protection"), "paper initial protection")
            stop_pct = _decimal(initial.get("stop_loss_pct"), "paper stop", positive=True)
            tp_pct = _decimal(initial.get("take_profit_pct"), "paper take profit", positive=True)
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
