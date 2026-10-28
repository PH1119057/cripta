"""Test the actual shared-PostgreSQL mixed P90+ordinary source boundary."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.p90l90_mixed_epoch_atomicity import (
    dispatch_observer_fact_atomically,
)


class FakeConnection:
    def __init__(self) -> None:
        self.transactions = 0

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield


def test_ordinary_only_epoch_preserves_original_no_extra_transaction():
    db = FakeConnection()
    effects = []
    assert dispatch_observer_fact_atomically(
        db, symbol="BTCUSDT", p90_symbols=("LTCUSDT",),
        p90_enabled=True, dispatch=lambda: effects.append("r1"),
    ) is None
    assert db.transactions == 0
    assert effects == ["r1"]
    assert dispatch_observer_fact_atomically(
        db, symbol="LTCUSDT", p90_symbols=("LTCUSDT",),
        p90_enabled=False, dispatch=lambda: effects.append("legacy"),
    ) is None
    assert db.transactions == 0
    assert effects == ["r1", "legacy"]


def test_real_observer_dispatches_inside_exact_mixed_atomic_boundary():
    source = (
        Path(__file__).resolve().parents[2]
        / "operations/monitoring/universal_entry_shadow.py"
    ).read_text(encoding="utf-8")
    epoch = source.split("def _run_observer_epoch(", 1)[1].split(
        "def _run_multi_strategy_observer(", 1
    )[0]
    before = epoch.index("def _process_fact_body(")
    wrapper = epoch.index("def process_fact(", before)
    assert wrapper > before
    tail = epoch[wrapper:epoch.index("    try:\n        trade_mirror_thread.start()",wrapper)]
    assert "dispatch_observer_fact_atomically(" in tail
    assert "p90_enabled=p90_consumer is not None" in tail
    assert "dispatch=lambda: _process_fact_body(fact, cursor)" in tail
    assert "paper.route_p90l90_native_trade(" not in epoch
    assert "paper.create_p90l90_native_paper_entry(" not in epoch


@pytest.mark.skipif(
    not os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN"),
    reason="requires disposable PG16 only",
)
def test_mixed_real_pg_rolls_back_ordinary_paper_and_p90_checkpoint_together():
    import psycopg

    dsn = os.environ["CRIPTA_TRADE_LIFECYCLE_TEST_DSN"]
    with (
        psycopg.connect(dsn) as db,
        pytest.raises(RuntimeError, match="ROLLBACK_TEST_SANDBOX"),
        db.transaction(),
    ):
        db.execute("CREATE TEMP TABLE p90_mixed_atomic_effects(actor text) ON COMMIT DROP")

        def failing_dispatch():
            with db.transaction():
                db.execute(
                    "INSERT INTO pg_temp.p90_mixed_atomic_effects VALUES ('ordinary')"
                )
            with db.transaction():
                db.execute(
                    "INSERT INTO pg_temp.p90_mixed_atomic_effects VALUES ('p90_entry')"
                )
                db.execute(
                    "INSERT INTO pg_temp.p90_mixed_atomic_effects VALUES ('p90_exit')"
                )
            raise RuntimeError("INJECTED_P90_FAILURE_AFTER_ORDINARY_COMMIT")

        with pytest.raises(RuntimeError, match="INJECTED_P90_FAILURE"):
            dispatch_observer_fact_atomically(
                db, symbol="LTCUSDT", p90_symbols={"LTCUSDT", "INJUSDT"},
                p90_enabled=True, dispatch=failing_dispatch,
            )
        assert db.execute(
            "SELECT count(*) FROM pg_temp.p90_mixed_atomic_effects"
        ).fetchone() == (0,)

        def healthy_dispatch():
            with db.transaction():
                db.execute(
                    "INSERT INTO pg_temp.p90_mixed_atomic_effects VALUES ('ordinary')"
                )
            with db.transaction():
                db.execute(
                    "INSERT INTO pg_temp.p90_mixed_atomic_effects VALUES ('p90_entry')"
                )
            return "ACK"

        assert dispatch_observer_fact_atomically(
            db, symbol="LTCUSDT", p90_symbols={"LTCUSDT"},
            p90_enabled=True, dispatch=healthy_dispatch,
        ) == "ACK"
        assert db.execute(
            "SELECT actor FROM pg_temp.p90_mixed_atomic_effects ORDER BY ctid"
        ).fetchall() == [("ordinary",), ("p90_entry",)]
        raise RuntimeError("ROLLBACK_TEST_SANDBOX")
