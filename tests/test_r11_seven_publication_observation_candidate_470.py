"""#470 seven-panel bounded PG-only MAYAK candidate, no runtime cutover."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import bybit_workbench.mayak.seven_publication_observation_candidate as stage
from bybit_workbench.mayak.native_trade_publication_reader import (
    R11OriginalNativePage,
)
from bybit_workbench.mayak.native_trade_replay_parity import (
    R11ReplayCursor,
    R11TradeParityBatch,
)
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakBatch,
    R11OptionalMayakCursor,
)
from bybit_workbench.mayak.pinned_original_publication_page import (
    R11PinnedMayakPublicationPage,
)
from bybit_workbench.mayak.publication_rest_parity import (
    R11MayakRestCandidate,
)

SESSION = "a" * 32
SPOT_EPOCH = "b" * 32
LINEAR_EPOCH = "c" * 32
RELEASE = "d" * 40
AT = datetime(2026, 11, 14, 16, 0, tzinfo=UTC)
NOW = AT + timedelta(seconds=10)
SEVEN = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
         "BTCUSDT", "ETHUSDT")


class Row:
    def __init__(self, value: tuple[str, ...]):
        self.value = value

    def fetchone(self) -> tuple[str, ...]:
        return self.value


class ReadOnlyPG:
    def __init__(self) -> None:
        self.isolation = "repeatable read"
        self.read_only = "on"
        self.queries: list[str] = []

    def execute(self, sql: str) -> Row:
        self.queries.append(sql)
        return Row((
            self.isolation if "isolation" in sql else self.read_only,
        ))


def optional(product: str) -> R11PinnedMayakPublicationPage:
    epoch = SPOT_EPOCH if product == "SPOT" else LINEAR_EPOCH
    cursor = R11OptionalMayakCursor(
        source_session_id=SESSION, transport_epoch_id=epoch,
        product_category=product, producer_release_commit=RELEASE,
        next_cursor=1, next_digest="e" * 64, books=(),
    )
    return R11PinnedMayakPublicationPage(
        publication_id=42, source_session_id=SESSION,
        transport_epoch_id=epoch, product_category=product,
        publication_as_of=AT, pinned_frame_number=1,
        pinned_frame_sha256="e" * 64,
        batch=R11OptionalMayakBatch(
            cursor=cursor, events=(), has_more_pages=False,
        ),
    )


def native_page(symbol: str) -> R11OriginalNativePage:
    return R11OriginalNativePage(
        symbol=symbol, publication_id=42,
        source_session_id=SESSION, producer_release_commit=RELEASE,
        publication_as_of=AT, publication_cursor=(1, 0),
        read_after=(0, 0), next_cursor=(1, 0),
        original_events=(), more_pages=False,
        last_event_at_publication_cursor=False,
    )


def native_batch(page: R11OriginalNativePage) -> R11TradeParityBatch:
    return R11TradeParityBatch(
        publication_id=42,
        cursor=R11ReplayCursor(
            symbol=page.symbol, source_session_id=SESSION,
            producer_release_commit=RELEASE, publication_id=42,
            publication_as_of=AT, publication_watermark=(1, 0),
            cursor=(1, 0), last_seq=-1, last_event_at=None,
            recent_exec_ids=(),
        ),
        events=(), has_more_pages=False,
    )


def rest(symbol: str) -> R11MayakRestCandidate:
    return R11MayakRestCandidate(
        symbol=symbol, publication_id=42, source_session_id=SESSION,
        release_commit=RELEASE, as_of=AT,
        ratio_sample_at=AT - timedelta(minutes=2),
        long_ratio=0.5, short_ratio=0.5,
        spot_supported=True, linear_supported=True,
    )


def mock_all(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def get_opt(_db: Any, *, publication_id: int,
                product_category: str, previous: Any = None
                ) -> R11PinnedMayakPublicationPage:
        assert publication_id == 42 and previous is None
        seen.append("optional:" + product_category)
        return optional(product_category)

    def get_native(_db: Any, *, symbol: str, observed_at: datetime,
                   read_after: tuple[int, int]) -> R11OriginalNativePage:
        assert isinstance(observed_at, datetime) and read_after == (0, 0)
        seen.append("native:" + symbol)
        return native_page(symbol)

    def get_rest(_db: Any, *, publication_id: int,
                 symbol: str) -> R11MayakRestCandidate:
        assert publication_id == 42
        seen.append("rest:" + symbol)
        return rest(symbol)

    monkeypatch.setattr(stage, "read_r11_pinned_mayak_optional_page", get_opt)
    monkeypatch.setattr(stage, "read_r11_original_linear_page", get_native)
    monkeypatch.setattr(stage, "project_r11_native_linear_trade_page",
                        lambda page, previous=None: native_batch(page))
    monkeypatch.setattr(stage, "read_r11_mayak_rest_candidate", get_rest)
    return seen


def test_one_publication_seven_symbols_three_source_families_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = mock_all(monkeypatch)
    db = ReadOnlyPG()
    probe = stage.collect_r11_seven_mayak_candidate(
        db, publication_id=42, observed_at=NOW,
    )
    assert probe.source_session_id == SESSION
    assert probe.release_commit == RELEASE
    assert probe.physical_source_refs_verified
    assert not probe.provider_continuity_verified
    assert not probe.durable_cursor_and_warmup_verified
    assert not probe.dispatcher_seven_coin_parity_verified
    assert not probe.mayak_cutover_ready
    assert len(probe.optional_cursors) == 2
    assert len(probe.native_cursors) == 7
    assert {item.symbol for item in probe.rest_facts} == set(SEVEN)
    assert seen == (
        ["optional:SPOT", "optional:LINEAR"]
        + ["native:" + symbol for symbol in SEVEN]
        + ["rest:" + symbol for symbol in SEVEN]
    )
    assert db.queries == [
        "SHOW transaction_isolation", "SHOW transaction_read_only",
    ]


@pytest.mark.parametrize("change", [
    "not_rr", "not_read_only", "late_source", "foreign_linear",
    "foreign_symbol", "mixed_rest", "expired_publication",
])
def test_missing_or_mixed_input_fails_before_cutover(
    monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    mock_all(monkeypatch)
    db = ReadOnlyPG()
    at = NOW
    if change == "not_rr":
        db.isolation = "read committed"
    elif change == "not_read_only":
        db.read_only = "off"
    elif change == "late_source":
        at = AT + timedelta(seconds=31)
    elif change == "expired_publication":
        at = AT - timedelta(seconds=1)
    elif change == "foreign_linear":
        real = stage.read_r11_pinned_mayak_optional_page
        monkeypatch.setattr(
            stage, "read_r11_pinned_mayak_optional_page",
            lambda *args, **kwargs: (
                replace(real(*args, **kwargs), source_session_id="f" * 32)
                if kwargs["product_category"] == "LINEAR"
                else real(*args, **kwargs)
            ),
        )
    elif change == "foreign_symbol":
        real = stage.read_r11_original_linear_page
        monkeypatch.setattr(
            stage, "read_r11_original_linear_page",
            lambda *args, **kwargs: (
                replace(real(*args, **kwargs), producer_release_commit="f" * 40)
                if kwargs["symbol"] == "ETHUSDT"
                else real(*args, **kwargs)
            ),
        )
    elif change == "mixed_rest":
        real = stage.read_r11_mayak_rest_candidate
        monkeypatch.setattr(
            stage, "read_r11_mayak_rest_candidate",
            lambda *args, **kwargs: (
                replace(real(*args, **kwargs), publication_id=43)
                if kwargs["symbol"] == "BTCUSDT"
                else real(*args, **kwargs)
            ),
        )
    with pytest.raises(stage.R11SevenMayakCollectionUnverified):
        stage.collect_r11_seven_mayak_candidate(
            db, publication_id=42, observed_at=at,
        )


def test_offline_replay_preserves_seven_coin_mayak_dispatcher_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_all(monkeypatch)
    candidate = stage.collect_r11_seven_mayak_candidate(
        ReadOnlyPG(), publication_id=42, observed_at=NOW,
    )
    snapshot = stage.replay_r11_seven_candidate_offline(
        candidate, now=NOW,
    )
    assert set(snapshot["coins"]) == set(SEVEN)
    assert snapshot["btc"] == snapshot["coins"]["BTCUSDT"]
    assert snapshot["eth"] == snapshot["coins"]["ETHUSDT"]
    assert snapshot["money_breadth"]["spot_coverage"]["total"] == 7
    assert snapshot["money_breadth"]["derivatives_coverage"]["total"] == 7
    assert len(snapshot["dispatcher_handoff"]["instrument_lineage"]) == 7
    assert "btc.state" in snapshot["dispatcher_handoff"][
        "dispatcher_features"
    ]
    assert "eth.state" in snapshot["dispatcher_handoff"][
        "dispatcher_features"
    ]
    assert snapshot["dispatcher_handoff"]["provenance"][
        "config_fingerprint"
    ] == snapshot["config_fingerprint"]
    assert all(
        not row["connected"] for row in snapshot["transport"].values()
    )


@pytest.mark.parametrize("change", [
    "false_ready", "source_continuity", "missing_coin",
    "mixed_publication", "old_clock",
])
def test_offline_projection_rejects_forged_readiness_and_incomplete_panel(
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    mock_all(monkeypatch)
    candidate = stage.collect_r11_seven_mayak_candidate(
        ReadOnlyPG(), publication_id=42, observed_at=NOW,
    )
    at = NOW
    if change == "false_ready":
        candidate = replace(candidate, mayak_cutover_ready=True)
    elif change == "source_continuity":
        candidate = replace(candidate, provider_continuity_verified=True)
    elif change == "missing_coin":
        candidate = replace(candidate, rest_facts=candidate.rest_facts[:-1])
    elif change == "mixed_publication":
        candidate = replace(candidate, rest_facts=(
            replace(candidate.rest_facts[0], publication_id=43),
            *candidate.rest_facts[1:],
        ))
    elif change == "old_clock":
        at = NOW + timedelta(seconds=31)
    with pytest.raises(stage.R11SevenMayakCollectionUnverified):
        stage.replay_r11_seven_candidate_offline(candidate, now=at)


def test_checkpoint_continues_exact_source_positions_after_page_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second PG read starts at the *physically proven* previous cursor."""
    mock_all(monkeypatch)
    initial = stage.collect_r11_seven_mayak_candidate(
        ReadOnlyPG(), publication_id=42, observed_at=NOW,
    )
    checkpoint = initial.checkpoint()
    assert checkpoint.publication_id == 42
    assert not checkpoint.provider_continuity_verified
    assert not checkpoint.mayak_cutover_ready
    assert len(checkpoint.optional_cursors) == 2
    assert len(checkpoint.native_cursors) == 7

    resumed: list[str] = []

    def get_optional(_db: Any, *, publication_id: int,
                     product_category: str, previous: Any = None
                     ) -> R11PinnedMayakPublicationPage:
        assert publication_id == 42
        assert previous is not None and previous.next_cursor == 1
        resumed.append("optional:" + product_category)
        return optional(product_category)

    def get_native(_db: Any, *, symbol: str, observed_at: datetime,
                   read_after: tuple[int, int]) -> R11OriginalNativePage:
        assert observed_at == NOW and read_after == (1, 0)
        resumed.append("native:" + symbol)
        return native_page(symbol)

    monkeypatch.setattr(stage, "read_r11_pinned_mayak_optional_page",
                        get_optional)
    monkeypatch.setattr(stage, "read_r11_original_linear_page", get_native)
    next_batch = stage.collect_r11_seven_mayak_candidate(
        ReadOnlyPG(), publication_id=42, observed_at=NOW,
        previous=checkpoint,
    )
    assert not next_batch.has_more_source_pages
    assert resumed == (
        ["optional:SPOT", "optional:LINEAR"]
        + ["native:" + symbol for symbol in SEVEN]
    )
    assert next_batch.checkpoint() == checkpoint


@pytest.mark.parametrize("fault", [
    "false_ready", "missing_native", "mixed_epoch", "future_checkpoint",
])
def test_forged_checkpoint_rejected_before_original_page_reads(
    monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    seen = mock_all(monkeypatch)
    candidate = stage.collect_r11_seven_mayak_candidate(
        ReadOnlyPG(), publication_id=42, observed_at=NOW,
    )
    checkpoint = candidate.checkpoint()
    before = len(seen)
    if fault == "false_ready":
        checkpoint = replace(checkpoint, mayak_cutover_ready=True)
    elif fault == "missing_native":
        checkpoint = replace(
            checkpoint, native_cursors=checkpoint.native_cursors[:-1],
        )
    elif fault == "mixed_epoch":
        checkpoint = replace(
            checkpoint, optional_cursors=(
                replace(checkpoint.optional_cursors[0],
                        source_session_id="f" * 32),
                checkpoint.optional_cursors[1],
            ),
        )
    elif fault == "future_checkpoint":
        checkpoint = replace(
            checkpoint, as_of_second=NOW + timedelta(seconds=1),
        )
    with pytest.raises(stage.R11SevenMayakCollectionUnverified):
        stage.collect_r11_seven_mayak_candidate(
            ReadOnlyPG(), publication_id=42,
            observed_at=NOW, previous=checkpoint,
        )
    assert len(seen) == before
