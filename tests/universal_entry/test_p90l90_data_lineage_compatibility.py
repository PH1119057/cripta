"""No-order P90 data lineage contract across code-only verified releases.

All tests are offline and read-only: no real PostgreSQL, Bybit, credentials or
production filesystem. Original source SHA is never rewritten to the new one.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from bybit_workbench.universal_entry import p90l90_data_lineage as lineage

RELEASE = "f" * 40
OTHER_RELEASE = "d" * 40


class Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


def seed_rows(*, writer: str | None = None, generation: int = 0):
    return [
        (
            symbol,
            {"schema": "P90L90_CAUSAL_TRANSPORT_JOIN_V1", "symbol": symbol},
            {"schema": "P90L90_CAUSAL_ENTRY_RESTART_V1"},
            lineage.ORIGIN_COMMIT,
            generation,
            writer,
        )
        for symbol in sorted(lineage._SYMBOLS)
    ]


def digest_of_checkpoints(rows: list[tuple[Any, ...]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            json.dumps(
                row[:5],sort_keys=True,separators=(",",":"),
                ensure_ascii=False,default=str,
            ).encode("utf-8")+b"\n"
        )
    return digest.hexdigest()


def attested(release: str) -> list[tuple[Any, ...]]:
    return [(
        release, lineage.ORIGIN_COMMIT,
        lineage.ORIGINAL_BAR_SHA256,
        lineage.ORIGINAL_CHECKPOINT_SHA256,
        lineage.CONTRACT_VERSION,17995,
    )]


class FakeDB:
    def __init__(
        self,
        *,
        approved: list[tuple[Any, ...]],
        producers: list[tuple[Any, ...]] | None = None,
        checkpoints: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.approved = approved
        self.producers = producers or [(lineage.ORIGIN_COMMIT,None,17995)]
        self.checkpoints = checkpoints or seed_rows()
        self.sql: list[str] = []

    def execute(
        self, sql: str, params: tuple[Any, ...] | None = None,
    ) -> Result:
        self.sql.append(sql)
        if "p90l90_data_lineage_compatibility" in sql:
            assert params == (lineage.DATA_LINEAGE_ID,)
            return Result(self.approved)
        if "GROUP BY source_commit,producer_release_commit" in sql:
            return Result(self.producers)
        if "last_writer_release_commit" in sql:
            return Result(self.checkpoints)
        raise AssertionError("unexpected DB query " + sql)


def _verified(monkeypatch: Any) -> None:
    monkeypatch.setattr(lineage,"verify_original_bar_archive",lambda c: None)
    monkeypatch.setattr(
        lineage,"ORIGINAL_CHECKPOINT_SHA256",digest_of_checkpoints(seed_rows()),
    )


def test_read_only_attested_new_release_preserves_old_checkpoint_source(
    monkeypatch: Any,
) -> None:
    _verified(monkeypatch)
    conn=FakeDB(approved=attested(RELEASE))
    data=lineage.inspect_p90_data_lineage(conn,loaded_commit=RELEASE)
    assert data.origin_commit == lineage.ORIGIN_COMMIT
    assert data.loaded_commit == RELEASE
    assert data.data_lineage_id != RELEASE
    assert data.approved_producer_commits == frozenset({
        lineage.ORIGIN_COMMIT,RELEASE,
    })
    assert all("SELECT" in sql and "FOR UPDATE" not in sql for sql in conn.sql)


def test_unattested_new_code_sha_fails_closed(monkeypatch: Any) -> None:
    _verified(monkeypatch)
    conn=FakeDB(approved=attested(OTHER_RELEASE))
    with pytest.raises(RuntimeError, match="LOADED_RELEASE_NOT_ATTESTED"):
        lineage.inspect_p90_data_lineage(conn,loaded_commit=RELEASE)


def test_unattested_new_bar_producer_fails_closed(monkeypatch: Any) -> None:
    _verified(monkeypatch)
    conn=FakeDB(
        approved=attested(RELEASE),
        producers=[
            (lineage.ORIGIN_COMMIT,None,17995),
            (OTHER_RELEASE,OTHER_RELEASE,1),
        ],
    )
    with pytest.raises(RuntimeError, match="UNATTESTED_BAR_PRODUCER"):
        lineage.inspect_p90_data_lineage(conn,loaded_commit=RELEASE)


def test_checkpoint_generation_carries_actual_writer(monkeypatch: Any) -> None:
    _verified(monkeypatch)
    conn=FakeDB(
        approved=attested(RELEASE),
        checkpoints=seed_rows(writer=RELEASE,generation=2),
    )
    result=lineage.inspect_p90_data_lineage(conn,loaded_commit=RELEASE)
    assert result.loaded_commit == RELEASE
    assert all(row[3] == lineage.ORIGIN_COMMIT for row in conn.checkpoints)
    conn.checkpoints=seed_rows(writer=OTHER_RELEASE,generation=2)
    with pytest.raises(RuntimeError, match="UNATTESTED_CHECKPOINT_WRITER"):
        lineage.inspect_p90_data_lineage(conn,loaded_commit=RELEASE)


def test_initial_registration_requires_original_seed_digest(
    monkeypatch: Any,
) -> None:
    _verified(monkeypatch)
    conn=FakeDB(approved=[])
    lineage.verify_p90_lineage_for_registration(conn,loaded_commit=RELEASE)
    conn.checkpoints=seed_rows(writer=RELEASE,generation=1)
    with pytest.raises(RuntimeError,match="INITIAL_CHECKPOINT_ALREADY_CHANGED"):
        lineage.verify_p90_lineage_for_registration(conn,loaded_commit=RELEASE)


def test_invalid_exact_release_rejected_before_db_access(monkeypatch: Any) -> None:
    _verified(monkeypatch)
    conn=FakeDB(approved=attested(RELEASE))
    with pytest.raises(ValueError,match="LOADED_RELEASE_INVALID"):
        lineage.inspect_p90_data_lineage(conn,loaded_commit="not-a-sha")
    assert conn.sql == []
