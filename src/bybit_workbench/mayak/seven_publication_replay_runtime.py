"""R11 #470: stateful PG-original MAYAK replay without any network collector.

This is the SOURCE-ONLY in-memory consumer engine, not an enabled production
service. Physical source continuity, durable restart, operator cutover,
Dispatcher runtime equivalence and LIVE remain independent acceptance gates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.mayak.causal_warmup import (
    ContinuityKey,
    R11CausalWarmupProof,
    R11CausalWarmupUnverified,
    evaluate_r11_causal_warmup,
    reconcile_r11_engine_continuity,
)
from bybit_workbench.mayak.core.live import LiveMayakEngine
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakReplayUnverified,
)
from bybit_workbench.mayak.provider_continuity import (
    r11_provider_continuity_proof_matches,
)
from bybit_workbench.mayak.publication_rest_parity import (
    R11MayakRestPublicationUnverified,
    apply_r11_mayak_rest_candidates_offline,
)
from bybit_workbench.mayak.seven_publication_observation_candidate import (
    R11SevenMayakCollectionUnverified,
    R11SevenMayakObservationCandidate,
    R11SevenMayakReplayCheckpoint,
)

_SYMBOLS = (
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
)


class R11MayakReplayNotReady(RuntimeError):
    """One original PG epoch/replay/snapshot contract was not verified."""


def _need(value: bool, reason: str) -> None:
    if not value:
        raise R11MayakReplayNotReady("R11_PG_MAYAK_" + reason)


@dataclass(slots=True)
class R11MayakPublicationReplay:
    """A causal seven-coin MAYAK calculation session from immutable PG data.

    The cursor is ONLY in memory: NEVER resume it after a process restart
    without an independently verified and atomically committed engine state.
    This processor always starts from the beginning of the current source
    epoch, and never authorizes automatic collector socket retirement.
    """

    engine: LiveMayakEngine = field(
        default_factory=lambda: LiveMayakEngine(_SYMBOLS),
    )
    checkpoint: R11SevenMayakReplayCheckpoint | None = None
    first_publication_at: datetime | None = None
    last_original_event_at: float | None = None
    failed: bool = False
    observations: int = 0
    require_provider_continuity: bool = False
    continuity_starts: dict[ContinuityKey, datetime] = field(
        default_factory=dict,
    )
    warmup_proof: R11CausalWarmupProof | None = None

    def apply(
        self, candidate: R11SevenMayakObservationCandidate, *,
        now: datetime,
    ) -> dict[str, Any]:
        """Advance one fully read PG publication; refuse detached resumes.

        No source transport is declared CONNECTED from publication presence.
        Full MAYAK WARMUP needs 120m of live original data and external
        continuity evidence, neither assumed by this function.
        """
        _need(not self.failed, "PREVIOUS_SOURCE_FAILURE")
        try:
            return self._apply(candidate, now=now)
        except (
            R11MayakReplayNotReady,
            R11SevenMayakCollectionUnverified,
            R11MayakRestPublicationUnverified,
            R11OptionalMayakReplayUnverified,
            R11CausalWarmupUnverified,
            TypeError, ValueError, OverflowError,
        ):
            self.failed = True
            raise

    def _apply(
        self, candidate: R11SevenMayakObservationCandidate, *,
        now: datetime,
    ) -> dict[str, Any]:
        proof = candidate.provider_continuity_proof
        continuity_ok = (
            candidate.provider_continuity_verified is False
            and proof is None
            if not self.require_provider_continuity
            else (
                candidate.provider_continuity_verified is True
                and r11_provider_continuity_proof_matches(
                    proof,
                    publication_id=candidate.publication_id,
                    source_session_id=candidate.source_session_id,
                    release_commit=candidate.release_commit,
                    as_of_second=candidate.as_of_second,
                )
            )
        )
        _need(
            isinstance(candidate, R11SevenMayakObservationCandidate)
            and isinstance(now, datetime)
            and now.tzinfo is not None
            and now.utcoffset() == timedelta(0)
            and candidate.physical_source_refs_verified is True
            and continuity_ok
            and candidate.durable_cursor_and_warmup_verified is False
            and candidate.dispatcher_seven_coin_parity_verified is False
            and candidate.mayak_cutover_ready is False
            and candidate.has_more_source_pages is False
            and candidate.read_after_checkpoint == self.checkpoint
            and len(candidate.optional_cursors) == 2
            and len(candidate.native_cursors) == 7
            and len(candidate.rest_facts) == 7
            and {x.symbol for x in candidate.rest_facts} == set(_SYMBOLS)
            and {x.product_category for x in candidate.optional_cursors}
            == {"SPOT", "LINEAR"}
            and {x.symbol for x in candidate.native_cursors}
            == set(_SYMBOLS)
            and candidate.as_of_second.tzinfo is not None
            and candidate.as_of_second.utcoffset() == timedelta(0)
            and timedelta(0) <= now - candidate.as_of_second
            <= timedelta(seconds=30),
            "PUBLICATION_OR_RESUME_INVALID",
        )
        if self.checkpoint is not None:
            _need(
                candidate.source_session_id == self.checkpoint.source_session_id
                and candidate.release_commit == self.checkpoint.release_commit
                and candidate.publication_id >= self.checkpoint.publication_id
                and candidate.as_of_second >= self.checkpoint.as_of_second,
                "EPOCH_OR_PUBLICATION_REGRESSION",
            )
        _need(
            all(
                row.publication_id == candidate.publication_id
                and row.source_session_id == candidate.source_session_id
                and row.release_commit == candidate.release_commit
                and row.as_of == candidate.as_of_second
                and row.physical_rest_proven
                and not row.mayak_cutover_ready
                for row in candidate.rest_facts
            )
            and all(
                row.source_session_id == candidate.source_session_id
                and row.producer_release_commit == candidate.release_commit
                for row in candidate.optional_cursors
            )
            and all(
                row.source_session_id == candidate.source_session_id
                and row.producer_release_commit == candidate.release_commit
                and row.publication_id == candidate.publication_id
                for row in candidate.native_cursors
            ),
            "SOURCE_LINEAGE_DRIFT",
        )
        if self.require_provider_continuity:
            assert proof is not None
            self.continuity_starts = reconcile_r11_engine_continuity(
                self.engine,
                proof=proof,
                previous_starts=self.continuity_starts,
            )
        original_events: list[tuple[float, int, int, Any]] = []
        for index, event in enumerate(candidate.optional_events):
            _need(
                event.symbol in _SYMBOLS
                and event.product in ("SPOT", "LINEAR"),
                "OPTIONAL_EVENT_SCOPE_INVALID",
            )
            original_events.append((event.timestamp, 0, index, event))
        for index, trade in enumerate(candidate.native_linear_trades):
            _need(
                trade.symbol in _SYMBOLS and trade.market == "linear",
                "NATIVE_TRADE_SCOPE_INVALID",
            )
            original_events.append((trade.timestamp, 1, index, trade))
        # Validate the *entire* batch before calling any engine callback.
        # One late event after the committed causal edge is UNKNOWN rather
        # than silently reordering historical trade/liquidation/book windows.
        events = sorted(original_events)
        last = self.last_original_event_at
        for timestamp, _family, _index, _event in events:
            _need(
                isinstance(timestamp, float)
                and timestamp <= candidate.as_of_second.timestamp() + 5
                and (last is None or timestamp >= last),
                "ORIGINAL_EVENT_TIME_REGRESSED",
            )
            last = timestamp
        apply_r11_mayak_rest_candidates_offline(
            self.engine, candidate.rest_facts,
        )
        for _timestamp, family, _index, event in events:
            if family == 0:
                event.apply_offline(self.engine)
            else:
                self.engine.on_trade(*event.on_trade_args())

        self.checkpoint = candidate.checkpoint()
        self.last_original_event_at = last
        if self.first_publication_at is None:
            self.first_publication_at = candidate.as_of_second
        self.observations += 1
        snapshot = self.engine.snapshot(now.astimezone(UTC))
        _need(
            set(snapshot["coins"]) == set(_SYMBOLS)
            and snapshot["config_fingerprint"]
            == self.engine.configuration_fingerprint()
            and snapshot["dispatcher_handoff"]["provenance"][
                "config_fingerprint"
            ] == snapshot["config_fingerprint"],
            "MAYAK_DISPATCHER_CALCULATION_DRIFT",
        )
        elapsed = (
            candidate.as_of_second - self.first_publication_at
        ).total_seconds()
        books_by_product = {
            item.product_category: {
                book.symbol for book in item.books
            }
            for item in candidate.optional_cursors
        }
        complete_books = all(
            books_by_product.get(product) == set(_SYMBOLS)
            for product in ("SPOT", "LINEAR")
        )
        warmup: R11CausalWarmupProof | None = None
        if self.require_provider_continuity:
            assert proof is not None
            warmup = evaluate_r11_causal_warmup(
                self.engine, provider_proof=proof,
                snapshot=snapshot, now=now,
            )
            self.warmup_proof = warmup
        minimum_continuity = (
            min(
                row.continuous_seconds for row in warmup.scopes
                if row.required_seconds > 0
            )
            if warmup is not None else max(0, elapsed)
        )
        # Elapsed time and book snapshots are necessary but NOT sufficient:
        # disconnects/transport coverage and persisted restart continuity
        # require an independent current-source proof.
        snapshot["r11_market_publication"] = {
            "publication_id": candidate.publication_id,
            "source_session_id": candidate.source_session_id,
            "producer_release_commit": candidate.release_commit,
            "as_of_second": candidate.as_of_second.isoformat(),
            "source_mode": "PG_ORIGINAL_REPLAY",
            "observational_symbols": list(_SYMBOLS),
            "warmup_seconds_observed": minimum_continuity,
            "warmup_time_sufficient": (
                warmup.causal_warmup_verified
                if warmup is not None else elapsed >= 7200
            ),
            "book_snapshot_coverage_sufficient": (
                all(
                    row.engine_coverage_verified
                    for row in warmup.scopes
                    if row.provider_scope.dataset == "ORDERBOOK_50"
                )
                if warmup is not None else complete_books
            ),
            "causal_warmup_verified": (
                warmup.causal_warmup_verified
                if warmup is not None else False
            ),
            "warmup_scopes": (
                warmup.as_status_rows() if warmup is not None else []
            ),
            "source_transport_continuity_verified": (
                self.require_provider_continuity
            ),
            "durable_restart_verified": False,
            "dispatcher_runtime_parity_verified": False,
            "consumer_ready": False,
            "mayak_ws_retirement_allowed": False,
            "trading_effect": "NONE",
        }
        handoff = snapshot["dispatcher_handoff"]
        handoff["provenance"]["r11_market_publication_id"] = (
            candidate.publication_id
        )
        handoff["provenance"]["r11_source_session_id"] = (
            candidate.source_session_id
        )
        handoff["provenance"]["r11_source_continuity_verified"] = (
            self.require_provider_continuity
        )
        handoff["provenance"]["r11_causal_warmup_verified"] = (
            warmup.causal_warmup_verified if warmup is not None else False
        )
        handoff["provenance"]["r11_consumer_ready"] = False
        _need(
            all(
                bool(row.get("connected"))
                == self.require_provider_continuity
                for row in self.engine.transport.values()
            ),
            "TRANSPORT_PROOF_STATE_DRIFT",
        )
        return snapshot
