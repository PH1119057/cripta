"""#470 R11: published MAYAK REST account-ratio and instrument support parity.

One REPEATABLE READ READ ONLY transaction, one immutable publication ID.
Only the existing canonical Native MarketSource acquires REST facts. No
consumer REST fallback, no websocket, no second writer or live cutover.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.mayak.core.live import LiveMayakEngine

_SEVEN = frozenset((
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
))
_SHA32 = re.compile(r"^[a-f0-9]{32}$")
_SHA40 = re.compile(r"^[a-f0-9]{40}$")
_DATASETS = (("SPOT", "INSTRUMENT_INFO"),
             ("LINEAR", "INSTRUMENT_INFO"),
             ("LINEAR", "ACCOUNT_RATIO"))
_TTL = {"INSTRUMENT_INFO": 1800, "ACCOUNT_RATIO": 650}


class R11MayakRestPublicationUnverified(RuntimeError):
    """Source-immutable observational REST feature not proven."""


@dataclass(frozen=True, slots=True)
class R11MayakRestCandidate:
    symbol: str
    publication_id: int
    source_session_id: str
    release_commit: str
    as_of: datetime
    ratio_sample_at: datetime
    long_ratio: float
    short_ratio: float
    spot_supported: bool
    linear_supported: bool
    physical_rest_proven: bool = True
    # Still not full seven-source continuity or MAYAK/Dispatcher parity.
    mayak_cutover_ready: bool = False


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise R11MayakRestPublicationUnverified(
            "R11_MAYAK_REST_" + reason
        )


def _utc(value: object) -> datetime:
    _need(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0),
        "CLOCK_NOT_UTC",
    )
    assert isinstance(value, datetime)
    return value.astimezone(UTC)


def _asof_str(raw: object) -> datetime:
    _need(isinstance(raw, str), "REFERENCE_TIME_TYPE_INVALID")
    assert isinstance(raw, str)
    try:
        return _utc(datetime.fromisoformat(raw))
    except ValueError as exc:
        raise R11MayakRestPublicationUnverified(
            "R11_MAYAK_REST_REFERENCE_TIME_INVALID"
        ) from exc


def _ratio(raw: object) -> float:
    _need(isinstance(raw, str), "RATIO_TYPE_INVALID")
    assert isinstance(raw, str)
    try:
        value = Decimal(raw)
    except (ValueError, InvalidOperation) as exc:
        raise R11MayakRestPublicationUnverified(
            "R11_MAYAK_REST_RATIO_INVALID"
        ) from exc
    _need(
        value.is_finite() and Decimal("0") <= value <= Decimal("1"),
        "RATIO_RANGE_INVALID",
    )
    number = float(value)
    _need(math.isfinite(number), "RATIO_OVERFLOW")
    return number


def read_r11_mayak_rest_candidate(
    connection: Any, *, publication_id: int, symbol: str,
) -> R11MayakRestCandidate:
    """Check each physical REST fact by exact immutable source reference.

    Missing/stale/non-trading instrument observation cannot be promoted to
    silent active. The caller has already opened READ ONLY REPEATABLE READ.
    """
    _need(
        type(publication_id) is int and publication_id > 0
        and symbol in _SEVEN,
        "SYMBOL_OR_PUBLICATION_SCOPE_INVALID",
    )
    _need(
        connection.execute("SHOW transaction_isolation").fetchone()
        == ("repeatable read",)
        and connection.execute("SHOW transaction_read_only").fetchone()
        == ("on",),
        "ATOMIC_READ_ONLY_REQUIRED",
    )
    row = connection.execute(
        """SELECT market_source_id,source_session_id,producer_release_commit,
                  as_of_second,publication_status,
                  rest_observation_refs,rest_field_coverage
             FROM analytics.market_publications
            WHERE publication_id=%s""",
        (publication_id,),
    ).fetchone()
    _need(isinstance(row, (tuple, list)) and len(row) == 7,
          "PUBLICATION_NOT_FOUND")
    source, session, release, at, status, refs, quality = row
    published = _utc(at)
    _need(
        source == BYBIT_PUBLIC_MARKET_SOURCE_ID
        and isinstance(session, str) and _SHA32.fullmatch(session) is not None
        and isinstance(release, str) and _SHA40.fullmatch(release) is not None
        and status == "WATERMARKS_ONLY"
        and published.microsecond == 0
        and isinstance(refs, dict) and isinstance(quality, dict),
        "PUBLICATION_IDENTITY_INVALID",
    )
    values: dict[tuple[str, str], dict[str, object]] = {}
    sample: datetime | None = None
    for product, dataset in _DATASETS:
        instrument = bybit_instrument_id(product.lower(), symbol)
        expected = refs.get(instrument)
        observed = quality.get(instrument)
        _need(
            isinstance(expected, dict) and isinstance(observed, dict)
            and isinstance(expected.get(dataset), dict)
            and isinstance(observed.get(dataset), dict)
            and observed[dataset].get("status") in (
                ("OBSERVED", "NOT_LISTED") if dataset == "INSTRUMENT_INFO"
                else ("OBSERVED",)
            ),
            "REF_OR_QUALITY_MISSING",
        )
        ref = expected[dataset]
        _need(
            ref.get("source_session_id") == session
            and ref.get("instrument_id") == instrument
            and ref.get("product_category") == product
            and ref.get("external_symbol") == symbol
            and ref.get("dataset") == dataset
            and type(ref.get("rest_observation_id")) is int
            and ref["rest_observation_id"] > 0,
            "PHYSICAL_REFERENCE_IDENTITY_INVALID",
        )
        source_at = _asof_str(ref.get("source_at"))
        received_at = _asof_str(ref.get("received_at"))
        _need(
            source_at <= received_at + timedelta(seconds=5)
            and received_at < published and source_at < published
            and timedelta(0) <= published - source_at
            <= timedelta(seconds=_TTL[dataset]),
            "REFERENCE_STALE_OR_FUTURE",
        )
        physical = connection.execute(
            """SELECT market_source_id,source_session_id,producer_release_commit,
                      instrument_id,product_category,external_symbol,dataset,
                      source_at,received_at,source_period,compact_values
                 FROM analytics.public_market_rest_observations
                WHERE rest_observation_id=%s""",
            (ref["rest_observation_id"],),
        ).fetchone()
        _need(
            isinstance(physical, (tuple, list)) and len(physical) == 11,
            "PHYSICAL_ROW_MISSING",
        )
        (
            p_source, p_session, p_release, p_inst, p_product, p_symbol,
            p_dataset, p_time, p_received, period, payload,
        ) = physical
        _need(
            p_source == source and p_session == session
            and p_release == release and p_inst == instrument
            and p_product == product and p_symbol == symbol
            and p_dataset == dataset
            and _utc(p_time) == source_at
            and _utc(p_received) == received_at
            and period == ("5min" if dataset == "ACCOUNT_RATIO" else "")
            and isinstance(payload, dict),
            "PHYSICAL_ORIGIN_DRIFT",
        )
        values[product, dataset] = payload
        if dataset == "ACCOUNT_RATIO":
            sample = source_at
    ratio = values["LINEAR", "ACCOUNT_RATIO"]
    long = _ratio(ratio.get("long_account_ratio"))
    short = _ratio(ratio.get("short_account_ratio"))
    _need(abs(long + short - 1.0) <= 0.015,
          "ACCOUNT_RATIO_NOT_COMPLEMENTARY")
    spot = values["SPOT", "INSTRUMENT_INFO"].get("status")
    linear = values["LINEAR", "INSTRUMENT_INFO"].get("status")
    _need(
        spot in ("Trading", "NOT_LISTED")
        and linear in ("Trading", "NOT_LISTED"),
        "INSTRUMENT_SUPPORT_INVALID",
    )
    _need(sample is not None, "RATIO_SAMPLE_MISSING")
    assert sample is not None
    return R11MayakRestCandidate(
        symbol=symbol, publication_id=publication_id,
        source_session_id=session, release_commit=release,
        as_of=published, ratio_sample_at=sample,
        long_ratio=long, short_ratio=short,
        spot_supported=spot == "Trading",
        linear_supported=linear == "Trading",
    )


def apply_r11_mayak_rest_candidates_offline(
    engine: LiveMayakEngine,
    candidates: tuple[R11MayakRestCandidate, ...],
) -> None:
    """Only disposable seven-panel replay; NEVER a live consumer cutover.

    The old collector sources seven public REST ratios and two product
    support sets. This adapter uses the same real engine callbacks, but
    only after ALL seven immutable source candidates are individually
    identity/freshness verified by read_r11_mayak_rest_candidate().
    """
    _need(
        isinstance(engine, LiveMayakEngine)
        and isinstance(candidates, tuple)
        and len(candidates) == len(_SEVEN)
        and all(isinstance(row, R11MayakRestCandidate) for row in candidates)
        and {row.symbol for row in candidates} == _SEVEN,
        "SEVEN_PANEL_NOT_COMPLETE",
    )
    first = candidates[0]
    _need(
        all(
            row.publication_id == first.publication_id
            and row.source_session_id == first.source_session_id
            and row.release_commit == first.release_commit
            and row.as_of == first.as_of
            and row.physical_rest_proven is True
            and row.mayak_cutover_ready is False
            and row.ratio_sample_at <= row.as_of
            and 0 <= row.long_ratio <= 1
            and 0 <= row.short_ratio <= 1
            and math.isfinite(row.long_ratio)
            and math.isfinite(row.short_ratio)
            and abs(row.long_ratio + row.short_ratio - 1) <= 0.015
            for row in candidates
        ),
        "REST_PARITY_MIXED_OR_FORGED",
    )
    engine.set_instrument_support(
        "spot", {row.symbol for row in candidates if row.spot_supported},
    )
    engine.set_instrument_support(
        "linear", {row.symbol for row in candidates if row.linear_supported},
    )
    for row in candidates:
        engine.on_ticker(
            row.symbol, row.ratio_sample_at.timestamp(),
            long_ratio=row.long_ratio,
            short_ratio=row.short_ratio,
        )
