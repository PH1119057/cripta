from __future__ import annotations

import copy
import json
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bybit_workbench.mayak.native_trade_replay_parity import R11ReplayCursor
from bybit_workbench.mayak.optional_original_event_parity import (
    R11OptionalMayakCursor,
)
from bybit_workbench.mayak.publication_consumer_commit import (
    R11ConsumerCommitUnverified,
    commit_r11_mayak_publication,
    load_r11_mayak_consumer_commit,
    verify_r11_mayak_consumer_reset_lineage,
)
from bybit_workbench.mayak.seven_publication_observation_candidate import (
    R11SevenMayakReplayCheckpoint,
)
from bybit_workbench.mayak.seven_publication_replay_runtime import (
    R11MayakPublicationReplay,
)

AT = datetime(2026, 11, 15, 20, 0, tzinfo=UTC)
SESSION = "a" * 32
RELEASE = "b" * 40
SEVEN = (
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
)


def replay_state(publication_id: int = 100) -> R11MayakPublicationReplay:
    at = AT + timedelta(seconds=publication_id - 100)
    checkpoint = R11SevenMayakReplayCheckpoint(
        publication_id=publication_id,
        source_session_id=SESSION, release_commit=RELEASE,
        as_of_second=at,
        optional_cursors=tuple(
            R11OptionalMayakCursor(
                source_session_id=SESSION,
                transport_epoch_id=(
                    "c" * 32 if product == "SPOT" else SESSION
                ),
                product_category=product,
                producer_release_commit=RELEASE,
                next_cursor=publication_id, next_digest="d" * 64,
                books=(),
            )
            for product in ("SPOT", "LINEAR")
        ),
        native_cursors=tuple(
            R11ReplayCursor(
                symbol=symbol, source_session_id=SESSION,
                producer_release_commit=RELEASE,
                publication_id=publication_id,
                publication_as_of=at, publication_watermark=(1, 0),
                cursor=(1, 0), last_seq=1,
                last_event_at=at - timedelta(seconds=1),
                recent_exec_ids=(),
            )
            for symbol in SEVEN
        ),
    )
    replay = R11MayakPublicationReplay(
        checkpoint=checkpoint,
        first_publication_at=AT - timedelta(minutes=1),
        last_original_event_at=at.timestamp() - 1,
        observations=publication_id - 99,
    )
    replay.engine.set_instrument_support("spot", set(SEVEN))
    replay.engine.set_instrument_support("linear", set(SEVEN))
    return replay


def snapshot(replay: R11MayakPublicationReplay) -> dict[str, Any]:
    assert replay.checkpoint is not None
    value = replay.engine.snapshot(replay.checkpoint.as_of_second)
    value["r11_market_publication"] = {
        "publication_id": replay.checkpoint.publication_id,
        "source_session_id": replay.checkpoint.source_session_id,
        "producer_release_commit": replay.checkpoint.release_commit,
        "as_of_second": replay.checkpoint.as_of_second.isoformat(),
        "consumer_ready": False,
        "mayak_ws_retirement_allowed": False,
    }
    value["dispatcher_handoff"]["provenance"].update({
        "r11_market_publication_id": replay.checkpoint.publication_id,
        "r11_source_session_id": replay.checkpoint.source_session_id,
    })
    return value


class Rows:
    def __init__(
        self, row: tuple[Any, ...] | None = None,
        rows: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.row = row
        self.rows = [] if rows is None else rows

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class Transaction(AbstractContextManager[None]):
    def __init__(self, db: AtomicDB) -> None:
        self.db = db

    def __enter__(self) -> None:
        self.db.staging = copy.deepcopy(self.db.committed)

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is None:
            self.db.committed = self.db.staging
        self.db.staging = None
        return False


class AtomicDB:
    def __init__(self) -> None:
        self.committed: tuple[Any, ...] | None = None
        self.staging: tuple[Any, ...] | None = None
        self.guard_valid = True
        self.shared_lock = True
        self.active_source = (SESSION, RELEASE)
        self.publication_override: tuple[Any, ...] | None = None

    def transaction(self) -> Transaction:
        return Transaction(self)

    def execute(
        self, sql: str, params: tuple[Any, ...] = (),
    ) -> Rows:
        if sql.startswith("LOCK TABLE"):
            return Rows()
        if "SELECT %s::jsonb::text" in sql:
            value = params[0]
            parsed = json.loads(value) if isinstance(value, str) else value.obj
            return Rows((json.dumps(
                parsed, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), default=str, allow_nan=False,
            ),))
        if "FROM pg_catalog.pg_trigger" in sql:
            return Rows((self.guard_valid,))
        if "pg_try_advisory_xact_lock_shared" in sql:
            return Rows((self.shared_lock,))
        if "FROM analytics.native_public_trade_source_sessions" in sql:
            return Rows(rows=[self.active_source])
        if "FROM analytics.market_publications" in sql:
            if self.publication_override is not None:
                return Rows(self.publication_override)
            publication_id = int(params[0])
            at = AT + timedelta(seconds=publication_id - 100)
            return Rows((SESSION, RELEASE, at, "WATERMARKS_ONLY"))
        if "FOR UPDATE" in sql:
            row = self.staging
            return Rows(
                None if row is None else (row[0], row[4], row[2], row[3])
            )
        if sql.lstrip().startswith("INSERT"):
            jsonb = params[10]
            self.staging = (
                params[1], params[2], params[3], params[4], params[5],
                params[6], params[7], params[8], params[9], jsonb.obj,
                params[11], AT,
            )
            return Rows((params[1],))
        if sql.lstrip().startswith("SELECT generation,market_source_id"):
            if self.committed is None:
                return Rows(None)
            if "snapshot::text" in sql:
                snapshot_text = json.dumps(
                    self.committed[9], ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), default=str, allow_nan=False,
                )
                return Rows((*self.committed, snapshot_text))
            if "state_checkpoint" in sql:
                return Rows(self.committed[:9])
            return Rows(self.committed[:5])
        raise AssertionError(sql)


@pytest.mark.parametrize(
    "crash_point", (
        "AFTER_ENCODE", "AFTER_SOURCE_FENCE", "AFTER_LOCK", "AFTER_WRITE",
    ),
)
def test_every_precommit_crash_leaves_no_partial_generation(
    crash_point: str,
) -> None:
    db = AtomicDB()
    replay = replay_state()

    def crash(at: str) -> None:
        if at == crash_point:
            raise RuntimeError(at)

    with pytest.raises(RuntimeError, match=crash_point):
        commit_r11_mayak_publication(
            db, replay=replay, snapshot=snapshot(replay),
            expected_generation=0, expected_previous_publication_id=None,
            fault_injector=crash,
        )

    assert db.committed is None
    assert load_r11_mayak_consumer_commit(
        db, expected_source_session_id=SESSION,
        expected_release_commit=RELEASE,
    ) is None


def test_commit_and_restore_are_one_exact_generation() -> None:
    db = AtomicDB()
    replay = replay_state()
    committed = commit_r11_mayak_publication(
        db, replay=replay, snapshot=snapshot(replay),
        expected_generation=0, expected_previous_publication_id=None,
    )

    restored = load_r11_mayak_consumer_commit(
        db, expected_source_session_id=SESSION,
        expected_release_commit=RELEASE,
    )

    assert committed.generation == 1
    assert restored is not None
    assert restored.generation == 1
    assert restored.replay.checkpoint == replay.checkpoint
    assert restored.snapshot == committed.snapshot
    assert restored.state_sha256 == committed.state_sha256
    assert restored.snapshot_sha256 == committed.snapshot_sha256
    assert all(
        not row["connected"]
        for row in restored.replay.engine.transport.values()
    )


def test_crash_after_commit_restores_new_whole_generation() -> None:
    db = AtomicDB()
    first = replay_state(100)
    commit_r11_mayak_publication(
        db, replay=first, snapshot=snapshot(first),
        expected_generation=0, expected_previous_publication_id=None,
    )
    second = replay_state(101)

    def crash(at: str) -> None:
        if at == "AFTER_COMMIT":
            raise RuntimeError(at)

    with pytest.raises(RuntimeError, match="AFTER_COMMIT"):
        commit_r11_mayak_publication(
            db, replay=second, snapshot=snapshot(second),
            expected_generation=1, expected_previous_publication_id=100,
            fault_injector=crash,
        )

    restored = load_r11_mayak_consumer_commit(
        db, expected_source_session_id=SESSION,
        expected_release_commit=RELEASE,
    )
    assert restored is not None
    assert restored.generation == 2
    assert restored.replay.checkpoint is not None
    assert restored.replay.checkpoint.publication_id == 101
    assert restored.snapshot["r11_market_publication"]["publication_id"] == 101


def test_cas_regression_and_mixed_snapshot_fail_before_write() -> None:
    db = AtomicDB()
    first = replay_state(100)
    first_snapshot = snapshot(first)
    commit_r11_mayak_publication(
        db, replay=first, snapshot=first_snapshot,
        expected_generation=0, expected_previous_publication_id=None,
    )
    before = copy.deepcopy(db.committed)
    second = replay_state(101)

    with pytest.raises(R11ConsumerCommitUnverified, match="CAS_OR"):
        commit_r11_mayak_publication(
            db, replay=second, snapshot=snapshot(second),
            expected_generation=7, expected_previous_publication_id=100,
        )
    mixed = snapshot(second)
    mixed["dispatcher_handoff"]["provenance"][
        "r11_market_publication_id"
    ] = 100
    with pytest.raises(R11ConsumerCommitUnverified, match="INPUT_GENERATION"):
        commit_r11_mayak_publication(
            db, replay=second, snapshot=mixed,
            expected_generation=1, expected_previous_publication_id=100,
        )
    assert db.committed == before


@pytest.mark.parametrize("index", (8, 9))
def test_corrupt_state_or_snapshot_digest_fails_closed(index: int) -> None:
    db = AtomicDB()
    replay = replay_state()
    commit_r11_mayak_publication(
        db, replay=replay, snapshot=snapshot(replay),
        expected_generation=0, expected_previous_publication_id=None,
    )
    assert db.committed is not None
    row = list(db.committed)
    if index == 8:
        row[index] = "0" * 64
    else:
        row[index] = {**row[index], "observed_at": "forged"}
    db.committed = tuple(row)

    with pytest.raises(R11ConsumerCommitUnverified):
        load_r11_mayak_consumer_commit(
            db, expected_source_session_id=SESSION,
            expected_release_commit=RELEASE,
        )



@pytest.mark.parametrize(
    ("failure", "reason"),
    (
        ("guard", "SOURCE_EPOCH_GUARD"),
        ("lock", "SOURCE_EPOCH_LOCK"),
        ("source", "SOURCE_EPOCH_DRIFT"),
        ("publication", "PHYSICAL_PUBLICATION_DRIFT"),
    ),
)
def test_source_epoch_or_publication_drift_blocks_before_consumer_write(
    failure: str, reason: str,
) -> None:
    db = AtomicDB()
    replay = replay_state()
    if failure == "guard":
        db.guard_valid = False
    elif failure == "lock":
        db.shared_lock = False
    elif failure == "source":
        db.active_source = ("f" * 32, RELEASE)
    elif failure == "publication":
        db.publication_override = (
            SESSION, RELEASE, AT + timedelta(seconds=1), "WATERMARKS_ONLY",
        )

    with pytest.raises(R11ConsumerCommitUnverified, match=reason):
        commit_r11_mayak_publication(
            db, replay=replay, snapshot=snapshot(replay),
            expected_generation=0, expected_previous_publication_id=None,
        )
    assert db.committed is None


def test_source_epoch_fence_uses_current_r07_lock_abi() -> None:
    source = Path(
        "src/bybit_workbench/mayak/publication_consumer_commit.py"
    ).read_text(encoding="utf-8")
    migration = Path(
        "operations/sql/20261114_r07_source_epoch_transaction_guard.sql"
    ).read_text(encoding="utf-8")

    prefix = "CRIPTA:R07:SOURCE_EPOCH_TRANSITION:V1:"
    assert prefix in source
    assert prefix in migration
    assert "r07_source_epoch_guard_v1" in source
    assert "r07_source_epoch_guard_v1" in migration
    assert "pg_try_advisory_xact_lock_shared" in source

def test_migration_is_one_bounded_latest_row_with_runtime_actor_guard() -> None:
    sql = Path(
        "operations/sql/20261115_r11_mayak_consumer_commit.sql"
    ).read_text(encoding="utf-8")

    assert "consumer_id='R11_PG_ORIGINAL_MAYAK_V1'" in sql
    assert "pg_column_size(snapshot) <= 16777216" in sql
    assert "octet_length(state_checkpoint)" in sql
    assert "GRANT SELECT,INSERT,UPDATE" in sql
    assert "REVOKE DELETE,TRUNCATE" in sql
    assert "CREATE TABLE IF NOT EXISTS mayak_v2.r11_consumer_commit" in sql
    assert "CREATE TABLE IF NOT EXISTS mayak_v2.r11_consumer_history" not in sql


def test_source_reset_lineage_ignores_only_legacy_snapshot_digest() -> None:
    db = AtomicDB()
    replay = replay_state()
    commit_r11_mayak_publication(
        db, replay=replay, snapshot=snapshot(replay),
        expected_generation=0, expected_previous_publication_id=None,
    )
    assert db.committed is not None
    row = list(db.committed)
    row[10] = "0" * 64
    db.committed = tuple(row)

    with pytest.raises(
        R11ConsumerCommitUnverified,
        match="RESTORE_SNAPSHOT_DIGEST_INVALID",
    ):
        load_r11_mayak_consumer_commit(
            db, expected_source_session_id=SESSION,
            expected_release_commit=RELEASE,
        )

    lineage = verify_r11_mayak_consumer_reset_lineage(
        db, expected_source_session_id=SESSION,
        expected_release_commit=RELEASE,
    )
    assert lineage is not None
    assert lineage.generation == 1
    assert lineage.publication_id == 100

    row = list(db.committed)
    row[8] = "0" * 64
    db.committed = tuple(row)
    with pytest.raises(
        R11ConsumerCommitUnverified,
        match="RESET_LINEAGE_IDENTITY_OR_STATE_DIGEST_INVALID",
    ):
        verify_r11_mayak_consumer_reset_lineage(
            db, expected_source_session_id=SESSION,
            expected_release_commit=RELEASE,
        )
