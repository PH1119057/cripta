"""R11: offline BOOK math / TICKER sample parity against real MAYAK formulas.

One MarketPublication compact second can reproduce a *snapshot feature*, not
original orderbook deltas, ticker update chronology, source continuity, or
seven-symbol MAYAK. This pure adapter accepts disposable original 50-level
fixtures to compare the actual LiveMayakEngine math. It NEVER creates
Live/Trading permission or calls a network/DB/source writer.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.mayak.core.live import LiveMayakEngine
from bybit_workbench.mayak.source_publication_parity import (
    R11MayakFeatureCandidate,
)

_NATIVE_FIVE = frozenset((
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
))
_TICKER_MAP = (
    ("openInterest", "open_interest"),
    ("openInterestValue", "open_interest_value"),
    ("fundingRate", "funding_rate"),
    ("lastPrice", "last_price"),
    ("markPrice", "mark_price"),
    ("indexPrice", "index_price"),
)
_BOOK_BANDS = (5, 10, 25, 50)
_BOOK_KEYS = (
    "best_bid", "best_ask", "bid_size", "ask_size",
    *(f"{side}_{bps}bps_usd"
      for side in ("bid", "ask") for bps in _BOOK_BANDS),
)
_REL_TOL = 1e-12
_ABS_TOL = 1e-12


class R11SnapshotParityUnverified(RuntimeError):
    """No truthful partial MAYAK snapshot equivalence evidence."""


@dataclass(frozen=True, slots=True)
class R11MayakTickerSample:
    symbol: str
    publication_id: int
    source_session_id: str
    sampled_as_of: datetime
    legacy_fields: tuple[tuple[str, float], ...]
    # Sampled compact values do not preserve individual TICKER WS timestamps.
    ticker_original_event_timing_verified: bool = False
    multi_horizon_history_verified: bool = False
    seven_symbol_mayak_ready: bool = False

    def as_legacy_kwargs(self) -> dict[str, float]:
        """Only for disposable parity replay at *sampled*, not event, time."""
        return dict(self.legacy_fields)


@dataclass(frozen=True, slots=True)
class R11BookSnapshotMathCheck:
    symbol: str
    market: str
    publication_id: int
    source_session_id: str
    compared_keys: tuple[str, ...]
    snapshot_formula_match: bool
    # A supplied full ladder is not authenticated original source evidence.
    original_book_frame_provenance_verified: bool = False
    book_delta_sequence_verified: bool = False
    seven_symbol_mayak_ready: bool = False
    mayak_socket_retirement_authorized: bool = False


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise R11SnapshotParityUnverified("R11_SNAPSHOT_" + code)


def _as_float(value: object, code: str) -> float:
    _need(
        isinstance(value, (Decimal, float, int))
        and not isinstance(value, bool),
        code,
    )
    amount = float(value)
    _need(math.isfinite(amount), code)
    return amount


def _source(candidate: R11MayakFeatureCandidate) -> None:
    _need(isinstance(candidate, R11MayakFeatureCandidate), "SOURCE_TYPE")
    _need(
        candidate.symbol in _NATIVE_FIVE
        and type(candidate.publication_id) is int
        and candidate.publication_id > 0
        and isinstance(candidate.source_session_id, str)
        and re.fullmatch(r"[0-9a-f]{32}", candidate.source_session_id)
        is not None
        and isinstance(candidate.producer_release_commit, str)
        and re.fullmatch(
            r"[0-9a-f]{40}", candidate.producer_release_commit,
        ) is not None
        and isinstance(candidate.as_of_second, datetime)
        and candidate.as_of_second.utcoffset() == timedelta(0)
        and candidate.as_of_second.microsecond == 0,
        "SOURCE_IDENTITY_OR_UTC_INVALID",
    )
    # Never promote a caller-supplied readiness assertion into proof.
    _need(
        candidate.seven_symbol_panel_verified is False
        and candidate.original_spot_and_linear_trade_replay_verified is False
        and candidate.book_delta_replay_verified is False
        and candidate.liquidation_event_replay_verified is False
        and candidate.optional_rest_feature_parity_verified is False,
        "UNVERIFIED_SCOPE_MARKED_READY",
    )


def project_r11_ticker_sample(
    candidate: R11MayakFeatureCandidate,
) -> R11MayakTickerSample:
    """Exact MAYAK ticker field spelling, one compact publication sample.

    The source reader has already checked the original per-field timestamps.
    Do not use this sample to reconstruct 5/15/30/60m OI histories or
    funding-change chronology; those require verified ordered event history.
    """
    _source(candidate)
    _need(
        isinstance(candidate.linear_ticker, dict),
        "TICKER_FIELDS_MISSING",
    )
    fields: list[tuple[str, float]] = []
    for source_key, legacy_key in _TICKER_MAP:
        value = candidate.linear_ticker.get(source_key)
        _need(isinstance(value, Decimal), "TICKER_ORIGINAL_VALUE_NOT_DECIMAL")
        numeric = _as_float(value, "TICKER_NONFINITE")
        _need(
            numeric >= 0 if source_key == "fundingRate" else numeric > 0,
            "TICKER_SIGN_INVALID",
        ) if source_key != "fundingRate" else None
        fields.append((legacy_key, numeric))
    return R11MayakTickerSample(
        symbol=candidate.symbol,
        publication_id=candidate.publication_id,
        source_session_id=candidate.source_session_id,
        sampled_as_of=candidate.as_of_second.astimezone(UTC),
        legacy_fields=tuple(fields),
    )


def _ladder(
    rows: tuple[tuple[Decimal, Decimal], ...],
    *,
    max_levels: int,
) -> list[tuple[float, float]]:
    _need(
        type(rows) is tuple and 1 <= len(rows) <= max_levels,
        "ORIGINAL_LADDER_BOUNDS_INVALID",
    )
    levels: list[tuple[float, float]] = []
    seen: set[Decimal] = set()
    for level in rows:
        _need(
            type(level) is tuple and len(level) == 2
            and isinstance(level[0], Decimal)
            and isinstance(level[1], Decimal),
            "ORIGINAL_LEVEL_SHAPE_INVALID",
        )
        price, quantity = level
        _need(
            price.is_finite() and price > 0
            and quantity.is_finite() and quantity > 0
            and price not in seen,
            "ORIGINAL_LEVEL_VALUE_OR_DUPLICATE_INVALID",
        )
        p = _as_float(price, "ORIGINAL_LEVEL_NONFINITE")
        q = _as_float(quantity, "ORIGINAL_LEVEL_NONFINITE")
        _need(p > 0 and q > 0 and math.isfinite(p * q),
              "ORIGINAL_LEVEL_FLOAT_UNREPRESENTABLE")
        levels.append((p, q))
        seen.add(price)
    return levels


def check_r11_book_snapshot_math(
    candidate: R11MayakFeatureCandidate,
    *,
    market: str,
    bids: tuple[tuple[Decimal, Decimal], ...],
    asks: tuple[tuple[Decimal, Decimal], ...],
) -> R11BookSnapshotMathCheck:
    """Compare exact legacy MAYAK book feature formulas to compact BOOK.

    Full raw book levels are independently supplied to this *test-only*
    verifier, not obtained from (or synthesized out of) compact band totals.
    A passing check is a mathematical sample, NOT original WS provenance.
    """
    _source(candidate)
    _need(market in ("spot", "linear"), "PRODUCT_SCOPE_INVALID")
    compact = (
        candidate.spot_book_bands if market == "spot"
        else candidate.linear_book_bands
    )
    _need(isinstance(compact, dict), "BOOK_COMPACT_FIELDS_MISSING")
    bid_levels = _ladder(bids, max_levels=50)
    ask_levels = _ladder(asks, max_levels=50)
    _need(
        max(p for p, _ in bid_levels) < min(p for p, _ in ask_levels),
        "ORIGINAL_LADDER_CROSSED",
    )
    engine = LiveMayakEngine((candidate.symbol,))
    engine.on_book(
        market, candidate.symbol, candidate.as_of_second.timestamp(),
        bid_levels, ask_levels,
    )
    expected = engine.books[(market, candidate.symbol)]
    sorted_bids = sorted(bid_levels, reverse=True)
    sorted_asks = sorted(ask_levels)
    original_fields = {
        "best_bid": sorted_bids[0][0],
        "best_ask": sorted_asks[0][0],
        "bid_size": sorted_bids[0][1],
        "ask_size": sorted_asks[0][1],
    }
    for side in ("bid", "ask"):
        for bps in _BOOK_BANDS:
            original_fields[f"{side}_{bps}bps_usd"] = (
                expected[f"{side}_depth_{bps}bps_usd"]
            )
    for key in _BOOK_KEYS:
        actual = compact.get(key)
        _need(
            isinstance(actual, Decimal) and actual.is_finite()
            and actual >= 0,
            "BOOK_COMPACT_FIELD_INVALID",
        )
        observed = _as_float(actual, "BOOK_COMPACT_FLOAT_INVALID")
        _need(
            math.isclose(
                observed, original_fields[key],
                rel_tol=_REL_TOL, abs_tol=_ABS_TOL,
            ),
            f"BOOK_MATH_DRIFT_{key.upper()}",
        )
    return R11BookSnapshotMathCheck(
        symbol=candidate.symbol, market=market,
        publication_id=candidate.publication_id,
        source_session_id=candidate.source_session_id,
        compared_keys=_BOOK_KEYS,
        snapshot_formula_match=True,
    )
