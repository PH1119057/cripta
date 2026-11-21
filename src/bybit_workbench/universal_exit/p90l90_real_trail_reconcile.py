"""Verified present-time P90 REAL native TRAIL035 recovery observation.

Never replays old MFE or borrows a now-expired first-fill source window.
Only signed current OPEN/SL/TP and original fresh MarketPublication LTP
are permitted. All pre-release positions lack the durable per-fill marker.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.market_data.r10_execution_publication import R10ExecutionQuote
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import FrozenPolicy, TradeDirection
from bybit_workbench.universal_entry.fingerprint import fingerprint
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)
from bybit_workbench.universal_exit.contracts import ExitObservation
from bybit_workbench.universal_exit.p90l90_real_observation import (
    ScopedPrivatePositionEvidence,
)
from bybit_workbench.universal_exit.p90l90_real_v3 import (
    P90_TRAIL_RECOVERY_POLICY,
)


class P90TrailRecoveryUnverified(RuntimeError):
    """No safe current original-market + authenticated Exchange proof."""


def _positive(value: object) -> Decimal:
    try:
        number = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        raise P90TrailRecoveryUnverified(
            "P90_TRAIL_RECOVERY_PRICE_NOT_PROVEN"
        ) from None
    if not number.is_finite() or number <= 0:
        raise P90TrailRecoveryUnverified(
            "P90_TRAIL_RECOVERY_PRICE_NOT_PROVEN"
        )
    return number


def build_p90_trail_recovery_observation(
    *,
    position: StrategyPosition,
    private: ScopedPrivatePositionEvidence,
    quote: R10ExecutionQuote,
    eligibility: object,
    expected_source_session_id: str,
    received_at: datetime,
) -> ExitObservation:
    """Return immutable NEW current-state observation; no Bybit/DB write.

    An eligible OPEN position was created while this recovery contract was
    physically loaded. Existing 2026-11-19 INJ lacks the marker and is blocked
    independent of how old or favorable its historical MFE becomes.
    """
    if (
        eligibility != P90_TRAIL_RECOVERY_POLICY
        or position.identity_status != "PROVEN"
        or position.strategy_id not in ALLOWED_SYMBOLS
        or position.strategy_version != VERSION
        or position.symbol not in ALLOWED_SYMBOLS[position.strategy_id]
        or not position.trading_account_id
        or not position.strategy_execution_binding_id
        or position.position_idx != 0
        or position.initial_protection_confirmed_at is None
        or position.exchange_position_key != exchange_position_key(
            position.trading_account_id, position.symbol, 0,
        )
        or private.trading_account_id != position.trading_account_id
        or private.strategy_execution_binding_id
           != position.strategy_execution_binding_id
        or private.exchange_position_key != position.exchange_position_key
        or private.symbol != position.symbol
        or private.position_idx != position.position_idx
        or private.source_ref != "bybit:signed-position-list:exact-account"
        or not private.signed_provider_uid.isdigit()
        or not private.connector_id or not private.generation_id
        or private.pending_exit_mutation is not False
        or quote.symbol != position.symbol
        or quote.source_session_id != expected_source_session_id
        or not quote.producer_release_commit
        or not isinstance(quote.publication_id, int)
        or quote.publication_id < 1
    ):
        raise P90TrailRecoveryUnverified(
            "P90_TRAIL_RECOVERY_ACCOUNT_SOURCE_OR_ELIGIBILITY_UNVERIFIED"
        )

    if any(
        value.tzinfo is None or value.utcoffset() != timedelta()
        for value in (
            position.fill_at, position.initial_protection_confirmed_at,
            private.observed_at, quote.field_times[0], quote.as_of_second,
            received_at,
        )
    ) or not (
        position.fill_at <= position.initial_protection_confirmed_at
        <= private.observed_at <= received_at
        and quote.field_times[0] <= quote.as_of_second <= received_at
        and timedelta() <= received_at - private.observed_at
        <= timedelta(seconds=5)
        and position.fill_at <= quote.as_of_second
        and quote.field_times[0] <= quote.as_of_second
        and timedelta() <= received_at - quote.as_of_second
        <= timedelta(seconds=2)
    ):
        raise P90TrailRecoveryUnverified(
            "P90_TRAIL_RECOVERY_CURRENT_PROOF_STALE"
        )

    signed = private.position
    side = "Buy" if position.direction is TradeDirection.LONG else "Sell"
    if (
        signed.get("symbol") != position.symbol
        or signed.get("side") != side
        or str(signed.get("positionIdx")) != "0"
        or _positive(signed.get("avgPrice")) != position.actual_avg_fill
        or _positive(signed.get("size")) != position.actual_qty
    ):
        raise P90TrailRecoveryUnverified(
            "P90_TRAIL_RECOVERY_SIGNED_POSITION_DRIFT"
        )
    tick = _positive(private.tick_size)
    stop = _positive(signed.get("stopLoss"))
    tp = _positive(signed.get("takeProfit"))
    mark = _positive(signed.get("markPrice"))
    ltp = _positive(quote.last_price)
    try:
        trailing = Decimal(str(signed.get("trailingStop")))
    except (TypeError, ValueError, InvalidOperation):
        raise P90TrailRecoveryUnverified(
            "P90_TRAIL_RECOVERY_SIGNED_TRAILING_MISSING"
        ) from None
    if (
        not trailing.is_finite() or trailing < 0
        or trailing % tick != 0 or stop % tick != 0 or tp % tick != 0
        or not (
            stop < mark < tp and stop < ltp < tp and tp > position.actual_avg_fill
            if position.direction is TradeDirection.LONG
            else tp < mark < stop and tp < ltp < stop
                 and tp < position.actual_avg_fill
        )
    ):
        raise P90TrailRecoveryUnverified(
            "P90_TRAIL_RECOVERY_SIGNED_PROTECTION_OR_PRICE_INVALID"
        )

    quote_ref = (
        f"bybit:market-publication:{quote.publication_id}:"
        f"source={quote.source_session_id}"
    )
    signed_ref = (
        f"{private.source_ref}:account={private.trading_account_id}:"
        f"generation={private.generation_id}"
    )
    observation_id = "p90-trail-reconcile-" + fingerprint({
        "position": position.strategy_position_id,
        "exit_plan": position.exit_plan_fingerprint,
        "publication": quote.publication_id,
        "private_generation": private.generation_id,
        "publication_time": quote.as_of_second.astimezone(UTC),
    })[:32]
    return ExitObservation(
        observation_id=observation_id,
        strategy_position_id=position.strategy_position_id,
        symbol=position.symbol,
        # A recovery is a new present-time protection fact. The unchanged
        # original lastPrice field time is historical and can predate fill;
        # only the verified current MarketPublication is the event clock.
        event_at=quote.as_of_second.astimezone(UTC),
        observed_at=received_at.astimezone(UTC),
        received_at=received_at.astimezone(UTC),
        event_kind="P90L90_REAL_TRAIL_RECOVERY",
        attributes=FrozenPolicy.from_mapping({"p90l90_real": {
            "trading_account_id": position.trading_account_id,
            "strategy_execution_binding_id":
                position.strategy_execution_binding_id,
            "exchange_position_key": position.exchange_position_key,
            "actual_fill_confirmed": True,
            "protection_exchange_confirmed": True,
            "current_trailing_recovery_verified": True,
            "current_original_ltp_verified": True,
            "trailing_recovery_policy": P90_TRAIL_RECOVERY_POLICY,
            "pending_exit_mutation": False,
            "current_original_ltp": str(ltp),
            "exchange_stop_price": str(stop),
            "exchange_tp_price": str(tp),
            "exchange_trailing_distance": str(trailing),
            "exchange_mark_price": str(mark),
            "tick_size": str(tick),
        }}),
        source_refs=(quote_ref, signed_ref),
    )
