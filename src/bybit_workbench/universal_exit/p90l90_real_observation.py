"""P90L90 REAL ExitObservation from exact native MFE + signed-account facts.

Pure validation/normalization only. The actual private Bybit connector must
independently attest account UID, freshness, inventory and protection readback;
the actual native source must independently attest fill-forward continuity.
Constructing these data objects is NOT signed evidence or permission to trade.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import FrozenPolicy, TradeDirection
from bybit_workbench.universal_entry.fingerprint import fingerprint
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

from .contracts import ExitObservation
from .p90l90_real_mfe import P90RealMfeCheckpoint, P90RealMfeUnverified


class P90RealObservationUnverified(ValueError):
    """The signed inventory or native MFE source is not proven exact."""


@dataclass(frozen=True, slots=True)
class ScopedPrivatePositionEvidence:
    """Input supplied ONLY by authenticated account-local private producer."""

    trading_account_id: str
    strategy_execution_binding_id: str
    connector_id: str
    signed_provider_uid: str
    generation_id: str
    exchange_position_key: str
    symbol: str
    position_idx: int
    observed_at: datetime
    source_ref: str
    position: Mapping[str, object]
    tick_size: Decimal
    pending_exit_mutation: bool


def _positive(value: object, code: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        raise P90RealObservationUnverified(code) from None
    if d <= 0 or not d.is_finite():
        raise P90RealObservationUnverified(code)
    return d


def build_real_native_exit_observation(
    *,
    position: StrategyPosition,
    checkpoint: P90RealMfeCheckpoint,
    cursor: TradeCursor,
    price: Decimal,
    native_fact_id: str,
    native_source_refs: tuple[str, ...],
    private: ScopedPrivatePositionEvidence,
    received_at: datetime,
) -> ExitObservation:
    """Normalize source-verified facts into the existing Universal ExitPlan.

    MFE checkpoint MUST already be atomically recorded by native callback;
    the signed-account evidence provider MUST independently verify its UID,
    account, connector and private-WS continuity before constructing input.
    """
    if (
        position.strategy_id not in ALLOWED_SYMBOLS
        or position.strategy_version != VERSION
        or position.identity_status != "PROVEN"
        or position.initial_protection_confirmed_at is None
        or position.trading_account_id is None
        or position.strategy_execution_binding_id is None
        or position.position_idx != 0
        or position.exchange_position_key != exchange_position_key(
            position.trading_account_id, position.symbol, 0,
        )
        or position.symbol not in ALLOWED_SYMBOLS[position.strategy_id]
        or not native_fact_id
        or not native_source_refs
        or any(not ref for ref in native_source_refs)
        or private.pending_exit_mutation
        or not private.connector_id or not private.generation_id
        or not private.signed_provider_uid.isdigit()
        or not private.source_ref
        or private.trading_account_id != position.trading_account_id
        or private.strategy_execution_binding_id
        != position.strategy_execution_binding_id
        or private.exchange_position_key != position.exchange_position_key
        or private.symbol != position.symbol
        or private.position_idx != position.position_idx
    ):
        raise P90RealObservationUnverified("P90_REAL_EXIT_PRIVATE_ACCOUNT_INVALID")
    if (
        received_at.tzinfo is None or private.observed_at.tzinfo is None
        or cursor.traded_at.tzinfo is None
        or private.observed_at.astimezone(UTC) < cursor.traded_at.astimezone(UTC)
        or received_at.astimezone(UTC) < private.observed_at.astimezone(UTC)
        or (
            received_at.astimezone(UTC) - private.observed_at.astimezone(UTC)
        ).total_seconds() > 15
        or checkpoint.position_id != position.strategy_position_id
        or checkpoint.account_id != position.trading_account_id
        or checkpoint.binding_id != position.strategy_execution_binding_id
        or checkpoint.exchange_position_key != position.exchange_position_key
        or checkpoint.strategy_config_fingerprint
        != position.strategy_config_fingerprint
        or checkpoint.symbol != position.symbol
        or checkpoint.direction is not position.direction
        or checkpoint.fill_at != position.fill_at.astimezone(UTC)
        or checkpoint.fill_price != position.actual_avg_fill
        or checkpoint.cursor_seq != cursor.seq
        or checkpoint.cursor_exec_id != cursor.exec_id
        or checkpoint.cursor_at != cursor.traded_at.astimezone(UTC)
        or checkpoint.last_native_price != price
    ):
        raise P90RealObservationUnverified("P90_REAL_EXIT_NATIVE_MFE_OR_TIME_INVALID")
    raw = private.position
    signed_side = "Buy" if position.direction is TradeDirection.LONG else "Sell"
    if (
        str(raw.get("symbol") or "") != position.symbol
        or str(raw.get("side") or "") != signed_side
        or str(raw.get("positionIdx")) != str(position.position_idx)
        or _positive(raw.get("avgPrice"), "P90_REAL_FILL_INVALID")
        != position.actual_avg_fill
        or _positive(raw.get("size"), "P90_REAL_SIZE_INVALID")
        != position.actual_qty
    ):
        raise P90RealObservationUnverified("P90_REAL_EXIT_SIGNED_POSITION_MISMATCH")
    stop = _positive(raw.get("stopLoss"), "P90_REAL_STOP_NOT_PRESENT")
    tp = _positive(raw.get("takeProfit"), "P90_REAL_TP_NOT_PRESENT")
    mark = _positive(raw.get("markPrice"), "P90_REAL_SIGNED_MARK_NOT_PRESENT")
    # A missing signed trailingStop cannot be silently treated as zero: it
    # could mean an already active autonomous Bybit trailing guard.
    try:
        trailing = Decimal(str(raw.get("trailingStop")))
    except (InvalidOperation, ValueError, TypeError):
        raise P90RealObservationUnverified(
            "P90_REAL_SIGNED_TRAILING_READBACK_MISSING"
        ) from None
    tick = _positive(private.tick_size, "P90_REAL_TICK_INVALID")
    if not trailing.is_finite() or trailing < 0 or trailing % tick != 0:
        raise P90RealObservationUnverified(
            "P90_REAL_SIGNED_TRAILING_READBACK_INVALID"
        )
    # Profitable trailing stops may be beyond Entry, but must remain on
    # the protective side of the current signed mark. An already marketable
    # resting TP cannot be asserted to be active Exchange protection.
    long = position.direction is TradeDirection.LONG
    if (
        stop % tick != 0 or tp % tick != 0
        or not (
            stop < mark < tp and tp > position.actual_avg_fill
            if long else tp < mark < stop and tp < position.actual_avg_fill
        )
    ):
        raise P90RealObservationUnverified(
            "P90_REAL_EXIT_EXCHANGE_PROTECTION_NOT_PROVEN"
        )
    if (
        position.direction is TradeDirection.LONG
        and checkpoint.best_favorable_price < position.actual_avg_fill
    ) or (
        position.direction is TradeDirection.SHORT
        and checkpoint.best_favorable_price > position.actual_avg_fill
    ):
        raise P90RealMfeUnverified("P90_REAL_EXIT_MFE_OPPOSES_POSITION")
    ref = (
        f"{private.source_ref}:account={private.trading_account_id}:"
        f"generation={private.generation_id}"
    )
    observation_id = "p90-real-native-" + fingerprint({
        "position_id": position.strategy_position_id,
        "plan": position.exit_plan_fingerprint,
        "source_commit": checkpoint.source_commit,
        "trade_seq": cursor.seq,
        "trade_exec_id": cursor.exec_id,
    })[:32]
    return ExitObservation(
        observation_id=observation_id,
        strategy_position_id=position.strategy_position_id,
        symbol=position.symbol,
        event_at=cursor.traded_at.astimezone(UTC),
        observed_at=cursor.traded_at.astimezone(UTC),
        received_at=received_at.astimezone(UTC),
        event_kind="P90L90_REAL_NATIVE_TRADE",
        attributes=FrozenPolicy.from_mapping({
            "p90l90_real": {
                "trading_account_id": position.trading_account_id,
                "strategy_execution_binding_id":
                    position.strategy_execution_binding_id,
                "exchange_position_key": position.exchange_position_key,
                "actual_fill_confirmed": True,
                "protection_exchange_confirmed": True,
                "durable_cursor_verified": True,
                "pending_exit_mutation": False,
                "native_trade_price": str(price),
                "durable_best_favorable_price":
                    str(checkpoint.best_favorable_price),
                "exchange_stop_price": str(stop),
                "exchange_tp_price": str(tp),
                "exchange_trailing_distance": str(trailing),
                "exchange_mark_price": str(mark),
                "tick_size": str(tick),
            },
        }),
        source_refs=(*native_source_refs, f"bybit:exec:{cursor.exec_id}", ref),
    )


@dataclass(frozen=True, slots=True)
class P90RealPostfillSourceWindow:
    """Proof value returned only by the independent live MarketSource owner.

    A boolean, REST ticker or a current publicTrade alone does NOT establish
    the absence of an earlier +0.5% excursion. The producer must audit every
    fill-forward native trade and its original subscribed source epoch.
    This type does not manufacture that attestation.
    """

    position_id: str
    symbol: str
    fill_at: datetime
    covered_through: datetime
    source_commit: str
    last_native_price: Decimal
    best_favorable_price: Decimal
    source_refs: tuple[str, ...]
    complete_native_window: bool


def build_real_postfill_exit_observation(
    *,
    position: StrategyPosition,
    private: ScopedPrivatePositionEvidence,
    coverage: P90RealPostfillSourceWindow,
    signed_fill_execution_ids: tuple[str, ...],
    exact_entry_order_id: str,
    received_at: datetime,
) -> ExitObservation:
    """First-fill -> Universal Exit, without waiting for another native tick.

    Must be called within the account/source handoff transaction by the
    credential-local Execution owner. The coverage is a separately audited
    original MarketSource window, not an inferred REST mark or self-asserted
    stream-health flag. No Exchange POST occurs inside this constructor.
    """
    if (
        position.strategy_id not in ALLOWED_SYMBOLS
        or position.strategy_version != VERSION
        or position.symbol not in ALLOWED_SYMBOLS[position.strategy_id]
        or position.identity_status != "PROVEN"
        or position.trading_account_id is None
        or position.strategy_execution_binding_id is None
        or position.position_idx != 0
        or position.initial_protection_confirmed_at is None
        or position.exchange_position_key != exchange_position_key(
            position.trading_account_id, position.symbol, 0,
        )
        or not signed_fill_execution_ids
        or len(set(signed_fill_execution_ids)) != len(signed_fill_execution_ids)
        or any(not isinstance(x, str) or not x.strip()
               for x in signed_fill_execution_ids)
        or not exact_entry_order_id.strip()
        or coverage.complete_native_window is not True
        or coverage.position_id != position.strategy_position_id
        or coverage.symbol != position.symbol
        or coverage.source_commit is None
        or re.fullmatch(r"[0-9a-f]{40}", coverage.source_commit) is None
        or not coverage.source_refs
        or any(not ref.startswith("bybit:publicTrade:")
               for ref in coverage.source_refs)
        or private.pending_exit_mutation is not False
        or private.trading_account_id != position.trading_account_id
        or private.strategy_execution_binding_id
           != position.strategy_execution_binding_id
        or private.exchange_position_key != position.exchange_position_key
        or private.symbol != position.symbol
        or private.position_idx != position.position_idx
        or not private.connector_id or not private.generation_id
        or not private.signed_provider_uid.isdigit()
        or private.source_ref != "bybit:signed-position-list:exact-account"
    ):
        raise P90RealObservationUnverified(
            "P90_REAL_POSTFILL_SOURCE_ACCOUNT_OR_ORDER_UNVERIFIED"
        )
    timestamps = (
        position.fill_at, position.initial_protection_confirmed_at,
        coverage.fill_at, coverage.covered_through,
        private.observed_at, received_at,
    )
    if any(at.tzinfo is None or at.utcoffset() is None for at in timestamps):
        raise P90RealObservationUnverified("P90_REAL_POSTFILL_CLOCK_UNVERIFIED")
    if (
        coverage.fill_at.astimezone(UTC) != position.fill_at.astimezone(UTC)
        or coverage.covered_through < position.initial_protection_confirmed_at
        or coverage.covered_through < coverage.fill_at
        or private.observed_at < coverage.covered_through
        or received_at < private.observed_at
        or (received_at - coverage.covered_through).total_seconds() > 5
    ):
        raise P90RealObservationUnverified("P90_REAL_POSTFILL_WINDOW_STALE_OR_GAP")
    actual_fill = _positive(position.actual_avg_fill, "P90_REAL_POSTFILL_AVG_INVALID")
    raw = private.position
    side = "Buy" if position.direction is TradeDirection.LONG else "Sell"
    if (
        raw.get("symbol") != position.symbol
        or raw.get("side") != side
        or str(raw.get("positionIdx")) != "0"
        or _positive(raw.get("avgPrice"), "P90_REAL_POSTFILL_SIGNED_AVG") != actual_fill
        or _positive(raw.get("size"), "P90_REAL_POSTFILL_SIGNED_SIZE")
           != position.actual_qty
    ):
        raise P90RealObservationUnverified("P90_REAL_POSTFILL_SIGNED_POSITION_DRIFT")
    tick = _positive(private.tick_size, "P90_REAL_POSTFILL_TICK")
    stop = _positive(raw.get("stopLoss"), "P90_REAL_POSTFILL_SL")
    tp = _positive(raw.get("takeProfit"), "P90_REAL_POSTFILL_TP")
    mark = _positive(raw.get("markPrice"), "P90_REAL_POSTFILL_MARK")
    try:
        trail = Decimal(str(raw.get("trailingStop")))
    except (ValueError, TypeError, InvalidOperation):
        raise P90RealObservationUnverified("P90_REAL_POSTFILL_SIGNED_TRAIL_ABSENT") from None
    best = _positive(coverage.best_favorable_price, "P90_REAL_POSTFILL_BEST")
    last = _positive(coverage.last_native_price, "P90_REAL_POSTFILL_LAST")
    long = position.direction is TradeDirection.LONG
    if (
        not trail.is_finite() or trail < 0 or trail % tick != 0
        or stop % tick != 0 or tp % tick != 0
        or not (stop < mark < tp and tp > actual_fill
                if long else tp < mark < stop and tp < actual_fill)
        or (best < actual_fill if long else best > actual_fill)
        or (best < last if long else best > last)
    ):
        raise P90RealObservationUnverified("P90_REAL_POSTFILL_PROTECTION_UNVERIFIED")
    signed_ref = (
        f"{private.source_ref}:account={private.trading_account_id}:"
        f"generation={private.generation_id}"
    )
    evidence_id = fingerprint({
        "position_id": position.strategy_position_id,
        "source_commit": coverage.source_commit,
        "through": coverage.covered_through.astimezone(UTC),
        "fill_ids": signed_fill_execution_ids,
        "order_id": exact_entry_order_id,
    })[:32]
    return ExitObservation(
        observation_id="p90-real-postfill-" + evidence_id,
        strategy_position_id=position.strategy_position_id,
        symbol=position.symbol,
        event_at=coverage.covered_through.astimezone(UTC),
        observed_at=coverage.covered_through.astimezone(UTC),
        received_at=received_at.astimezone(UTC),
        event_kind="P90L90_REAL_POSTFILL",
        attributes=FrozenPolicy.from_mapping({"p90l90_real": {
            "trading_account_id": position.trading_account_id,
            "strategy_execution_binding_id": position.strategy_execution_binding_id,
            "exchange_position_key": position.exchange_position_key,
            "actual_fill_confirmed": True,
            "protection_exchange_confirmed": True,
            "fill_to_postfill_source_verified": True,
            "signed_fill_execution_ids": list(signed_fill_execution_ids),
            "pending_exit_mutation": False,
            "native_trade_price": str(last),
            "durable_best_favorable_price": str(best),
            "exchange_stop_price": str(stop),
            "exchange_tp_price": str(tp),
            "exchange_trailing_distance": str(trail),
            "exchange_mark_price": str(mark),
            "tick_size": str(tick),
        }}),
        source_refs=(
            *coverage.source_refs,
            f"bybit:execution:list:exact-order:{exact_entry_order_id}",
            *(f"bybit:execution:fill:{x}" for x in signed_fill_execution_ids),
            signed_ref,
        ),
    )
