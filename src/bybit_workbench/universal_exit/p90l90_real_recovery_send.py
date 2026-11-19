"""Final private-worker source/position fence for delayed native TRAIL035.

Read-only DB attestation of durable opt-in, exact ExitDecision and original
MarketPublication. The credential-owning caller MUST re-GET signed Bybit
position AFTER acquiring its account lock and BEFORE the actual POST.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any

from bybit_workbench.market_data.r10_execution_publication import R10ExecutionQuote
from bybit_workbench.universal_exit.p90l90_real_v3 import P90_TRAIL_RECOVERY_POLICY


class P90TrailRecoverySendBlocked(RuntimeError):
    """No current exact financial/provenance proof for one Bybit POST."""


def _need(value: bool, code: str) -> None:
    if not value:
        raise P90TrailRecoverySendBlocked("P90_TRAIL_SEND_" + code)


def _price(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
        if result.is_finite() and result > 0:
            return result
    except (ValueError, InvalidOperation, TypeError):
        pass
    raise P90TrailRecoverySendBlocked("P90_TRAIL_SEND_PRICE_INVALID")


def validate_p90_trail_recovery_send(
    connection: Any,
    *,
    payload: Mapping[str, object],
    position: Mapping[str, object],
    quote: R10ExecutionQuote,
    tick: Decimal,
    at: datetime,
) -> None:
    """Check a new-contract Exit command against immutable, current evidence.

    Does not authorize generic account trades, a pre-2026-11-19 position or
    an in-flight UNKNOWN mutation; those are independent caller gates.
    """
    account = str(payload.get("trading_account_id") or "")
    position_id = str(payload.get("strategy_position_id") or "")
    decision_id = str(payload.get("exit_decision_id") or "")
    symbol = str(payload.get("symbol") or "")
    mutation = payload.get("requested_mutation")
    _need(
        payload.get("trail_recovery_policy") == P90_TRAIL_RECOVERY_POLICY
        and str(payload.get("action_kind")) == "SET_TRAILING"
        and account.startswith("ta-") and len(account) == 35
        and position_id.startswith("POS-")
        and decision_id.startswith("exit-decision-")
        and symbol == quote.symbol and symbol == position.get("symbol")
        and isinstance(mutation, Mapping)
        and tick > 0 and tick.is_finite()
        and at.utcoffset() == timedelta(0)
        and quote.as_of_second.utcoffset() == timedelta(0)
        and quote.field_times[0] <= quote.as_of_second
        and timedelta() <= at - quote.as_of_second <= timedelta(seconds=2),
        "SCOPE_OR_PUBLICATION_UNVERIFIED",
    )
    row = connection.execute(
        """SELECT p.initial_protection_evidence,d.rule_id,d.action_kind,
                  d.requested_mutation,o.event_kind,o.observed_at,
                  o.attributes,o.source_refs
             FROM runtime.position_ownership p
             JOIN strategy_exit.exit_decisions d
               ON d.strategy_position_id=p.position_id
             JOIN strategy_exit.exit_observations o
               ON o.observation_id=d.observation_id
            WHERE p.position_id=%s AND p.trading_account_id=%s
              AND p.state='OPEN' AND p.identity_status='PROVEN'
              AND d.exit_decision_id=%s AND d.action_kind='SET_TRAILING'
              AND o.strategy_position_id=p.position_id""",
        (position_id, account, decision_id),
    ).fetchone()
    _need(row is not None and len(row) == 8, "RECORDED_OWNER_MISSING")
    evidence, rule, action, saved_mutation, event, observed, attrs, refs = row
    _need(
        isinstance(evidence, Mapping)
        and evidence.get("trailing_recovery_policy") == P90_TRAIL_RECOVERY_POLICY
        and rule == "P90L90_REAL_BYBIT_NATIVE_TRAIL035_RECONCILE"
        and action == "SET_TRAILING"
        and event == "P90L90_REAL_TRAIL_RECOVERY"
        and saved_mutation == dict(mutation)
        and isinstance(observed, datetime)
        and observed.utcoffset() == timedelta(0)
        and timedelta() <= at - observed <= timedelta(seconds=5)
        and isinstance(attrs, Mapping)
        and isinstance(attrs.get("p90l90_real"), Mapping)
        and isinstance(refs, list)
        and (
            f"bybit:market-publication:{quote.publication_id}:"
            f"source={quote.source_session_id}"
        ) in refs,
        "DURABLE_DECISION_OR_ORIGINAL_SOURCE_MISMATCH",
    )
    facts = attrs["p90l90_real"]
    _need(
        facts.get("trailing_recovery_policy") == P90_TRAIL_RECOVERY_POLICY
        and facts.get("current_trailing_recovery_verified") is True
        and facts.get("current_original_ltp_verified") is True
        and facts.get("trading_account_id") == account
        and (
            f"bybit:signed-position-list:exact-account:"
            f"account={account}:"
            f"generation="
        ) in "|".join(refs),
        "RECOVERY_OBSERVATION_NOT_PROVEN",
    )
    ltp = _price(facts.get("current_original_ltp"))
    _need(ltp == quote.last_price, "ORIGINAL_LTP_CHANGED")
    fill = _price(position.get("avgPrice"))
    stop = _price(position.get("stopLoss"))
    tp = _price(position.get("takeProfit"))
    mark = _price(position.get("markPrice"))
    side = str(position.get("side") or "")
    _need(
        side in ("Buy", "Sell")
        and str(position.get("positionIdx")) == "0"
        and _price(position.get("size")) > 0
        and (
            stop < mark < tp and stop < ltp < tp and tp > fill
            if side == "Buy"
            else tp < mark < stop and tp < ltp < stop and tp < fill
        )
        and stop == _price(facts.get("exchange_stop_price"))
        and tp == _price(facts.get("exchange_tp_price"))
        and _price(tick) > 0,
        "PROTECTION_OR_POSITION_CHANGED",
    )
    try:
        old_trailing = Decimal(str(position.get("trailingStop")))
    except (ValueError, TypeError, InvalidOperation):
        raise P90TrailRecoverySendBlocked(
            "P90_TRAIL_SEND_OLD_TRAILING_NOT_PROVEN"
        ) from None
    _need(
        old_trailing.is_finite() and old_trailing == 0
        and facts.get("exchange_trailing_distance") in ("0", "0.0"),
        "TRAIL_ALREADY_SET_OR_DRIFTED",
    )
    expected_distance = (
        fill * Decimal("0.0035") / tick
    ).to_integral_value(rounding=ROUND_FLOOR) * tick
    activation = (
        fill * (
            Decimal("1.005") if side == "Buy" else Decimal("0.995")
        ) / tick
    ).to_integral_value(
        rounding=ROUND_CEILING if side == "Buy" else ROUND_FLOOR
    ) * tick
    _need(
        _price(mutation.get("distance")) == expected_distance
        and mutation.get("tpsl_mode") == "Full",
        "NATIVE_TRAIL_DISTANCE_DRIFT",
    )
    profitable = ltp >= activation if side == "Buy" else ltp <= activation
    if profitable:
        _need(
            "active_price" not in mutation,
            "VENUE_IMMEDIATE_ACTIVATION_REQUIRED",
        )
    else:
        _need(
            _price(mutation.get("active_price")) == activation,
            "VENUE_FUTURE_ACTIVATION_MISSING",
        )
