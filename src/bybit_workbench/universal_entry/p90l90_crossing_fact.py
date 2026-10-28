"""Canonical P90L90 normalized crossing fact shared by Watch and durable PAPER.

This function owns ONLY the exact source-proven synthetic candidate envelope.
It does not decide Strategy admission, create PAPER positions or send orders.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from .contracts import FrozenPolicy, MarketFactEnvelope, TradeDirection
from .fingerprint import fingerprint
from .p90l90_causal import P90L90Crossing, P90L90EntryState


def p90l90_crossing_fact(
    *,
    entry_plan_fingerprint: str,
    source_fact: MarketFactEnvelope,
    crossing: P90L90Crossing,
    geometry: P90L90EntryState,
) -> MarketFactEnvelope:
    """Emit exactly the legacy MarketWatch v2 no-order fact identity/payload."""
    if not entry_plan_fingerprint or source_fact.event_kind != "PUBLIC_TRADE":
        raise ValueError("P90L90 crossing requires exact native source/plan")
    if (
        crossing.event_at != source_fact.event_at
        or crossing.effective_at != geometry.effective_at
        or not geometry.effective_at <= crossing.event_at
        or crossing.event_at >= geometry.effective_at + timedelta(minutes=5)
        or crossing.modeled_exchange_fill
    ):
        raise ValueError("P90L90 crossing and causal geometry not equivalent")
    edge = geometry.entry_edge(crossing.side)
    if edge is None or edge != crossing.boundary:
        raise ValueError("P90L90 crossing not supported by frozen raw Entry")
    source_price = source_fact.attributes.to_dict().get("price")
    if str(source_price) != str(crossing.current_price):
        if source_price is None or Decimal(str(source_price)) != crossing.current_price:
            raise ValueError("P90L90 crossing native trade price mismatch")
    return MarketFactEnvelope(
        fact_id="watch-p90l90-" + fingerprint({
            "entry_plan_fingerprint": entry_plan_fingerprint,
            "source_fact": source_fact.fact_id,
            "direction": crossing.side,
            "effective_at": crossing.effective_at,
        })[:32],
        event_kind="P90L90_RAW_H9_PREV7D_CROSSING",
        symbol=source_fact.symbol,
        observed_at=source_fact.observed_at,
        event_at=crossing.event_at,
        received_at=source_fact.received_at,
        source_refs=source_fact.source_refs,
        direction=TradeDirection(crossing.side),
        attributes=FrozenPolicy.from_mapping({
            "entry_price": crossing.current_price,
            "price": crossing.current_price,
            "previous_tick_price": crossing.previous_price,
            "raw_entry_boundary": crossing.boundary,
            "depth_pct": crossing.depth_pct,
            "state_effective_at": crossing.effective_at,
            "p90l90": {
                "candidate_price": crossing.current_price,
                "opposite_raw_l53_target": geometry.opposite_tp(crossing.side),
                "state_effective_at": crossing.effective_at,
                "depth_pct": crossing.depth_pct,
            },
            "modeled_exchange_fill": False,
            "trade_fact_id": source_fact.fact_id,
        }),
    )
