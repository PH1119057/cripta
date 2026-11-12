"""Explicit no-order fresh epoch after GAP; never claim cross-gap continuity."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID


class R06EpochTransitionBlocked(RuntimeError):
    """Old history cannot be silently inherited or reset."""


def prior_epoch(connection: Any, new_session: str) -> tuple[Any, ...]:
    rows = connection.execute(
        "SELECT DISTINCT ON (c.strategy_id) c.strategy_id,c.strategy_version,"
        "c.entry_plan_fingerprint,c.exit_plan_fingerprint,c.source_session_id,"
        "c.producer_release_commit,c.consumer_release_commit,c.frame_number,"
        "c.frame_index,c.last_exec_id,c.bootstrap_publication_id,c.last_publication_id,"
        "c.causal_state,s.state,s.ended_at "
        "FROM strategy_entry.p90_scoped_native_consumer_checkpoints c "
        "JOIN analytics.native_public_trade_source_sessions s "
        "ON s.source_session_id=c.source_session_id "
        "WHERE c.source_session_id<>%s "
        "ORDER BY c.strategy_id,c.bootstrap_source_at DESC,c.last_publication_id DESC",
        (new_session,),
    ).fetchall()
    if (len(rows) != 2 or {r[0] for r in rows} != {MAIN_ID, SUB_ID}
            or len({r[4] for r in rows}) != 1
            or any(r[13] != "GAP" or not isinstance(r[14], datetime) for r in rows)):
        raise R06EpochTransitionBlocked("R06_PRIOR_EPOCH_NOT_EXACT_TERMINAL_GAP")
    return tuple(rows)


def record_fresh_epoch(
    connection: Any, *, proof: Any, strategies: tuple[tuple[str, str, str, str], ...],
    consumer_release: str,
) -> None:
    """Append audit inside the SAME transaction as both future cursor INSERTs.

    The caller has already performed real signed MAIN/SUB FLAT, stopped old
    Entry, and pinned the future closed boundary. Old rows remain untouched.
    """
    if connection.execute(
        "SELECT count(*) FROM strategy_entry.p90_scoped_native_consumer_checkpoints"
    ).fetchone() == (0,):
        return
    if (proof.new_epoch_only is not True
            or proof.closed_5m_at <= proof.legacy_detached_at):
        raise R06EpochTransitionBlocked("R06_NEW_EPOCH_FUTURE_FENCE_REQUIRED")
    rows = prior_epoch(connection, proof.pin.source_session_id)
    for row in rows:
        (name, version, entry, exit_, old, _producer, release, frame, index,
         exec_id, boot, last, state, _, ended) = row
        if ((name, version, entry, exit_) not in strategies
                or type(frame) is not int or frame < 0
                or type(index) is not int or index < 0
                or type(boot) is not int or type(last) is not int or last < boot
                or not isinstance(state, dict) or state.get("version") != 1
                or ended > proof.legacy_detached_at):
            raise R06EpochTransitionBlocked("R06_PRIOR_EPOCH_LINEAGE_INVALID")
        connection.execute(
            "SELECT strategy_id FROM strategy_entry.p90_scoped_native_consumer_checkpoints "
            "WHERE strategy_id=%s AND strategy_version=%s AND source_session_id=%s FOR UPDATE",
            (name, version, old),
        )
        digest = hashlib.sha256(json.dumps(
            state, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        connection.execute(
            "INSERT INTO strategy_entry.p90_r06_fresh_epoch_transitions "
            "(strategy_id,strategy_version,previous_source_session_id,source_session_id,"
            "previous_consumer_release,consumer_release_commit,previous_frame_number,"
            "previous_frame_index,previous_last_exec_id,previous_bootstrap_publication_id,"
            "previous_last_publication_id,previous_causal_state_sha256,"
            "bootstrap_publication_id,bootstrap_source_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (name, version, old, proof.pin.source_session_id, release, consumer_release,
             frame, index, exec_id, boot, last, digest, proof.pin.publication_id,
             proof.closed_5m_at),
        )
