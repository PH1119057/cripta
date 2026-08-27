from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from bybit_workbench.domain.intents import EnterIntent
from bybit_workbench.domain.models import InstrumentRules, OrderRequest
from bybit_workbench.domain.types import OrderSide, PositionSide

from .models import RiskCheck, RiskContext, RiskDecision, RiskProfile


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def normalize_entry_price(intent: EnterIntent, rules: InstrumentRules) -> Decimal:
    if intent.direction is PositionSide.LONG:
        return floor_to_step(intent.entry_price, rules.tick_size)
    return ceil_to_step(intent.entry_price, rules.tick_size)


def normalize_stop_price(intent: EnterIntent, rules: InstrumentRules) -> Decimal:
    if intent.direction is PositionSide.LONG:
        return floor_to_step(intent.stop_price, rules.tick_size)
    return ceil_to_step(intent.stop_price, rules.tick_size)


class RiskEngine:
    def evaluate_entry(
        self,
        intent: EnterIntent,
        profile: RiskProfile,
        context: RiskContext,
        rules: InstrumentRules,
    ) -> RiskDecision:
        checks: list[RiskCheck] = []

        def check(code: str, passed: bool, detail: str) -> None:
            checks.append(RiskCheck(code, passed, detail))

        check("symbol_allowed", intent.symbol in profile.allowed_symbols, intent.symbol)
        check(
            "direction_allowed",
            intent.direction in profile.allowed_directions,
            intent.direction.value,
        )
        check("instrument_matches", intent.symbol == rules.symbol, rules.symbol)
        check(
            "leverage_limit",
            intent.leverage <= profile.max_leverage,
            f"requested={intent.leverage} max={profile.max_leverage}",
        )
        daily_loss_limit = profile.daily_loss_limit(context.equity)
        check(
            "daily_loss_limit",
            context.daily_realized_pnl > -daily_loss_limit,
            (
                f"pnl={context.daily_realized_pnl} limit=-{daily_loss_limit} "
                f"({profile.max_daily_loss_percent}% equity; "
                f"absolute={profile.max_daily_loss or 'off'})"
            ),
        )
        check(
            "loss_streak_limit",
            context.consecutive_losses < profile.max_consecutive_losses,
            f"current={context.consecutive_losses} max={profile.max_consecutive_losses}",
        )
        check(
            "open_position_limit",
            context.open_positions < profile.max_open_positions,
            f"current={context.open_positions} max={profile.max_open_positions}",
        )
        check(
            "pending_entry_limit",
            context.pending_entries < profile.max_pending_entries,
            f"current={context.pending_entries} max={profile.max_pending_entries}",
        )
        market_age = _age_seconds(context.evaluated_at, context.market_data_at)
        private_age = _age_seconds(context.evaluated_at, context.private_stream_at)
        check(
            "market_timestamp_valid",
            context.market_data_at <= context.evaluated_at,
            f"observed={context.market_data_at.isoformat()}",
        )
        check(
            "private_timestamp_valid",
            context.private_stream_at <= context.evaluated_at,
            f"observed={context.private_stream_at.isoformat()}",
        )
        check(
            "market_data_fresh",
            Decimal(str(market_age)) <= profile.max_market_data_age_seconds,
            f"age={market_age:.3f}s max={profile.max_market_data_age_seconds}s",
        )
        check(
            "private_stream_fresh",
            Decimal(str(private_age)) <= profile.max_private_stream_age_seconds,
            f"age={private_age:.3f}s max={profile.max_private_stream_age_seconds}s",
        )
        cooldown_clear = (
            context.cooldown_until is None or context.evaluated_at >= context.cooldown_until
        )
        check("cooldown_clear", cooldown_clear, str(context.cooldown_until))
        check(
            "trading_hour_allowed",
            context.evaluated_at.hour in profile.allowed_utc_hours,
            f"hour={context.evaluated_at.hour} UTC",
        )
        position_clear = context.current_position_side is PositionSide.FLAT
        if not profile.prohibit_position_increase:
            position_clear = True
        check(
            "position_increase_forbidden",
            position_clear,
            context.current_position_side.value,
        )
        protected_for_increase = (
            context.current_position_side is PositionSide.FLAT or context.position_is_protected
        )
        check(
            "position_protected_for_increase",
            protected_for_increase,
            f"protected={context.position_is_protected}",
        )

        liquidation_ok, liquidation_detail = _liquidation_buffer_check(
            intent,
            profile,
            context,
        )
        check("liquidation_buffer", liquidation_ok, liquidation_detail)

        normalized_entry = normalize_entry_price(intent, rules)
        normalized_stop = normalize_stop_price(intent, rules)
        raw_distance = abs(intent.entry_price - intent.stop_price)
        normalized_distance = abs(normalized_entry - normalized_stop)
        conservative_distance = max(raw_distance, normalized_distance)
        risk_limits = []
        if profile.max_risk_amount > 0:
            risk_limits.append(profile.max_risk_amount)
        if profile.max_risk_percent > 0:
            risk_limits.append(context.equity * profile.max_risk_percent / Decimal("100"))
        risk_budget = min(risk_limits)
        friction_per_unit = normalized_entry * (
            profile.max_slippage_percent / Decimal("100")
            + profile.estimated_fee_rate * Decimal("2")
        )
        loss_per_unit = conservative_distance + friction_per_unit
        raw_quantity = risk_budget / loss_per_unit
        notional_cap_quantity = profile.max_position_notional / normalized_entry
        margin_cap_notional = context.available_balance * intent.leverage
        margin_cap_quantity = margin_cap_notional / normalized_entry
        exchange_max_quantity = rules.max_order_qty
        if intent.order_type.value == "Market" and rules.max_market_order_qty is not None:
            exchange_max_quantity = rules.max_market_order_qty
        if intent.requested_notional is None:
            requested_quantity = raw_quantity
        else:
            check(
                "requested_notional_limit",
                intent.requested_notional <= profile.max_position_notional,
                f"requested={intent.requested_notional} max={profile.max_position_notional}",
            )
            check(
                "requested_margin_available",
                intent.requested_notional <= margin_cap_notional,
                f"requested={intent.requested_notional} available_margin={margin_cap_notional}",
            )
            requested_quantity = intent.requested_notional / normalized_entry
        bounded_quantity = min(
            requested_quantity,
            notional_cap_quantity,
            margin_cap_quantity,
            exchange_max_quantity,
        )
        quantity = floor_to_step(bounded_quantity, rules.qty_step)
        minimum_notional_quantity = ceil_to_step(
            rules.min_notional / normalized_entry, rules.qty_step
        )
        minimum_viable_quantity = max(rules.min_order_qty, minimum_notional_quantity)
        minimum_viable_loss = minimum_viable_quantity * loss_per_unit
        minimum_viable_risk_percent = (
            minimum_viable_loss / context.equity * Decimal("100")
        )
        check(
            "minimum_quantity",
            quantity >= rules.min_order_qty,
            f"quantity={quantity} min={rules.min_order_qty}",
        )
        notional = quantity * normalized_entry
        minimum_notional_ok = notional >= rules.min_notional
        check(
            "minimum_notional",
            minimum_notional_ok,
            (
                f"notional={notional} min={rules.min_notional}; "
                f"exchange_min_qty={minimum_viable_quantity}; "
                f"min_loss={minimum_viable_loss}; "
                f"min_risk_pct={minimum_viable_risk_percent:.4f}%"
            ),
        )
        estimated_loss = quantity * loss_per_unit
        estimated_fees = quantity * normalized_entry * profile.estimated_fee_rate * Decimal("2")
        estimated_slippage = (
            quantity * normalized_entry * profile.max_slippage_percent / Decimal("100")
        )
        check(
            "risk_budget_respected",
            estimated_loss <= risk_budget,
            f"loss={estimated_loss} budget={risk_budget}",
        )
        if any(not item.passed for item in checks):
            return RiskDecision(
                False,
                tuple(checks),
                normalized_entry=normalized_entry,
                normalized_stop=normalized_stop,
                candidate_quantity=quantity,
                risk_budget=risk_budget,
                estimated_loss_at_stop=estimated_loss,
                estimated_fees=estimated_fees,
                estimated_slippage=estimated_slippage,
                minimum_viable_quantity=minimum_viable_quantity,
                minimum_viable_loss_at_stop=minimum_viable_loss,
                minimum_viable_risk_percent=minimum_viable_risk_percent,
            )
        side = OrderSide.BUY if intent.direction is PositionSide.LONG else OrderSide.SELL
        order = OrderRequest(
            client_order_id=intent.intent_id,
            symbol=intent.symbol,
            side=side,
            order_type=intent.order_type,
            quantity=quantity,
            price=normalized_entry if intent.order_type.value == "Limit" else None,
        )
        return RiskDecision(
            True,
            tuple(checks),
            normalized_order=order,
            normalized_entry=normalized_entry,
            normalized_stop=normalized_stop,
            candidate_quantity=quantity,
            risk_budget=risk_budget,
            estimated_loss_at_stop=estimated_loss,
            estimated_fees=estimated_fees,
            estimated_slippage=estimated_slippage,
            minimum_viable_quantity=minimum_viable_quantity,
            minimum_viable_loss_at_stop=minimum_viable_loss,
            minimum_viable_risk_percent=minimum_viable_risk_percent,
        )


def _age_seconds(now: datetime, observed_at: datetime) -> float:
    return max(0.0, (now - observed_at).total_seconds())


def _liquidation_buffer_check(
    intent: EnterIntent,
    profile: RiskProfile,
    context: RiskContext,
) -> tuple[bool, str]:
    required = profile.min_liquidation_buffer_percent
    if required == 0:
        return True, "disabled"
    liquidation = context.estimated_liquidation_price
    if liquidation is None:
        return False, "estimated liquidation price is unavailable"
    if intent.direction is PositionSide.LONG:
        distance = intent.stop_price - liquidation
    else:
        distance = liquidation - intent.stop_price
    actual_percent = distance / intent.entry_price * Decimal("100")
    return (
        actual_percent >= required,
        f"actual={actual_percent}% required={required}%",
    )
