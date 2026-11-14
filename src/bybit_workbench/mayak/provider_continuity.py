"""R11 #470: fail-closed provider-continuity proof for PG MAYAK.

Physical frames and MarketPublication rows are only evidence inputs.  They do
not become continuity by themselves.  A stream interval starts at an exact
provider subscription ACK for the current transport epoch, requires later
physical transport evidence, and is cut by every scoped quality boundary.

REST observations are deliberately POINT_IN_TIME.  Their freshness may be
proven, but they never manufacture websocket history or a 120 minute warmup.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID

_SEVEN = (
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
)
_ID = re.compile(r"^[0-9a-f]{32}$")
_RELEASE = re.compile(r"^[0-9a-f]{40}$")
_QUALITY = frozenset((
    "GAP", "NO_DATA", "STALE", "NOT_LISTED", "UNKNOWN", "RECOVERING",
))
_ACK_REASON = {
    "SPOT": "PUBLIC_SPOT_SUBSCRIPTIONS_ACKED",
    "LINEAR": "PUBLIC_LINEAR_SUBSCRIPTIONS_ACKED",
}
_REST_TTL = {"ACCOUNT_RATIO": 650, "INSTRUMENT_INFO": 1800}

Coverage = Literal["STREAM", "POINT_IN_TIME"]
ObservationKind = Literal[
    "TRANSPORT_FRAME", "SOURCE_EVENT", "BOOK_SNAPSHOT",
    "REST_FACT", "REST_NOT_LISTED",
]
ContinuityState = Literal["CONTINUOUS", "POINT_CURRENT", "WARMUP", "UNKNOWN"]


class R11ProviderContinuityUnverified(RuntimeError):
    """Continuity input identity or exact evidence contract is invalid."""


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise R11ProviderContinuityUnverified(
            "R11_PROVIDER_CONTINUITY_" + reason
        )


def _utc(value: datetime, reason: str) -> datetime:
    _need(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0),
        reason,
    )
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True, order=True)
class R11ProviderScope:
    product: str
    dataset: str
    topic: str
    symbol: str
    coverage: Coverage


@dataclass(frozen=True, slots=True)
class R11ProviderQualityBoundary:
    transport_epoch_id: str
    product: str
    topic: str
    symbol: str
    quality: str
    reason: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class R11ProviderObservation:
    transport_epoch_id: str
    product: str
    topic: str
    symbol: str
    observed_at: datetime
    kind: ObservationKind


@dataclass(frozen=True, slots=True)
class R11ProviderScopeContinuity:
    scope: R11ProviderScope
    transport_epoch_id: str
    state: ContinuityState
    continuous_from: datetime | None
    observed_through: datetime | None
    reason: str


@dataclass(frozen=True, slots=True)
class R11ProviderContinuityProof:
    publication_id: int
    source_session_id: str
    release_commit: str
    as_of_second: datetime
    transport_epochs: tuple[tuple[str, str], ...]
    scopes: tuple[R11ProviderScopeContinuity, ...]
    provider_continuity_verified: bool
    mayak_ready: bool = False

    def scope(self, product: str, dataset: str, symbol: str) -> (
        R11ProviderScopeContinuity
    ):
        matches = tuple(
            row for row in self.scopes
            if row.scope.product == product
            and row.scope.dataset == dataset
            and row.scope.symbol == symbol
        )
        _need(len(matches) == 1, "PROOF_SCOPE_NOT_EXACT")
        return matches[0]


def r11_provider_continuity_proof_matches(
    proof: object, *,
    publication_id: int,
    source_session_id: str,
    release_commit: str,
    as_of_second: datetime,
) -> bool:
    """Reject a copied bool or hand-built partial proof at trust boundaries."""
    if not isinstance(proof, R11ProviderContinuityProof):
        return False
    required = set(r11_required_provider_scopes())
    epochs = dict(proof.transport_epochs)
    return (
        proof.publication_id == publication_id
        and proof.source_session_id == source_session_id
        and proof.release_commit == release_commit
        and proof.as_of_second == as_of_second
        and proof.provider_continuity_verified is True
        and proof.mayak_ready is False
        and set(epochs) == {"SPOT", "LINEAR"}
        and epochs["LINEAR"] == source_session_id
        and len(proof.scopes) == len(required) == 63
        and {row.scope for row in proof.scopes} == required
        and all(
            row.transport_epoch_id == epochs[row.scope.product]
            and row.state in ("CONTINUOUS", "POINT_CURRENT")
            and row.continuous_from is not None
            and row.observed_through is not None
            and row.continuous_from <= row.observed_through <= as_of_second
            and (
                (row.scope.coverage == "STREAM"
                 and row.state == "CONTINUOUS")
                or (row.scope.coverage == "POINT_IN_TIME"
                    and row.state == "POINT_CURRENT")
            )
            for row in proof.scopes
        )
    )


def r11_required_provider_scopes() -> tuple[R11ProviderScope, ...]:
    """Exact seven-symbol observation panel; BTC/ETH remain non-trading."""
    scopes: list[R11ProviderScope] = []
    for symbol in _SEVEN:
        scopes.extend((
            R11ProviderScope(
                "LINEAR", "PUBLIC_TRADE", f"publicTrade.{symbol}",
                symbol, "STREAM",
            ),
            R11ProviderScope(
                "SPOT", "PUBLIC_TRADE", f"publicTrade.{symbol}",
                symbol, "STREAM",
            ),
            R11ProviderScope(
                "SPOT", "ORDERBOOK_50", f"orderbook.50.{symbol}",
                symbol, "STREAM",
            ),
            R11ProviderScope(
                "LINEAR", "ORDERBOOK_50", f"orderbook.50.{symbol}",
                symbol, "STREAM",
            ),
            R11ProviderScope(
                "LINEAR", "TICKER", f"tickers.{symbol}",
                symbol, "STREAM",
            ),
            R11ProviderScope(
                "LINEAR", "LIQUIDATION", f"allLiquidation.{symbol}",
                symbol, "STREAM",
            ),
            R11ProviderScope(
                "LINEAR", "ACCOUNT_RATIO",
                f"REST:market/account-ratio:{symbol}", symbol,
                "POINT_IN_TIME",
            ),
            R11ProviderScope(
                "SPOT", "INSTRUMENT_INFO",
                f"REST:market/instruments-info:{symbol}", symbol,
                "POINT_IN_TIME",
            ),
            R11ProviderScope(
                "LINEAR", "INSTRUMENT_INFO",
                f"REST:market/instruments-info:{symbol}", symbol,
                "POINT_IN_TIME",
            ),
        ))
    return tuple(scopes)


def _boundary_matches(
    boundary: R11ProviderQualityBoundary,
    scope: R11ProviderScope,
) -> bool:
    return (
        boundary.product == scope.product
        and (
            (boundary.topic == "*" and boundary.symbol == "")
            or (
                boundary.topic == scope.topic
                and boundary.symbol == scope.symbol
            )
        )
    )


def _observation_matches(
    observation: R11ProviderObservation,
    scope: R11ProviderScope,
) -> bool:
    return (
        observation.product == scope.product
        and observation.topic == scope.topic
        and observation.symbol == scope.symbol
    )


def _validate_boundary(
    row: R11ProviderQualityBoundary, *, as_of: datetime,
) -> None:
    _need(
        isinstance(row, R11ProviderQualityBoundary)
        and _ID.fullmatch(row.transport_epoch_id) is not None
        and row.product in ("SPOT", "LINEAR")
        and isinstance(row.topic, str) and bool(row.topic)
        and isinstance(row.symbol, str)
        and (row.topic == "*") == (row.symbol == "")
        and row.quality in _QUALITY
        and isinstance(row.reason, str) and bool(row.reason)
        and _utc(row.observed_at, "BOUNDARY_CLOCK_INVALID") <= as_of,
        "BOUNDARY_INVALID",
    )


def _validate_observation(
    row: R11ProviderObservation, *, as_of: datetime,
) -> None:
    _need(
        isinstance(row, R11ProviderObservation)
        and _ID.fullmatch(row.transport_epoch_id) is not None
        and row.product in ("SPOT", "LINEAR")
        and isinstance(row.topic, str) and bool(row.topic)
        and isinstance(row.symbol, str)
        and (row.topic == "*") == (row.symbol == "")
        and row.kind in (
            "TRANSPORT_FRAME", "SOURCE_EVENT", "BOOK_SNAPSHOT",
            "REST_FACT", "REST_NOT_LISTED",
        )
        and _utc(row.observed_at, "OBSERVATION_CLOCK_INVALID") <= as_of,
        "OBSERVATION_INVALID",
    )


def prove_r11_provider_continuity(
    *,
    publication_id: int,
    source_session_id: str,
    release_commit: str,
    as_of_second: datetime,
    source_state: str,
    subscribed_at: datetime,
    transport_epochs: dict[str, str],
    boundaries: tuple[R11ProviderQualityBoundary, ...],
    observations: tuple[R11ProviderObservation, ...],
) -> R11ProviderContinuityProof:
    """Reduce immutable source evidence to exact per-scope RED/GREEN state.

    A reconnect only produces another RECOVERING boundary.  It cannot reuse
    an earlier epoch, earlier BOOK ladder, or an earlier warm feature window.
    """
    as_of = _utc(as_of_second, "PUBLICATION_CLOCK_INVALID")
    subscribed = _utc(subscribed_at, "SUBSCRIPTION_CLOCK_INVALID")
    _need(
        type(publication_id) is int and publication_id > 0
        and _ID.fullmatch(source_session_id) is not None
        and _RELEASE.fullmatch(release_commit) is not None
        and source_state in ("ACTIVE", "GAP")
        and subscribed <= as_of
        and set(transport_epochs) == {"SPOT", "LINEAR"}
        and all(_ID.fullmatch(value) is not None
                for value in transport_epochs.values())
        and transport_epochs["LINEAR"] == source_session_id
        and isinstance(boundaries, tuple)
        and isinstance(observations, tuple),
        "SOURCE_IDENTITY_INVALID",
    )
    for boundary in boundaries:
        _validate_boundary(boundary, as_of=as_of)
    for observation in observations:
        _validate_observation(observation, as_of=as_of)

    results: list[R11ProviderScopeContinuity] = []
    for scope in r11_required_provider_scopes():
        epoch = transport_epochs[scope.product]
        relevant_boundaries = sorted(
            (
                row for row in boundaries
                if row.transport_epoch_id == epoch
                and _boundary_matches(row, scope)
            ),
            key=lambda row: row.observed_at,
        )
        exact_observations = sorted(
            (
                row for row in observations
                if row.transport_epoch_id == epoch
                and _observation_matches(row, scope)
            ),
            key=lambda row: row.observed_at,
        )

        if scope.coverage == "POINT_IN_TIME":
            usable = tuple(
                row for row in exact_observations
                if row.kind in ("REST_FACT", "REST_NOT_LISTED")
            )
            latest = usable[-1] if usable else None
            not_listed_ok = (
                latest is not None
                and latest.kind == "REST_NOT_LISTED"
                and scope.dataset == "INSTRUMENT_INFO"
            )
            age_ok = (
                latest is not None
                and as_of - latest.observed_at
                <= timedelta(seconds=_REST_TTL[scope.dataset])
            )
            if latest is None:
                bad = tuple(relevant_boundaries)
            else:
                bad = tuple(
                    row for row in relevant_boundaries
                    if row.observed_at > latest.observed_at
                    or (
                        row.observed_at == latest.observed_at
                        and not (
                            (
                                row.quality == "RECOVERING"
                                and row.reason == "PUBLIC_REST_FACT_COMMITTED"
                            )
                            or (row.quality == "NOT_LISTED" and not_listed_ok)
                        )
                    )
                )
            ok = source_state == "ACTIVE" and age_ok and not bad
            results.append(R11ProviderScopeContinuity(
                scope=scope, transport_epoch_id=epoch,
                state="POINT_CURRENT" if ok else "UNKNOWN",
                continuous_from=(latest.observed_at if ok and latest else None),
                observed_through=(latest.observed_at if latest else None),
                reason=("CURRENT_PHYSICAL_REST_FACT" if ok
                        else "REST_FACT_STALE_MISSING_OR_DEGRADED"),
            ))
            continue

        acknowledgements = tuple(
            row for row in relevant_boundaries
            if row.topic == "*"
            and row.quality == "RECOVERING"
            and row.reason == _ACK_REASON[scope.product]
        )
        ack = acknowledgements[-1] if acknowledgements else None
        witnesses = tuple(
            row for row in observations
            if row.product == scope.product
            and row.transport_epoch_id == epoch
            and row.topic == "*"
            and row.kind == "TRANSPORT_FRAME"
            and ack is not None
            and row.observed_at >= ack.observed_at
        )
        witness = witnesses[-1] if witnesses else None
        transport_bad = tuple(
            row for row in relevant_boundaries
            if row.topic == "*"
            and (ack is None or row.observed_at > ack.observed_at)
        )
        scoped_bad = tuple(
            row for row in relevant_boundaries
            if row.topic != "*"
            and (ack is None or row.observed_at >= ack.observed_at)
        )
        recovered_at: datetime | None = None
        if scoped_bad:
            boundary = scoped_bad[-1]
            permitted_kinds = (
                ("BOOK_SNAPSHOT",)
                if scope.dataset == "ORDERBOOK_50"
                else ("SOURCE_EVENT",)
            )
            recovered = tuple(
                row for row in exact_observations
                if row.kind in permitted_kinds
                and row.observed_at >= boundary.observed_at
            )
            if recovered:
                recovered_at = recovered[-1].observed_at
            elif (
                scope.dataset == "ORDERBOOK_50"
                and boundary.quality == "RECOVERING"
                and boundary.reason == "PUBLIC_BOOK_SNAPSHOT_RESTORED"
                and any(
                    row.kind == "BOOK_SNAPSHOT"
                    and boundary.observed_at - timedelta(seconds=5)
                    <= row.observed_at <= boundary.observed_at
                    for row in exact_observations
                )
            ):
                # The source emits this boundary only after decoding and
                # applying that exact snapshot.  It starts a fresh interval;
                # it still needs the later product transport witness below.
                recovered_at = boundary.observed_at
        current_transport = (
            witness is not None
            and as_of - witness.observed_at <= timedelta(seconds=70)
        )
        ok = (
            source_state == "ACTIVE"
            and ack is not None
            and ack.observed_at >= subscribed
            and current_transport
            and not transport_bad
            and (not scoped_bad or recovered_at is not None)
        )
        start = (
            max(ack.observed_at, recovered_at)
            if ok and ack is not None and recovered_at is not None
            else (ack.observed_at if ok and ack is not None else None)
        )
        results.append(R11ProviderScopeContinuity(
            scope=scope, transport_epoch_id=epoch,
            state="CONTINUOUS" if ok else "WARMUP",
            continuous_from=start,
            observed_through=(witness.observed_at if witness else None),
            reason=("ACKED_EPOCH_WITH_CURRENT_PHYSICAL_WITNESS"
                    if ok else "ACK_WITNESS_OR_RECOVERY_MISSING"),
        ))

    proof = R11ProviderContinuityProof(
        publication_id=publication_id,
        source_session_id=source_session_id,
        release_commit=release_commit,
        as_of_second=as_of,
        transport_epochs=tuple(sorted(transport_epochs.items())),
        scopes=tuple(results),
        provider_continuity_verified=(
            source_state == "ACTIVE"
            and len(results) == 63
            and all(
                row.state in ("CONTINUOUS", "POINT_CURRENT")
                for row in results
            )
        ),
    )
    _need(not proof.mayak_ready, "FALSE_MAYAK_READY")
    return proof


def read_r11_provider_quality_boundaries(
    connection: Any, *,
    source_session_id: str,
    release_commit: str,
    through: datetime,
) -> tuple[R11ProviderQualityBoundary, ...]:
    """Read append-only quality evidence inside caller's RR/RO transaction."""
    at = _utc(through, "QUALITY_READ_CLOCK_INVALID")
    _need(
        _ID.fullmatch(source_session_id) is not None
        and _RELEASE.fullmatch(release_commit) is not None
        and connection.execute("SHOW transaction_isolation").fetchone()
        == ("repeatable read",)
        and connection.execute("SHOW transaction_read_only").fetchone()
        == ("on",),
        "QUALITY_READ_SCOPE_INVALID",
    )
    rows = connection.execute(
        """SELECT transport_epoch_id,product_category,dataset_topic,
                  external_symbol,quality_state,reason,observed_at
             FROM analytics.public_market_source_quality_events
            WHERE source_session_id=%s AND producer_release_commit=%s
              AND observed_at<=%s
            ORDER BY observed_at,quality_event_id""",
        (source_session_id, release_commit, at),
    ).fetchall()
    _need(isinstance(rows, (tuple, list)), "QUALITY_ROWS_INVALID")
    boundaries = tuple(R11ProviderQualityBoundary(
        transport_epoch_id=row[0], product=row[1], topic=row[2],
        symbol=row[3], quality=row[4], reason=row[5],
        observed_at=_utc(row[6], "QUALITY_ROW_CLOCK_INVALID"),
    ) for row in rows)
    for boundary in boundaries:
        _validate_boundary(boundary, as_of=at)
    return boundaries


def read_r11_provider_continuity_for_candidate(
    connection: Any, *,
    publication_id: int,
    source_session_id: str,
    release_commit: str,
    as_of_second: datetime,
    transport_epochs: dict[str, str],
    transport_witnesses: tuple[R11ProviderObservation, ...],
    optional_events: tuple[Any, ...],
    rest_facts: tuple[Any, ...],
) -> R11ProviderContinuityProof:
    """Build the proof from the same immutable RR/RO candidate transaction."""
    source = connection.execute(
        """SELECT state,subscribed_at,market_source_id,producer_release_commit
             FROM analytics.native_public_trade_source_sessions
            WHERE source_session_id=%s""",
        (source_session_id,),
    ).fetchone()
    _need(
        isinstance(source, (tuple, list)) and len(source) == 4
        and source[0] in ("ACTIVE", "GAP")
        and _utc(source[1], "SOURCE_SUBSCRIPTION_CLOCK_INVALID")
        <= as_of_second
        and source[2] == BYBIT_PUBLIC_MARKET_SOURCE_ID
        and source[3] == release_commit,
        "SOURCE_SESSION_ROW_INVALID",
    )
    boundaries = read_r11_provider_quality_boundaries(
        connection,
        source_session_id=source_session_id,
        release_commit=release_commit,
        through=as_of_second,
    )
    observations = list(transport_witnesses)
    topic_by_event = {
        "TRADE": "publicTrade.",
        "BOOK": "orderbook.50.",
        "TICKER": "tickers.",
        "LIQUIDATION": "allLiquidation.",
    }
    for event in optional_events:
        _need(
            getattr(event, "event", None) in topic_by_event
            and getattr(event, "product", None) in ("SPOT", "LINEAR")
            and getattr(event, "symbol", None) in _SEVEN
            and isinstance(getattr(event, "received_at", None), datetime),
            "OPTIONAL_EVENT_CONTINUITY_EVIDENCE_INVALID",
        )
        product = event.product
        frame_type = getattr(event, "frame_type", "")
        kind: ObservationKind = (
            "BOOK_SNAPSHOT"
            if event.event == "BOOK" and frame_type == "snapshot"
            else "SOURCE_EVENT"
        )
        observations.append(R11ProviderObservation(
            transport_epoch_id=transport_epochs[product],
            product=product,
            topic=topic_by_event[event.event] + event.symbol,
            symbol=event.symbol,
            observed_at=event.received_at,
            kind=kind,
        ))
    for rest in rest_facts:
        _need(
            getattr(rest, "symbol", None) in _SEVEN
            and getattr(rest, "source_session_id", None) == source_session_id
            and getattr(rest, "release_commit", None) == release_commit,
            "REST_CONTINUITY_EVIDENCE_INVALID",
        )
        points: tuple[
            tuple[str, str, datetime | None, ObservationKind], ...
        ] = (
            (
                "LINEAR", "ACCOUNT_RATIO", rest.ratio_received_at,
                "REST_FACT",
            ),
            (
                "SPOT", "INSTRUMENT_INFO", rest.spot_instrument_received_at,
                "REST_FACT" if rest.spot_supported else "REST_NOT_LISTED",
            ),
            (
                "LINEAR", "INSTRUMENT_INFO",
                rest.linear_instrument_received_at,
                "REST_FACT" if rest.linear_supported else "REST_NOT_LISTED",
            ),
        )
        for product, dataset, observed, kind in points:
            _need(
                isinstance(observed, datetime),
                "REST_RECEIPT_CLOCK_MISSING",
            )
            assert isinstance(observed, datetime)
            prefix = (
                "REST:market/account-ratio:"
                if dataset == "ACCOUNT_RATIO"
                else "REST:market/instruments-info:"
            )
            observations.append(R11ProviderObservation(
                transport_epoch_id=transport_epochs[product],
                product=product, topic=prefix + rest.symbol,
                symbol=rest.symbol, observed_at=observed, kind=kind,
            ))
    return prove_r11_provider_continuity(
        publication_id=publication_id,
        source_session_id=source_session_id,
        release_commit=release_commit,
        as_of_second=as_of_second,
        source_state=source[0],
        subscribed_at=_utc(source[1], "SOURCE_SUBSCRIPTION_CLOCK_INVALID"),
        transport_epochs=transport_epochs,
        boundaries=boundaries,
        observations=tuple(observations),
    )
