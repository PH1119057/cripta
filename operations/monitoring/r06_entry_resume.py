"""Bounded R06 recovery and audited no-order checkpoint release adoption.

Actor: the exclusive systemd Entry worker, Unix/DB cripta. No filesystem
writes. The reviewed installer/postgres creates the append-only bridge table.
This module never resets a cursor or creates an execution request.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from bybit_workbench.market_data.source_required_feed_manifest import RequiredPublicFeed
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from operations.monitoring.r06_fresh_entry_boundary import _require_no_exposure_or_requests

APPROVED_BASELINE = "9c27db23ce9f19c199506d225c37841901a7235d"
_SEMANTIC_PATHS = (
    "src/bybit_workbench/universal_entry",
    "src/bybit_workbench/domain",
    "src/bybit_workbench/market_data",
    "src/bybit_workbench/market_identity.py",
    "operations/monitoring/r06_fresh_entry_boundary.py",
    "operations/monitoring/required_public_feeds.json",
)


class R06ResumeBlocked(RuntimeError):
    """No exact no-order compatibility or bounded original history proof."""


def entry_native_ttls(path: Path, symbols: tuple[str, ...]) -> dict[str, timedelta]:
    """Use only the checked-in ENTRY_P90 manifest, never a guessed TTL."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        requirements = tuple(RequiredPublicFeed(**item) for item in payload)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise R06ResumeBlocked("R06_ENTRY_SOURCE_MANIFEST_INVALID") from exc
    wanted = [r for r in requirements if r.consumer_id == "ENTRY_P90"
              and r.dataset == "PUBLIC_TRADE" and r.product == "LINEAR"]
    if (len(wanted) != len(symbols) or {r.symbol for r in wanted} != set(symbols)
            or any(not r.required or r.freshness_seconds > 300 for r in wanted)):
        raise R06ResumeBlocked("R06_ENTRY_SOURCE_MANIFEST_SCOPE_DRIFT")
    return {r.symbol: timedelta(seconds=r.freshness_seconds) for r in wanted}


def recovery_counts(
    connection: Any, *, session: str, symbol: str, closed_at: datetime,
    counts: dict[str, int],
) -> dict[str, int]:
    """Extra closed history for the persisted state, bounded to one day.

    The causal point is unchanged. Older native events rebuild state only;
    the independent five-second no-late-signal gate remains in the worker.
    """
    rows = connection.execute(
        "SELECT causal_state FROM strategy_entry.p90_scoped_native_consumer_checkpoints "
        "WHERE source_session_id=%s ORDER BY strategy_id", (session,),
    ).fetchall()
    earliest = closed_at
    for (state,) in rows:
        if not isinstance(state, dict) or not isinstance(state.get("by_symbol"), dict):
            raise R06ResumeBlocked("R06_RESUME_CAUSAL_STATE_INVALID")
        saved = state["by_symbol"].get(symbol)
        if saved is None:
            continue
        try:
            at = datetime.fromisoformat(saved["geometry_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise R06ResumeBlocked("R06_RESUME_GEOMETRY_TIME_INVALID") from exc
        if at.utcoffset() != timedelta(0) or at > closed_at:
            raise R06ResumeBlocked("R06_RESUME_GEOMETRY_TIME_INVALID")
        earliest = min(earliest, at)
    lag = closed_at - earliest
    if lag > timedelta(days=1):
        raise R06ResumeBlocked("R06_RESUME_HISTORY_BOUND_EXCEEDED")
    seconds = int(lag.total_seconds())
    return {tf: n + (seconds + int(tf)*60 - 1)//(int(tf)*60)
            for tf, n in counts.items()}


def verify_semantic_compatibility(source: Path, previous: str, target: str) -> None:
    """One explicitly approved baseline; unchanged Engine/Plan/codec trees."""
    if (previous != APPROVED_BASELINE or len(target) != 40
            or any(ch not in "0123456789abcdef" for ch in target)):
        raise R06ResumeBlocked("R06_RESUME_RELEASE_NOT_APPROVED")
    for path in _SEMANTIC_PATHS:
        values = []
        for sha in (previous, target):
            try:
                value = subprocess.check_output(
                    ["git", "-C", str(source), "rev-parse", f"{sha}:{path}"],
                    stderr=subprocess.DEVNULL, timeout=5,
                ).strip()
            except (OSError, subprocess.SubprocessError) as exc:
                raise R06ResumeBlocked("R06_RESUME_SEMANTIC_PROOF_UNAVAILABLE") from exc
            if len(value) != 40:
                raise R06ResumeBlocked("R06_RESUME_SEMANTIC_PROOF_UNAVAILABLE")
            values.append(value)
        if values[0] != values[1]:
            raise R06ResumeBlocked("R06_RESUME_ENTRY_SEMANTICS_CHANGED")


def bridge_checkpoint_release(
    connection: Any, *, bundles: tuple[Any, ...], session: str,
    producer: str, target: str, source_checkout: Path,
) -> None:
    """Signed, atomic adoption of two EXISTING cursors, with durable audit.

    Called only by the exclusive, advisory-locked same-unit R06 worker.
    An epoch, plan, frame, publication, last exec ID or causal state is NEVER
    replaced. Both release fields and both audit rows commit or roll back.
    """
    from operations.monitoring.p90l90_real_source_bootstrap import (
        preflight_p90_real_private_flat_source,
    )

    if len(bundles) != 2 or {b.card.strategy_id for b in bundles} != {MAIN_ID, SUB_ID}:
        raise R06ResumeBlocked("R06_RESUME_PLAN_SCOPE_INVALID")
    verify_semantic_compatibility(source_checkout, APPROVED_BASELINE, target)
    preflight_p90_real_private_flat_source(connection, bundles=bundles)
    with connection.transaction():
        source = connection.execute(
            "SELECT state,producer_release_commit FROM "
            "analytics.native_public_trade_source_sessions "
            "WHERE source_session_id=%s FOR SHARE", (session,),
        ).fetchone()
        if source != ("ACTIVE", producer):
            raise R06ResumeBlocked("R06_RESUME_SOURCE_EPOCH_CHANGED")
        _require_no_exposure_or_requests(connection)
        rows = connection.execute(
            "SELECT strategy_id,strategy_version,entry_plan_fingerprint,"
            "exit_plan_fingerprint,consumer_release_commit,frame_number,frame_index,"
            "last_exec_id,bootstrap_publication_id,last_publication_id,"
            "bootstrap_source_at,causal_state,producer_release_commit "
            "FROM strategy_entry.p90_scoped_native_consumer_checkpoints "
            "WHERE source_session_id=%s ORDER BY strategy_id FOR UPDATE", (session,),
        ).fetchall()
        if len(rows) != 2 or {r[0] for r in rows} != {MAIN_ID, SUB_ID}:
            raise R06ResumeBlocked("R06_RESUME_DUAL_CURSOR_REQUIRED")
        for row in rows:
            (name, version, entry, exit_, previous, frame, index, last_exec,
             boot, last, at, state, prod) = row
            bundle = next(b for b in bundles if b.card.strategy_id == name)
            if (previous != APPROVED_BASELINE or prod != producer
                    or version != bundle.card.strategy_version
                    or entry != bundle.entry_plan.entry_plan_fingerprint
                    or exit_ != bundle.exit_plan.exit_plan_fingerprint
                    or type(frame) is not int or frame < 0
                    or type(index) is not int or index < 0
                    or type(boot) is not int or type(last) is not int or last < boot
                    or not isinstance(at, datetime) or at.utcoffset() != timedelta(0)
                    or not isinstance(state, dict) or state.get("version") != 1
                    or not isinstance(state.get("by_symbol"), dict)):
                raise R06ResumeBlocked("R06_RESUME_CURSOR_IDENTITY_DRIFT")
            publications = connection.execute(
                "SELECT publication_id FROM analytics.market_publications "
                "WHERE publication_id=ANY(%s) AND source_session_id=%s "
                "AND producer_release_commit=%s", ([boot, last], session, producer),
            ).fetchall()
            if {r[0] for r in publications} != {boot, last}:
                raise R06ResumeBlocked("R06_RESUME_PUBLICATION_LINEAGE_INVALID")
            digest = hashlib.sha256(json.dumps(
                state, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            connection.execute(
                "INSERT INTO strategy_entry.p90_r06_checkpoint_release_bridges "
                "(strategy_id,strategy_version,source_session_id,previous_release,"
                "consumer_release_commit,frame_number,frame_index,last_exec_id,"
                "bootstrap_publication_id,last_publication_id,causal_state_sha256) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (name, version, session, previous, target, frame, index, last_exec,
                 boot, last, digest),
            )
            changed = connection.execute(
                "UPDATE strategy_entry.p90_scoped_native_consumer_checkpoints "
                "SET consumer_release_commit=%s WHERE strategy_id=%s "
                "AND strategy_version=%s AND source_session_id=%s "
                "AND consumer_release_commit=%s AND frame_number=%s "
                "AND frame_index=%s AND last_publication_id=%s",
                (target, name, version, session, previous, frame, index, last),
            )
            if changed.rowcount != 1:
                raise R06ResumeBlocked("R06_RESUME_CURSOR_COMPARE_AND_SWAP_FAILED")
