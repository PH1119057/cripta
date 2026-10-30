"""P90L90 immutable market-data origin and reviewed executable compatibility.

Original seed rows are NEVER rewritten to another release SHA. New bar facts
are marked with their actual producing release. Every new runtime release must
have a durable append-only compatibility attestation before it can recover
the five native-trade/Entry checkpoints or enter no-order PAPER.

This is a read-side safety gate. It cannot activate a Strategy, create a PAPER
position, mutate Exchange state, or silently register its own compatibility.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

ORIGIN_COMMIT = "ecde51102e4fe624e4b312eca85318540f200229"
ORIGINAL_BAR_SHA256 = "940b9ee8894105ed37143b14dae2f6b517cc230e158c2022837e230abe16e011"
ORIGINAL_CHECKPOINT_SHA256 = "3020454d5af5d335b8a04fea0cdabdf2ae3e8d750aea21910c347d46c90fea8b"
DATA_LINEAGE_ID = "p90l90-causal-v1-" + ORIGINAL_BAR_SHA256
CONTRACT_VERSION = "P90L90_CAUSAL_LINEAGE_V1"
_ORIGINAL_BAR_COUNT = 17995
_SYMBOLS = frozenset({"LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"})
_SHA = re.compile(r"^[0-9a-f]{40}$")
_ATTESTATION_TABLE = "strategy_entry.p90l90_data_lineage_compatibility"


@dataclass(frozen=True, slots=True)
class P90L90DataLineage:
    data_lineage_id: str
    origin_commit: str
    loaded_commit: str
    approved_producer_commits: frozenset[str]


def _digest_row(hasher: Any, values: object, *, default: bool = False) -> None:
    if default:
        serialized = json.dumps(
            values, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), default=str,
        )
    else:
        serialized = json.dumps(
            values, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"),
        )
    hasher.update(serialized.encode("utf-8") + b"\n")


def verify_original_bar_archive(connection: Any) -> None:
    """Verify original 17,995 immutable records, including exact JSON bytes.

    A later live release may append new bars under its OWN source_commit.
    This function always verifies the original source subset, never relabels it.
    """
    digest = hashlib.sha256()
    total = 0
    symbols: set[str] = set()
    groups: dict[tuple[str, str], int] = {}
    rows = connection.execute(
        """SELECT symbol,timeframe,closed_at,event_json,source_commit
             FROM strategy_entry.p90l90_causal_closed_bars
            WHERE source_commit=%s
            ORDER BY symbol,timeframe,closed_at""",
        (ORIGIN_COMMIT,),
    )
    for symbol, timeframe, closed_at, payload, source in rows:
        if (symbol not in _SYMBOLS or timeframe not in {"5", "15"}
                or not isinstance(closed_at, datetime) or source != ORIGIN_COMMIT):
            raise RuntimeError("P90_DATA_ORIGINAL_BAR_IDENTITY_MISMATCH")
        _digest_row(digest, [
            symbol, timeframe, closed_at.isoformat(), payload, source,
        ])
        total += 1
        symbols.add(symbol)
        key = symbol, timeframe
        groups[key] = groups.get(key, 0) + 1
    if (
        total != _ORIGINAL_BAR_COUNT
        or symbols != _SYMBOLS
        or len(groups) != 10
        or any(groups.get((symbol, "5")) != 2600
               or groups.get((symbol, "15")) != 999 for symbol in _SYMBOLS)
        or digest.hexdigest() != ORIGINAL_BAR_SHA256
    ):
        raise RuntimeError("P90_DATA_ORIGINAL_BAR_DIGEST_OR_COVERAGE_MISMATCH")


def _checkpoint_rows(connection: Any) -> list[tuple[Any, ...]]:
    rows = connection.execute(
        """SELECT symbol,checkpoint_json,entry_checkpoint_json,
                  source_commit,generation,last_writer_release_commit
             FROM strategy_entry.p90l90_causal_transport_checkpoints
            ORDER BY symbol"""
    ).fetchall()
    rows = cast(list[tuple[Any, ...]], rows)
    if (
        len(rows) != 5
        or {r[0] for r in rows} != _SYMBOLS
        or any(r[3] != ORIGIN_COMMIT or r[2] is None
               or not isinstance(r[4], int) or r[4] < 0
               for r in rows)
    ):
        raise RuntimeError("P90_DATA_CHECKPOINT_SOURCE_OR_COVERAGE_MISMATCH")
    return rows


def _attestations(connection: Any) -> frozenset[str]:
    rows = connection.execute(
        f"""SELECT loaded_commit,origin_commit,archive_sha256,
                   seed_checkpoint_sha256,contract_version,original_bar_count
              FROM {_ATTESTATION_TABLE}
             WHERE data_lineage_id=%s""",
        (DATA_LINEAGE_ID,),
    ).fetchall()
    approved: set[str] = set()
    for release, origin, digest, checkpoint_digest, contract, count in rows:
        if (
            _SHA.fullmatch(release) is None or origin != ORIGIN_COMMIT
            or digest != ORIGINAL_BAR_SHA256
            or checkpoint_digest != ORIGINAL_CHECKPOINT_SHA256
            or contract != CONTRACT_VERSION or count != _ORIGINAL_BAR_COUNT
        ):
            raise RuntimeError("P90_DATA_LINEAGE_ATTESTATION_CORRUPT")
        approved.add(release)
    return frozenset(approved)


def _check_producer_rows(
    connection: Any, *, approved: frozenset[str],
) -> None:
    # An original candle predates the producer-release field; do not backfill.
    rows = connection.execute(
        """SELECT source_commit,producer_release_commit,count(*)
             FROM strategy_entry.p90l90_causal_closed_bars
            GROUP BY source_commit,producer_release_commit"""
    ).fetchall()
    if not rows:
        raise RuntimeError("P90_DATA_EMPTY_ARCHIVE")
    for source, producer, n in rows:
        if n <= 0:
            raise RuntimeError("P90_DATA_INVALID_ARCHIVE_GROUP")
        if source == ORIGIN_COMMIT and producer is None:
            continue
        if (
            source == ORIGIN_COMMIT or producer != source
            or source not in approved or _SHA.fullmatch(source) is None
        ):
            raise RuntimeError("P90_DATA_UNATTESTED_BAR_PRODUCER")


def inspect_p90_data_lineage(
    connection: Any, *, loaded_commit: str,
) -> P90L90DataLineage:
    """Fail-closed, SELECT-only compatibility proof for installed runtime."""
    if _SHA.fullmatch(loaded_commit) is None:
        raise ValueError("P90_DATA_LOADED_RELEASE_INVALID")
    verify_original_bar_archive(connection)
    approved = _attestations(connection)
    if loaded_commit not in approved:
        raise RuntimeError("P90_DATA_LOADED_RELEASE_NOT_ATTESTED")
    _check_producer_rows(connection, approved=approved)
    rows = _checkpoint_rows(connection)
    if all(r[4] == 0 for r in rows):
        digest = hashlib.sha256()
        for symbol, checkpoint, entry, source, generation, writer in rows:
            if writer is not None:
                raise RuntimeError("P90_DATA_ORIGINAL_SEED_HAS_NEW_WRITER")
            _digest_row(digest, (
                symbol, checkpoint, entry, source, generation,
            ), default=True)
        if digest.hexdigest() != ORIGINAL_CHECKPOINT_SHA256:
            raise RuntimeError("P90_DATA_ORIGINAL_CHECKPOINT_DIGEST_MISMATCH")
    for _symbol, _checkpoint, _entry, _origin, generation, writer in rows:
        if generation > 0 and writer not in approved:
            raise RuntimeError("P90_DATA_UNATTESTED_CHECKPOINT_WRITER")
        if generation == 0 and writer is not None:
            raise RuntimeError("P90_DATA_UNEXPECTED_SEED_WRITER")
    return P90L90DataLineage(
        DATA_LINEAGE_ID, ORIGIN_COMMIT, loaded_commit,
        frozenset({ORIGIN_COMMIT, *approved}),
    )


def verify_p90_lineage_for_registration(
    connection: Any, *, loaded_commit: str,
) -> None:
    """Pre-insert proof: no automatic approval from Git ancestry alone."""
    if _SHA.fullmatch(loaded_commit) is None:
        raise ValueError("P90_DATA_LOADED_RELEASE_INVALID")
    verify_original_bar_archive(connection)
    approved = _attestations(connection)
    _check_producer_rows(connection, approved=approved)
    rows = _checkpoint_rows(connection)
    if not approved:
        if any(r[4] != 0 or r[5] is not None for r in rows):
            raise RuntimeError("P90_DATA_INITIAL_CHECKPOINT_ALREADY_CHANGED")
        digest = hashlib.sha256()
        for symbol, checkpoint, entry, source, generation, _writer in rows:
            _digest_row(digest, (
                symbol, checkpoint, entry, source, generation,
            ), default=True)
        if digest.hexdigest() != ORIGINAL_CHECKPOINT_SHA256:
            raise RuntimeError("P90_DATA_INITIAL_CHECKPOINT_DIGEST_MISMATCH")
    else:
        for _symbol, _checkpoint, _entry, _origin, generation, writer in rows:
            if generation > 0 and writer not in approved:
                raise RuntimeError("P90_DATA_PREVIOUS_WRITER_NOT_ATTESTED")
            if generation == 0 and writer is not None:
                raise RuntimeError("P90_DATA_SEED_WRITER_UNEXPECTED")
