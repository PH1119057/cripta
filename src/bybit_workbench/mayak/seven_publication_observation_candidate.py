"""#470 R11: one-transaction seven-coin MAYAK original-fact candidate.

This is READ-ONLY and NOT a switched MAYAK runtime. It collects bounded,
publication-pinned physical SPOT/LINEAR originals, native LINEAR trades and
REST facts from the ONE MarketSource. No independent REST/WS writer or
permission to replace the legacy source is inferred from a successful read.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.mayak.native_trade_publication_reader import (
    R11OriginalNativeUnverified,
    read_r11_original_linear_page,
)
from bybit_workbench.mayak.native_trade_replay_parity import (
    R11MayakTradeInput,
    R11ReplayCursor,
    R11TradeReplayUnverified,
    project_r11_native_linear_trade_page,
)
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakCursor,
    R11OptionalMayakEvent,
)
from bybit_workbench.mayak.pinned_original_publication_page import (
    R11PinnedMayakPublicationUnverified,
    read_r11_pinned_mayak_optional_page,
)
from bybit_workbench.mayak.publication_rest_parity import (
    R11MayakRestCandidate,
    R11MayakRestPublicationUnverified,
    read_r11_mayak_rest_candidate,
)

_SEVEN = (
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
)
_MAX_PAGES = 16


class R11SevenMayakCollectionUnverified(RuntimeError):
    """Exact original PG provenance / bounded full-panel page not proven."""


@dataclass(frozen=True, slots=True)
class R11SevenMayakObservationCandidate:
    publication_id: int
    source_session_id: str
    release_commit: str
    as_of_second: datetime
    optional_events: tuple[R11OptionalMayakEvent, ...]
    native_linear_trades: tuple[R11MayakTradeInput, ...]
    rest_facts: tuple[R11MayakRestCandidate, ...]
    optional_cursors: tuple[R11OptionalMayakCursor, ...]
    native_cursors: tuple[R11ReplayCursor, ...]
    # Every event is physically publication-pinned; no REST fallback.
    physical_source_refs_verified: bool = True
    # Missing provider continuity/durable replay/restart/Dispatcher acceptance.
    provider_continuity_verified: bool = False
    durable_cursor_and_warmup_verified: bool = False
    dispatcher_seven_coin_parity_verified: bool = False
    mayak_cutover_ready: bool = False


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise R11SevenMayakCollectionUnverified(
            "R11_SEVEN_MAYAK_" + reason
        )


def collect_r11_seven_mayak_candidate(
    connection: Any, *,
    publication_id: int,
    observed_at: datetime,
) -> R11SevenMayakObservationCandidate:
    """Bounded full source transaction; caller owns RR READ ONLY.

    Bootstrap starts from zero, never an arbitrary unanchored cursor. This
    cannot be used to switch live source because it has no crash-proof
    persisted checkpoints, full provider continuity or WARMUP proof.
    """
    _need(
        type(publication_id) is int and publication_id > 0
        and isinstance(observed_at, datetime)
        and observed_at.tzinfo is not None
        and observed_at.utcoffset() == timedelta(0),
        "ARGUMENT_INVALID",
    )
    _need(
        connection.execute("SHOW transaction_isolation").fetchone()
        == ("repeatable read",)
        and connection.execute("SHOW transaction_read_only").fetchone()
        == ("on",),
        "RR_READ_ONLY_REQUIRED",
    )
    originals: list[R11OptionalMayakEvent] = []
    option_states: list[R11OptionalMayakCursor] = []
    session: str | None = None
    release: str | None = None
    published: datetime | None = None
    epochs: dict[str, str] = {}
    try:
        for product in ("SPOT", "LINEAR"):
            state: R11OptionalMayakCursor | None = None
            for _ in range(_MAX_PAGES):
                result = read_r11_pinned_mayak_optional_page(
                    connection, publication_id=publication_id,
                    product_category=product, previous=state,
                )
                if session is None:
                    session = result.source_session_id
                    published = result.publication_as_of
                _need(
                    result.source_session_id == session
                    and result.publication_as_of == published
                    and result.publication_id == publication_id
                    and result.physical_published_cursor_verified
                    and not result.mayak_ws_retirement_allowed,
                    "CROSS_PRODUCT_PUBLICATION_DRIFT",
                )
                if release is None:
                    release = result.batch.cursor.producer_release_commit
                _need(
                    result.batch.cursor.producer_release_commit == release
                    and result.batch.cursor.source_session_id == session
                    and result.batch.cursor.product_category == product
                    and not result.batch.safe_to_switch_mayak_public_socket,
                    "CROSS_PRODUCT_EPOCH_DRIFT",
                )
                if product not in epochs:
                    epochs[product] = result.transport_epoch_id
                _need(
                    epochs[product] == result.transport_epoch_id,
                    "TRANSPORT_EPOCH_CHANGED_IN_PAGE",
                )
                originals.extend(result.batch.events)
                state = result.batch.cursor
                if not result.batch.has_more_pages:
                    option_states.append(state)
                    break
            else:
                _need(False, "OPTIONAL_FRAME_BACKLOG_EXCEEDS_BOUND")

        native: list[R11MayakTradeInput] = []
        native_states: list[R11ReplayCursor] = []
        for symbol in _SEVEN:
            previous: R11ReplayCursor | None = None
            after = (0, 0)
            for _ in range(_MAX_PAGES):
                page = read_r11_original_linear_page(
                    connection, symbol=symbol, observed_at=observed_at,
                    read_after=after,
                )
                _need(
                    page.publication_id == publication_id
                    and page.source_session_id == session
                    and page.producer_release_commit == release
                    and page.publication_as_of == published
                    and not page.mayak_cutover_ready,
                    "NATIVE_TRADE_PUBLICATION_DRIFT",
                )
                batch = project_r11_native_linear_trade_page(
                    page, previous=previous,
                )
                native.extend(batch.events)
                previous = batch.cursor
                after = batch.cursor.cursor
                if not batch.has_more_pages:
                    native_states.append(batch.cursor)
                    break
            else:
                _need(False, "NATIVE_TRADE_BACKLOG_EXCEEDS_BOUND")

        rest = tuple(
            read_r11_mayak_rest_candidate(
                connection, publication_id=publication_id, symbol=symbol,
            )
            for symbol in _SEVEN
        )
        _need(
            session is not None and release is not None
            and published is not None
            and len(option_states) == 2
            and len(native_states) == 7
            and all(
                row.publication_id == publication_id
                and row.source_session_id == session
                and row.release_commit == release
                and row.as_of == published
                and row.physical_rest_proven
                and not row.mayak_cutover_ready
                for row in rest
            )
            and timedelta(0) <= observed_at - published
            <= timedelta(seconds=30),
            "FULL_SEVEN_REST_PROVENANCE_OR_FRESHNESS_MISSING",
        )
    except (
        R11PinnedMayakPublicationUnverified, R11OriginalNativeUnverified,
        R11TradeReplayUnverified, R11MayakRestPublicationUnverified,
    ) as exc:
        raise R11SevenMayakCollectionUnverified(
            "R11_SEVEN_MAYAK_SOURCE_READ_FAILED"
        ) from exc
    assert session is not None and release is not None and published is not None
    return R11SevenMayakObservationCandidate(
        publication_id=publication_id,
        source_session_id=session, release_commit=release,
        as_of_second=published,
        optional_events=tuple(originals),
        native_linear_trades=tuple(native),
        rest_facts=rest,
        optional_cursors=tuple(option_states),
        native_cursors=tuple(native_states),
    )
