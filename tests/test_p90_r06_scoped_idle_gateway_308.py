"""Prevent unrelated native-trade idle windows from closing the entire P90 gate.

The financial source/Entry engine MUST still reject stale trading evidence.
Existing OPEN-position Exit path remains fully strict.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
    NativeSourceEvidence,
    NativeSourceSymbolEvidence,
)
from operations.connectivity import p90_r06_exit_owner as exit_owner
from operations.monitoring.r06_pg_only_entry import _published_r06_observer_state


STRATEGIES = ("p90_main", "p90_sub")
SESSION = "a" * 32
COMMIT = "b" * 40
AT = datetime(2026, 11, 18, 11, 35, tzinfo=UTC)
CURRENT = "NATIVE_SOURCE_COMMITTED_CURRENT"
STALE = "NATIVE_SOURCE_CURSOR_NOT_FRESH"


@pytest.mark.parametrize(
    ("blocked", "expected"),
    [
        ({}, "R06_PG_NO_ORDER_SOURCE_ONLY"),
        ({"p90_main": STALE}, "R06_PG_NO_ORDER_SOURCE_ONLY"),
        ({"p90_sub": "R06_SCOPE_AWAITING_CONFIRMED_CANDLE"},
         "R06_PG_NO_ORDER_SOURCE_ONLY"),
        ({"p90_main": STALE, "p90_sub": STALE}, "R06_PG_NO_ORDER_SOURCE_ONLY"),
        ({"p90_main": "NATIVE_SOURCE_SESSION_NOT_CURRENT"},
         "R06_PG_NO_ORDER_DEGRADED"),
        ({"foreign": STALE}, "R06_PG_NO_ORDER_DEGRADED"),
    ],
)
def test_r06_scoped_wait_never_disables_healthy_other_strategy(
    blocked: dict[str, str], expected: str,
) -> None:
    assert _published_r06_observer_state(blocked, STRATEGIES) == expected


def test_r06_unknown_or_duplicate_scope_fails_closed() -> None:
    assert _published_r06_observer_state({}, ()) == "R06_PG_NO_ORDER_DEGRADED"
    assert _published_r06_observer_state(
        {}, ("p90_main", "p90_main")
    ) == "R06_PG_NO_ORDER_DEGRADED"


def _source(
    *,
    stale_symbols: tuple[str, ...] = (),
    invalid_reason: str | None = None,
    missing: str | None = None,
    market: str = BYBIT_PUBLIC_MARKET_SOURCE_ID,
    session: str | None = SESSION,
) -> NativeSourceEvidence:
    by_symbol = {
        symbol: NativeSourceSymbolEvidence(
            symbol=symbol, ready=symbol not in stale_symbols,
            reason=(
                invalid_reason or STALE
                if symbol in stale_symbols else CURRENT
            ),
        )
        for symbol in exit_owner._SYMBOLS if symbol != missing
    }
    return NativeSourceEvidence(
        market_source_id=market,
        session_id=session,
        producer_release_commit=COMMIT,
        checked_at=AT,
        by_symbol=by_symbol,
    )


def test_flat_exit_can_serve_healthy_symbols_during_one_idle_cursor() -> None:
    assert exit_owner._flat_scoped_source_acceptable(
        _source(stale_symbols=("APTUSDT",))
    )
    assert exit_owner._flat_scoped_source_acceptable(_source())


@pytest.mark.parametrize(
    "evidence",
    [
        _source(stale_symbols=exit_owner._SYMBOLS),
        _source(stale_symbols=("APTUSDT",), invalid_reason="NATIVE_SOURCE_CURSOR_MISSING"),
        _source(stale_symbols=("APTUSDT",), invalid_reason="NATIVE_SOURCE_JOURNAL_IDENTITY_DRIFT"),
        _source(missing="LTCUSDT"),
        _source(market="wrong-source"),
        _source(session=None),
    ],
)
def test_flat_exit_remains_fail_closed_for_shared_or_unproved_source(
    evidence: NativeSourceEvidence,
) -> None:
    assert not exit_owner._flat_scoped_source_acceptable(evidence)


class _Connection:
    def transaction(self):
        return nullcontext()

    def execute(self, sql: str):
        assert sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        return None


def test_flat_exit_rechecks_one_fresh_immutable_publication() -> None:
    partial = _source(stale_symbols=("APTUSDT",))
    with (
        patch.object(exit_owner, "_exit_native_ttls",
                     return_value={symbol: timedelta(seconds=90)
                                   for symbol in exit_owner._SYMBOLS}),
        patch.object(exit_owner, "read_scoped_native_source", return_value=partial),
        patch.object(
            exit_owner, "read_entry_publication_pin",
            return_value=SimpleNamespace(
                source_session_id=SESSION,
                producer_release_commit=COMMIT,
            ),
        ) as pin,
    ):
        exit_owner._flat_source_ready(
            _Connection(),
            scope=SimpleNamespace(
                identity=SimpleNamespace(approved_producers=frozenset((COMMIT,))),
            ),
            now=AT,
        )
        assert pin.call_count == 1


def test_flat_exit_rejects_publication_epoch_drift_even_if_one_symbol_fresh() -> None:
    partial = _source(stale_symbols=("APTUSDT",))
    with (
        patch.object(exit_owner, "_exit_native_ttls",
                     return_value={symbol: timedelta(seconds=90)
                                   for symbol in exit_owner._SYMBOLS}),
        patch.object(exit_owner, "read_scoped_native_source", return_value=partial),
        patch.object(
            exit_owner, "read_entry_publication_pin",
            return_value=SimpleNamespace(
                source_session_id="c" * 32,
                producer_release_commit=COMMIT,
            ),
        ),
        pytest.raises(
            exit_owner.P90R06ExitBlocked,
            match="P90_R06_EXIT_PUBLICATION_SOURCE_DRIFT",
        ),
    ):
        exit_owner._flat_source_ready(
            _Connection(),
            scope=SimpleNamespace(
                identity=SimpleNamespace(approved_producers=frozenset((COMMIT,))),
            ),
            now=AT,
        )


def test_live_position_exit_freshness_was_not_weakened() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "operations/connectivity/p90_r06_exit_owner.py"
    ).read_text(encoding="utf-8")
    snapshot = source.split("def _source_snapshot(", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert "source.ready" in snapshot
    assert '"P90_R06_EXIT_NATIVE_SOURCE_NOT_READY"' in snapshot
    assert "read_entry_publication_pin(" in snapshot


def test_r06_stale_scopes_do_not_emit_candidate_trades() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "operations/monitoring/r06_pg_only_entry.py"
    ).read_text(encoding="utf-8")
    processing = source.split("def _process_publication(", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert 'blocked[name] = "NATIVE_SOURCE_CURSOR_NOT_FRESH"\n' in processing
    assert "continue" in processing
    assert "read_scoped_native_source(" in processing
