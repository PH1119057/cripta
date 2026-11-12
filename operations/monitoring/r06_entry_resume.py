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
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import ALLOWED_SYMBOLS
from operations.monitoring.r06_entry_publication_shadow_probe import EXISTING_P90_COLD_COUNTS
from operations.monitoring.r06_fresh_entry_boundary import _require_no_exposure_or_requests

APPROVED_BASELINE = "b47aa43d9333083fb83bb16f191e16e1ff97bd33"
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


def geometry_seed_counts(state: dict, symbol: str, bootstrap: datetime) -> dict[str, int]:
    """Recover the exact recorded Wilder seed; legacy seed starts at bootstrap.

    A fingerprint is never waived. Legacy expanded seeds are reconstructed
    exactly, then future geometry uses the approved fixed cold counts and
    records those counts alongside the causal state.
    """
    base = dict(EXISTING_P90_COLD_COUNTS)
    recorded = state.get("seed_counts_by_symbol", {})
    if not isinstance(recorded, dict):
        raise R06ResumeBlocked("R06_GEOMETRY_SEED_METADATA_INVALID")
    if symbol in recorded:
        counts = recorded[symbol]
        if (not isinstance(counts, dict) or counts.keys() != base.keys()
                or any(type(counts[tf]) is not int or not base[tf] <= counts[tf] <= 10000
                       for tf in base)):
            raise R06ResumeBlocked("R06_GEOMETRY_SEED_METADATA_INVALID")
        return dict(counts)
    raw = state.get("by_symbol", {}).get(symbol)
    if raw is None:
        return base
    try:
        at = datetime.fromisoformat(raw["geometry_at"])
        if (bootstrap.utcoffset() != timedelta(0) or at.utcoffset() != timedelta(0)
                or not timedelta(0) <= at-bootstrap <= timedelta(days=1)):
            raise ValueError("invalid seed anchor")
    except (AttributeError, TypeError, KeyError, ValueError) as exc:
        raise R06ResumeBlocked("R06_GEOMETRY_SEED_ANCHOR_INVALID") from exc
    return {tf: n + int((_tf_boundary(at, tf)-_tf_boundary(bootstrap, tf)).total_seconds())
            // (int(tf)*60) for tf, n in base.items()}


def _tf_boundary(at: datetime, tf: str) -> datetime:
    minutes = int(tf)
    return at.replace(minute=at.minute//minutes*minutes, second=0, microsecond=0)


def slice_geometry_seed(
    history: dict[str, tuple[Any, ...]], *, at: datetime, counts: dict[str, int],
) -> dict[str, tuple[Any, ...]]:
    out = {}
    for tf, count in counts.items():
        bars = tuple(c for c in history[tf] if c.closed_at <= at)
        if len(bars) < count:
            raise R06ResumeBlocked("R06_GEOMETRY_EXACT_SEED_MISSING")
        out[tf] = bars[-count:]
    return out


def recovery_counts(
    connection: Any, *, session: str, symbol: str, closed_at: datetime,
    counts: dict[str, int],
) -> dict[str, int]:
    """Extra closed history for the persisted state, bounded to one day.

    The causal point is unchanged. Older native events rebuild state only;
    the independent five-second no-late-signal gate remains in the worker.
    """
    rows = connection.execute(
        "SELECT bootstrap_source_at,causal_state FROM "
        "strategy_entry.p90_scoped_native_consumer_checkpoints "
        "WHERE source_session_id=%s AND strategy_id=%s ORDER BY strategy_id",
        (session, next(name for name, symbols in ALLOWED_SYMBOLS.items() if symbol in symbols)),
    ).fetchall()
    required = dict(counts)
    for bootstrap, state in rows:
        if not isinstance(state, dict) or not isinstance(state.get("by_symbol"), dict):
            raise R06ResumeBlocked("R06_RESUME_CAUSAL_STATE_INVALID")
        saved = state["by_symbol"].get(symbol)
        try:
            # An audited bootstrap may have committed before the first batch.
            # Its unread originals still need geometry at the bootstrap bar.
            at = bootstrap if saved is None else datetime.fromisoformat(saved["geometry_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise R06ResumeBlocked("R06_RESUME_GEOMETRY_TIME_INVALID") from exc
        if at.utcoffset() != timedelta(0) or at > closed_at:
            raise R06ResumeBlocked("R06_RESUME_GEOMETRY_TIME_INVALID")
        if closed_at-at > timedelta(days=1):
            raise R06ResumeBlocked("R06_RESUME_HISTORY_BOUND_EXCEEDED")
        seed = geometry_seed_counts(state, symbol, bootstrap)
        for tf in counts:
            extra = int((_tf_boundary(closed_at, tf)-_tf_boundary(at, tf)).total_seconds())
            required[tf] = max(required[tf], seed[tf] + extra//(int(tf)*60))
    return required


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


def scope_waits_at_bootstrap(connection: Any, *, pin: Any, strategy: str,
                             version: str, symbols: tuple[str, ...]) -> bool:
    """A global bootstrap fence can precede the next trade of a quiet scope.

    Never move the cursor or waive the dispatcher's ahead-of-publication guard.
    Only an untouched, exactly attested global bootstrap fence may wait.
    """
    saved = connection.execute(
        "SELECT frame_number,frame_index,bootstrap_publication_id,causal_state "
        "FROM strategy_entry.p90_scoped_native_consumer_checkpoints "
        "WHERE strategy_id=%s AND strategy_version=%s AND source_session_id=%s",
        (strategy, version, pin.source_session_id),
    ).fetchone()
    if saved is None:
        raise R06ResumeBlocked("R06_SCOPE_CHECKPOINT_MISSING")
    frame, index, bootstrap_id, state = saved
    cursor = (frame, index)
    def maximum(watermarks: dict, scope: tuple[str, ...]) -> tuple[int, int]:
        return max((watermarks[s]["frame_number"], watermarks[s]["frame_index"])
                   for s in scope)
    if cursor <= maximum(pin.native_watermarks, symbols):
        return False
    if state != {"version": 1, "by_symbol": {}}:
        # A real accepted decision cursor ahead of publication still fails.
        return False
    bootstrap = connection.execute(
        "SELECT source_session_id,producer_release_commit,native_watermarks "
        "FROM analytics.market_publications WHERE publication_id=%s", (bootstrap_id,),
    ).fetchone()
    if (bootstrap is None or bootstrap[:2] != (pin.source_session_id,
                                              pin.producer_release_commit)
            or pin.publication_id < bootstrap_id
            or cursor != maximum(bootstrap[2], tuple(bootstrap[2]))
            or cursor > maximum(pin.native_watermarks, tuple(pin.native_watermarks))):
        raise R06ResumeBlocked("R06_BOOTSTRAP_SCOPE_FENCE_UNVERIFIED")
    return True
