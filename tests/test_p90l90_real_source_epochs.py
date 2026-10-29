"""REAL v3 source epochs must never relabel/overwrite the old PAPER seed.

These are no-order, no-production checks of the additive epoch table and
separate exact native checkpoint store. A genuine WS producer is still
required before creating or using a REAL epoch.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bybit_workbench.universal_entry.p90l90_atomic_real_epoch_checkpoint import (
    P90L90AtomicRealEpochCheckpointStore,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "operations/sql/20261029_p90l90_real_source_epochs.sql"
RELEASE = ROOT / "operations/infrastructure/install_verified_release.sh"
SOURCE = (
    ROOT / "src/bybit_workbench/universal_entry/"
    "p90l90_atomic_real_epoch_checkpoint.py"
)
EPOCH = "p90-real-epoch-" + "e" * 40
COMMIT = "a" * 40


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _DB:
    def __init__(self, *, state: str = "ACTIVE", commit: str = COMMIT):
        self.state = state
        self.commit = commit
        self.sql = []

    def execute(self, sql, args):
        self.sql.append((sql, args))
        if "p90l90_real_source_epochs" not in sql:
            raise AssertionError("unexpected table or SQL")
        return _Result((self.commit, self.state))


def test_read_existing_only_exact_active_real_epoch() -> None:
    db = _DB()
    store = P90L90AtomicRealEpochCheckpointStore(
        db, source_commit=COMMIT, epoch_id=EPOCH,
    )
    store._require_active_epoch()
    assert len(db.sql) == 1
    assert db.sql[0][1] == (EPOCH,)
    assert "p90l90_causal_transport_checkpoints" not in db.sql[0][0]


@pytest.mark.parametrize("status,commit", [
    ("RETIRED", COMMIT),
    ("ACTIVE", "b" * 40),
    ("INACTIVE", COMMIT),
])
def test_stale_or_foreign_real_epoch_is_not_tradable(
    status: str, commit: str,
) -> None:
    db = _DB(state=status, commit=commit)
    store = P90L90AtomicRealEpochCheckpointStore(
        db, source_commit=COMMIT, epoch_id=EPOCH,
    )
    with pytest.raises(RuntimeError, match="SOURCE_EPOCH_NOT_CURRENT"):
        store._require_active_epoch()


@pytest.mark.parametrize("epoch", [
    "not-an-epoch", "p90-real-epoch-" + "x" * 40,
    "p90-real-epoch-" + "a" * 39,
])
def test_epoch_identity_is_not_inferred_from_source_commit(epoch: str) -> None:
    with pytest.raises(ValueError, match="EXACT_SOURCE_EPOCH_ID_REQUIRED"):
        P90L90AtomicRealEpochCheckpointStore(
            _DB(), source_commit=COMMIT, epoch_id=epoch,
        )


def test_independent_real_epoch_is_additive_and_release_managed() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    release = RELEASE.read_text(encoding="utf-8")
    writer = SOURCE.read_text(encoding="utf-8")
    assert "p90l90_real_source_epochs" in sql
    assert "p90l90_real_causal_checkpoints" in sql
    assert "UNIQUE INDEX IF NOT EXISTS p90_real_at_most_one_active_epoch" in sql
    assert "status='RETIRED'" in sql
    assert "PRIMARY KEY (epoch_id,symbol)" in sql
    assert "p90l90_real_restart_bridges" in sql
    assert "p90-real-restart-[0-9a-f]{40}$" in sql
    assert "40}REVOKE" not in sql
    assert sql.count("COMMIT;") == 1
    assert "ON CONFLICT (epoch_id,symbol) DO NOTHING" in writer
    assert "WHERE epoch_id=%s AND symbol=%s" in writer
    assert "P90_REAL_SOURCE_EPOCH_NOT_CURRENT" in writer
    assert "UPDATE strategy_entry.p90l90_real_causal_checkpoints" in writer
    assert "writer_release_commit=%s" in writer
    for prohibited in (
        "UPDATE strategy_entry.p90l90_causal_transport_checkpoints",
        "DELETE FROM strategy_entry.p90l90_causal_transport_checkpoints",
        "TRUNCATE strategy_entry.p90l90_causal_closed_bars",
        "ALTER TABLE strategy_entry.p90l90_causal_transport_checkpoints",
        "DROP TABLE",
    ):
        assert prohibited not in sql
        assert prohibited not in writer
    assert "operations/sql/20261029_p90l90_real_source_epochs.sql" in release
    assert release.index("20261028_shared_closed_candle_source.sql") < (
        release.index("20261029_p90l90_real_source_epochs.sql")
    )
    consumer = (
        ROOT / "src/bybit_workbench/universal_entry/"
        "p90l90_observer_real_consumer.py"
    ).read_text(encoding="utf-8")
    assert "P90RealSharedCandleLedger(" in consumer
    assert "P90L90AtomicRealEpochCheckpointStore(" in consumer
    assert "source_epoch_id=source_epoch_id" not in consumer
    assert "epoch_id=source_epoch_id" in consumer
    assert "P90SharedCandleLedger(" not in consumer
    assert "P90L90AtomicPaperCheckpointStore(" not in consumer

