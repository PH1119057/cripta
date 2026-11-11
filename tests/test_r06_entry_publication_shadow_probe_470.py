"""R06 no-trade shadow probe: exact P90 plans and pinned source history."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from operations.monitoring import r06_entry_publication_shadow_probe as probe

AT = datetime(2026, 11, 11, 18, 40, tzinfo=UTC)
SYMS = probe.FIVE


class Cursor:
    def __init__(self, rows: list[tuple[object, ...]]):
        self.rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class DB:
    def __init__(self):
        self.rows = [
            (
                "p90l90_legacy_main_ltc_inj",
                {
                    "symbols": ["INJUSDT", "LTCUSDT"],
                    "watch_policy": {"geometry": {
                        "operator": "P90L90_RAW_H9_UNION_CROSSING",
                        "timeframes": ["5", "15"],
                        "width_filter": {"source": "PREV7D_COMPLETED_UTC"},
                    }},
                },
            ),
            (
                "p90l90_legacy_sub_dot_arb_apt",
                {
                    "symbols": ["APTUSDT", "ARBUSDT", "DOTUSDT"],
                    "watch_policy": {"geometry": {
                        "operator": "P90L90_RAW_H9_UNION_CROSSING",
                        "timeframes": ["5", "15"],
                        "width_filter": {"source": "PREV7D_COMPLETED_UTC"},
                    }},
                },
            ),
        ]
        self.commit = "b" * 40

    def execute(self, sql: str, params: object = ()) -> Cursor:
        assert sql.lstrip().upper().startswith("SELECT")
        if "FROM strategy_entry.strategy_activations" in sql:
            assert set(params[0]) == probe.P90_PLAN_IDS
            return Cursor(self.rows)
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            return Cursor([(self.commit,)])
        pytest.fail("unexpected query")


def test_probe_reads_exact_plan_source_and_cold_history_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = DB()
    calls: list[str] = []
    pin = SimpleNamespace(
        publication_id=12, source_session_id="a" * 32,
        producer_release_commit=db.commit, as_of_second=AT,
    )

    def compatible(**kwargs: object) -> frozenset[str]:
        assert kwargs["claimed_producer_commit"] == db.commit
        assert kwargs["loaded_consumer_commit"] == "c" * 40
        calls.append("ABI")
        return frozenset((db.commit,))

    def pinned(_conn: object, **kwargs: object) -> object:
        assert _conn is db
        assert kwargs["symbols"] == SYMS
        assert kwargs["intervals"] == ("5", "15")
        assert kwargs["accepted_producer_commits"] == frozenset((db.commit,))
        calls.append("PIN")
        return pin

    def seed(_conn: object, **kwargs: object) -> dict[str, tuple[int, ...]]:
        assert _conn is db
        assert kwargs["pin"] is pin
        assert kwargs["required_counts"] == {"5": 2600, "15": 1000}
        calls.append(kwargs["symbol"])
        return {"5": (1,) * 2600, "15": (1,) * 1000}

    monkeypatch.setattr(probe, "verify_native_producer_code_compatibility", compatible)
    monkeypatch.setattr(probe, "read_entry_publication_pin", pinned)
    monkeypatch.setattr(probe, "read_entry_publication_seed", seed)
    result = probe.verify_shadow_read(
        db, observed_at=AT, source_checkout=Path("/source"),
        loaded_commit="c" * 40,
    )
    assert result["result"] == "R06_SHADOW_SOURCE_AND_COLD_SEED_PASS"
    assert result["publication_id"] == 12
    assert set(result["history_counts"]) == set(SYMS)
    assert result["trading_effect"] == "NONE"
    assert result["strategy_signals_compared"] is False
    assert result["entry_dispatch_switched"] is False
    assert calls == ["ABI", "PIN", *SYMS]


def test_plan_scope_drift_blocks_shadow_without_alt_rest() -> None:
    db = DB()
    db.rows[0][1]["watch_policy"]["geometry"]["timeframes"] = ["1", "5"]
    with pytest.raises(
        probe.R06ShadowUnverified, match="GEOMETRY_CONTRACT_DRIFT",
    ):
        probe.verify_current_entry_plan_scope(db)
    db = DB()
    db.rows.pop()
    with pytest.raises(
        probe.R06ShadowUnverified, match="ENTRY_PLAN_SET_CHANGED",
    ):
        probe.verify_current_entry_plan_scope(db)


def test_producer_abi_failure_does_not_fake_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
        NativeSourceProofUnavailable,
    )

    def fail(**_kw: object) -> object:
        raise NativeSourceProofUnavailable("WRONG_PRODUCER_ABI")

    monkeypatch.setattr(probe, "verify_native_producer_code_compatibility", fail)
    with pytest.raises(
        probe.R06ShadowUnverified, match="PINNED_SOURCE_OR_SEED_UNVERIFIED",
    ):
        probe.verify_shadow_read(
            DB(), observed_at=AT, source_checkout=Path("/source"),
            loaded_commit="c" * 40,
        )


def test_unverified_history_fails_without_trading_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = DB()
    monkeypatch.setattr(
        probe, "verify_native_producer_code_compatibility",
        lambda **_kw: frozenset((db.commit,)),
    )
    monkeypatch.setattr(
        probe, "read_entry_publication_pin",
        lambda *_a, **_kw: SimpleNamespace(
            publication_id=12, source_session_id="a" * 32,
            producer_release_commit=db.commit, as_of_second=AT,
        ),
    )

    def missing(_conn: object, **_kw: object) -> object:
        raise probe.EntryPublicationUnavailable("R06_ENTRY_HISTORY_UNVERIFIED")

    monkeypatch.setattr(probe, "read_entry_publication_seed", missing)
    with pytest.raises(
        probe.R06ShadowUnverified, match="PINNED_SOURCE_OR_SEED_UNVERIFIED",
    ):
        probe.verify_shadow_read(
            db, observed_at=AT, source_checkout=Path("/source"),
            loaded_commit="c" * 40,
        )
