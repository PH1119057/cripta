from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 11, 17, 6, 35, 58, tzinfo=UTC)


class SourceConnection:
    def transaction(self):
        return nullcontext()

    def execute(self, sql, params=()):
        if sql.startswith("SET TRANSACTION"):
            return SimpleNamespace()
        raise AssertionError(sql)


def test_firstfill_keeps_single_market_source_broker_and_15s_contract() -> None:
    firstfill = (
        ROOT / "operations/connectivity/p90_real_independent_exit.py"
    ).read_text(encoding="utf-8")
    native = (
        ROOT / "operations/monitoring/native_public_trade_source.py"
    ).read_text(encoding="utf-8")
    union = (
        ROOT / "operations/monitoring/public_market_source_union_capture.py"
    ).read_text(encoding="utf-8")
    assert "read_real_postfill_source_window(" in firstfill
    assert "CRIPTA_R09_SOURCE_POSTFILL_BROKER" in native
    assert "_start_r09_postfill_broker(r09_mirror)" in union
    assert "_record_r09_postfill_message(" in union
    assert "_FIRST_FILL_WINDOW = timedelta(seconds=15)" in firstfill


def test_firstfill_block_is_durable_but_continuous_exit_still_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from operations.connectivity import p90_r06_exit_owner as continuous
    from operations.connectivity import p90_real_independent_exit as firstfill
    from operations.connectivity import universal_exit_consumer as consumer

    monkeypatch.setattr(consumer, "P90_R06_OWNER", "ENABLED")
    monkeypatch.setattr(consumer, "LOADED_COMMIT", "a" * 40)
    calls: list[str] = []

    def first(db, **kwargs):
        assert db is connection
        assert kwargs["now"] == AT
        calls.append("firstfill")
        return "P90_REAL_EXIT_FIRST_FILL_ORIGINAL_SOURCE_WINDOW_EXPIRED"

    monkeypatch.setattr(firstfill, "run_p90_exit_once", first)
    monkeypatch.setattr(
        firstfill,
        "audit_firstfill_exit_result",
        lambda *_a, **_k: calls.append("audit"),
    )
    monkeypatch.setattr(
        continuous,
        "run_p90_r06_exit_once",
        lambda *_a, **_k: calls.append("continuous")
        or "P90_R06_EXIT_OWNER_RUNNING|ARBUSDT:P90_R06_EXIT_MFE_AWAITING_CLOSED:ARBUSDT:0",
    )

    source = object()
    connection = SimpleNamespace(
        commit=lambda: calls.append("commit"),
    )
    result = consumer.run_once(
        connection,
        source_connection=source,
        run_generic=False,
        now=AT,
    )
    assert result.startswith("P90_R06_FIRSTFILL_OWNER_BLOCKED|")
    assert "CONTINUOUS=P90_R06_EXIT_OWNER_RUNNING|" in result
    assert calls == ["firstfill", "audit", "commit", "continuous"]


def test_historical_verified_seed_selects_exact_missed_boundary() -> None:
    from operations.connectivity import p90_r06_exit_owner as owner

    start = datetime(2026, 11, 17, 3, 45, tzinfo=UTC)
    candles = tuple(
        SimpleNamespace(closed_at=start + timedelta(minutes=5 * i))
        for i in range(40)
    )
    boundary = candles[37].closed_at
    history = owner._closed_history_for_boundary(candles, boundary)
    assert len(history) == 36
    assert history[-1] is candles[37]
    assert history[0] is candles[2]
    with pytest.raises(owner.P90R06ExitBlocked, match="CLOSED_BOUNDARY_GAP"):
        owner._closed_history_for_boundary(
            candles, start - timedelta(minutes=5)
        )


def test_missed_firstfill_status_is_committed_as_blocked_not_rolled_back() -> None:
    code = (
        ROOT / "operations/connectivity/universal_exit_consumer.py"
    ).read_text(encoding="utf-8")
    assert 'result.startswith("P90_R06_FIRSTFILL_OWNER_BLOCKED|")' in code
    commit_pos = code.index("connection.commit()")
    blocked_pos = code.index('state="P90_R06_EXIT_OWNER_BLOCKED"')
    assert commit_pos < blocked_pos


def test_firstfill_window_stays_15_seconds_and_late_arm_remains_forbidden() -> None:
    source = (
        ROOT / "operations/connectivity/p90_real_independent_exit.py"
    ).read_text(encoding="utf-8")
    assert "_FIRST_FILL_WINDOW = timedelta(seconds=15)" in source
    assert "FIRST_FILL_ORIGINAL_SOURCE_WINDOW_EXPIRED" in source


def test_historical_closed_catchup_uses_fresh_current_private_proof() -> None:
    source = (
        ROOT / "operations/connectivity/p90_r06_exit_owner.py"
    ).read_text(encoding="utf-8")
    assert "private.for_closed_boundary(subject, datetime.now(UTC))" in source
    assert "self._private.for_closed_boundary(position, checked_at)" in source

    ipc = (
        ROOT / "src/bybit_workbench/universal_exit/p90l90_real_private_ipc.py"
    ).read_text(encoding="utf-8")
    assert '"request_at"' not in ipc
    assert "P90_PRIVATE_IPC_EVENT_STALE" in ipc
