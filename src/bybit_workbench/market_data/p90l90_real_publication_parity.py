"""R09: two-source-port parity without promoting observations to financial Exit.

The independent native MarketSource broker must already certify the complete
first-fill window (including the absence of source discontinuities).  A PG
MarketPublication candidate is only a second read of original facts.  Matching
these two ports never substitutes current signed MAIN/SUB OPEN/SL/TP and
pending-mutation proof, nor proves a zero-event postfill interval.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from bybit_workbench.market_data.p90l90_real_publication_postfill import (
    R09NativePublicationCandidate,
)
from bybit_workbench.universal_exit.p90l90_real_observation import (
    P90RealPostfillSourceWindow,
)

_MAX_PUBLICATION_LAG = timedelta(seconds=5)


class R09PublicationParityUnverified(RuntimeError):
    """Source broker and original PG publication are not identical/safe."""


@dataclass(frozen=True, slots=True)
class R09PublicationParityEvidence:
    """Read-model comparison only; still needs private signed Exit gate."""

    position_id: str
    symbol: str
    source_session_id: str
    publication_id: int
    observed_through: datetime
    source_refs: tuple[str, ...]
    # The comparison NEVER sets a trading authorization bit.
    financial_exit_ready: bool = False


def require_r09_broker_publication_parity(
    *,
    candidate: R09NativePublicationCandidate,
    broker_window: P90RealPostfillSourceWindow,
) -> R09PublicationParityEvidence:
    if (
        candidate.native_continuity_verified is not False
        or candidate.exit_ready is not False
        or broker_window.complete_native_window is not True
        or broker_window.no_new_native_trade_rest_verified is not False
        or candidate.position_id != broker_window.position_id
        or candidate.symbol != broker_window.symbol
        or candidate.source_commit != broker_window.source_commit
        or candidate.fill_at != broker_window.fill_at
        or not candidate.source_refs
        or candidate.native_event_count != len(candidate.source_refs)
        or len(candidate.source_refs) != len(set(candidate.source_refs))
        or candidate.source_refs != broker_window.source_refs
        or candidate.last_native_price != broker_window.last_native_price
        or candidate.observed_favorable_price != broker_window.best_favorable_price
        or broker_window.covered_through < candidate.fill_at
        or broker_window.covered_through > candidate.covered_through
        or not timedelta(0) <= (
            candidate.covered_through - broker_window.covered_through
        ) <= _MAX_PUBLICATION_LAG
    ):
        raise R09PublicationParityUnverified(
            "R09_PUBLICATION_BROKER_ORIGINAL_NATIVE_PARITY_UNVERIFIED"
        )
    return R09PublicationParityEvidence(
        position_id=candidate.position_id,
        symbol=candidate.symbol,
        source_session_id=candidate.source_session_id,
        publication_id=candidate.publication_id,
        observed_through=candidate.covered_through,
        source_refs=candidate.source_refs,
    )
