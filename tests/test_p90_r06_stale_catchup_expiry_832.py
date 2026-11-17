from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_exit.execution_bridge import (
    ExitExecutionBridgeBlockCode,
    ExitExecutionBridgeBlocked,
)
from operations.connectivity import p90_r06_exit_owner as owner


def _cycle(monkeypatch: pytest.MonkeyPatch, *, exc: Exception):
    boundary = datetime(2026, 11, 17, 7, 15, tzinfo=UTC)
    position = SimpleNamespace(
        fill_at=datetime(2026, 11, 17, 6, 35, tzinfo=UTC),
        symbol="ARBUSDT",
        strategy_position_id="pos",
        strategy_id="p90l90_legacy_sub_dot_arb_apt",
        trading_account_id="ta-" + "8" * 32,
    )
    scope = SimpleNamespace(
        identity=SimpleNamespace(origin_commit="a" * 40),
        routes=SimpleNamespace(
            account_by_strategy={
                "p90l90_legacy_sub_dot_arb_apt": position.trading_account_id,
            }
        ),
    )
    snapshot = SimpleNamespace(
        closed_at=boundary,
        events=(),
        truncated=False,
    )
    markers: list[datetime] = []

    monkeypatch.setattr(owner, "_runtime_scope", lambda *_a, **_k: scope)
    monkeypatch.setattr(owner, "_open_positions", lambda *_a, **_k: (position,))
    monkeypatch.setattr(owner, "lock_p90_exit_account", lambda *_a, **_k: None)
    monkeypatch.setattr(owner, "_refresh_exit_claim", lambda *_a, **_k: None)
    monkeypatch.setattr(owner, "_latest_mfe", lambda *_a, **_k: object())
    monkeypatch.setattr(owner, "_last_closed_boundary", lambda *_a, **_k: boundary)
    monkeypatch.setattr(
        owner,
        "_next_closed_boundary",
        lambda *_a, **_k: boundary,
    )
    monkeypatch.setattr(owner, "_source_snapshot", lambda *_a, **_k: snapshot)
    monkeypatch.setattr(owner, "_before_boundary", lambda *_a, **_k: ((), True))
    monkeypatch.setattr(owner, "_append_mfe", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        owner,
        "_process_closed",
        lambda *_a, **_k: (_ for _ in ()).throw(exc),
    )
    monkeypatch.setattr(
        owner,
        "_record_stale_closed_boundary",
        lambda *_a, boundary, **_k: markers.append(boundary),
        raising=False,
    )
    return boundary, markers


def test_request_expired_catchup_advances_without_retroactive_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary, markers = _cycle(
        monkeypatch,
        exc=ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.REQUEST_EXPIRED,
            "ExitExecutionRequest expired before dispatch",
        ),
    )

    result = owner.run_p90_r06_exit_once(
        object(),
        object(),
        loaded_commit="b" * 40,
        now=datetime(2026, 11, 17, 12, 30, tzinfo=UTC),
    )

    assert markers == [boundary]
    assert result.endswith(
        "ARBUSDT:P90_R06_EXIT_CLOSED_STALE_DECISION_SKIPPED"
    )


def test_non_expiry_bridge_blocker_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _boundary, markers = _cycle(
        monkeypatch,
        exc=ExitExecutionBridgeBlocked(
            ExitExecutionBridgeBlockCode.IDENTITY_MISMATCH,
            "lineage mismatch",
        ),
    )

    with pytest.raises(ExitExecutionBridgeBlocked) as caught:
        owner.run_p90_r06_exit_once(
            object(),
            object(),
            loaded_commit="b" * 40,
            now=datetime(2026, 11, 17, 12, 30, tzinfo=UTC),
        )

    assert caught.value.code is ExitExecutionBridgeBlockCode.IDENTITY_MISMATCH
    assert markers == []


def test_closed_boundary_index_includes_stale_catchup_marker() -> None:
    source = Path(
        "operations/connectivity/p90_r06_exit_owner.py"
    ).read_text(encoding="utf-8")
    assert "_STALE_CLOSED_KIND" in source
    assert "_STALE_CLOSED_KIND" in source.split(
        "def _last_closed_boundary", 1
    )[1].split("def _source_snapshot", 1)[0]
