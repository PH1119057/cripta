"""Fail-closed P90L90 REAL v3 technical LIVE-arm evidence producer.

The producer is intentionally separate from owner approval.  It may run only
after source-only PREARM has activated the exact two immutable v3 Strategies.
It reads current release/source/private/account/lifecycle state and writes
durable §4.7 evidence.  It never opens the mainnet gate, never grants execution
permission, never creates a LIVE-arm session and never calls an Exchange
mutation endpoint.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from bybit_workbench.live_arm_readiness import (
    evaluate_live_arm,
    scope_for_check,
)
from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.p90l90_real_arm_control import (
    p90l90_real_v3_contexts_and_routes,
)
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    MAX_ACCOUNT_COMMITTED,
    REQUESTED_USDT,
    VERSION,
)
from bybit_workbench.universal_entry.runtime_loader import load_active_strategy_bundles
from bybit_workbench.universal_exit.p90l90_real_private_ipc import P90PrivateProofClient

_P90_PARITY_MODULES = (
    "src/bybit_workbench/p90l90_real_v3_independent_parity.py",
    "src/bybit_workbench/universal_entry/engine.py",
    "src/bybit_workbench/universal_entry/execution_bridge.py",
    "src/bybit_workbench/universal_entry/p90l90_real_contract_v3.py",
    "src/bybit_workbench/universal_entry/p90l90_crossing_fact.py",
    "src/bybit_workbench/universal_exit/p90l90_real_v3.py",
    "src/bybit_workbench/universal_exit/execution_bridge.py",
)

_MAIN_ACCOUNT = "ta-2122c81ecff14f3b85b102db605212f7"
_SUB_ACCOUNT = "ta-853ac5fcd58185697396d33cd57459fa"
_ACCOUNT_BY_STRATEGY = {MAIN_ID: _MAIN_ACCOUNT, SUB_ID: _SUB_ACCOUNT}
_EVIDENCE_SOURCE = "p90l90-real-v3:technical-prearm-evidence"
_SERVICE_UNITS = (
    "cripta-private-runtime.service",
    f"cripta-private-runtime@{_SUB_ACCOUNT}.service",
    "cripta-lifecycle-supervisor.service",
    f"cripta-lifecycle-account@{_SUB_ACCOUNT}.service",
    "cripta-position-supervisor.service",
    f"cripta-account-position@{_SUB_ACCOUNT}.service",
    "cripta-dashboard.service",
)


class P90PrearmEvidenceBlocked(ValueError):
    """Current P90 PREARM state cannot prove all technical LIVE-arm gates."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise P90PrearmEvidenceBlocked("P90 REAL v3 PREARM: " + reason)


def _aware(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise P90PrearmEvidenceBlocked(
            "P90 REAL v3 PREARM: " + label + " timestamp missing"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise P90PrearmEvidenceBlocked(
            "P90 REAL v3 PREARM: " + label + " timestamp naive"
        )
    return value.astimezone(UTC)


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise P90PrearmEvidenceBlocked(
            f"P90 REAL v3 PREARM: {label} unreadable: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise P90PrearmEvidenceBlocked(
            "P90 REAL v3 PREARM: " + label + " is not an object"
        )
    return cast(dict[str, object], raw)


def _service_active(unit: str) -> None:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", unit],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _require(result.returncode == 0, "required service inactive: " + unit)


def _git_text(*args: str) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise P90PrearmEvidenceBlocked(
            "P90 REAL v3 PREARM: release Git evidence unavailable"
        ) from exc


def _release_and_parity_evidence(
    *,
    release_commit: str,
    source_root: Path,
    runtime_root: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    _require(
        len(release_commit) == 40
        and all(ch in "0123456789abcdef" for ch in release_commit),
        "loaded release commit invalid",
    )
    source_commit = _git_text("git", "-C", str(source_root), "rev-parse", "HEAD")
    remote = _git_text(
        "git", "-C", str(source_root), "ls-remote", "origin", "refs/heads/main"
    ).split()[0]
    dirty = _git_text("git", "-C", str(source_root), "status", "--porcelain=v1")
    try:
        runtime_commit = (runtime_root / "INSTALLED_COMMIT").read_text(
            encoding="utf-8"
        ).strip()
        state_commit = Path("/var/lib/cripta/release/INSTALLED_COMMIT").read_text(
            encoding="utf-8"
        ).strip()
    except OSError as exc:
        raise P90PrearmEvidenceBlocked(
            f"P90 REAL v3 PREARM: installed release identity unreadable: {exc}"
        ) from exc
    _require(not dirty, "source checkout is dirty")
    _require(
        source_commit == remote == runtime_commit == state_commit == release_commit,
        "remote/source/runtime/state release identity mismatch",
    )

    hashes: dict[str, str] = {}
    for relative in _P90_PARITY_MODULES:
        source_path = source_root / relative
        runtime_path = runtime_root / relative
        try:
            source_bytes = source_path.read_bytes()
            runtime_bytes = runtime_path.read_bytes()
            release_bytes = subprocess.check_output(
                ["git", "-C", str(source_root), "show", f"{release_commit}:{relative}"],
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise P90PrearmEvidenceBlocked(
                f"P90 REAL v3 PREARM: parity module unreadable {relative}: {exc}"
            ) from exc
        source_canonical = source_bytes.replace(b"\r\n", b"\n")
        runtime_canonical = runtime_bytes.replace(b"\r\n", b"\n")
        release_canonical = release_bytes.replace(b"\r\n", b"\n")
        source_hash = hashlib.sha256(source_canonical).hexdigest()
        _require(
            source_hash == hashlib.sha256(runtime_canonical).hexdigest()
            == hashlib.sha256(release_canonical).hexdigest(),
            "parity-critical source/runtime/Git mismatch " + relative,
        )
        hashes[relative] = source_hash

    from bybit_workbench.p90l90_real_v3_independent_parity import (
        P90IndependentParityFailure,
        run_p90l90_real_v3_independent_decision_parity,
    )

    try:
        parity = run_p90l90_real_v3_independent_decision_parity()
    except P90IndependentParityFailure as exc:
        raise P90PrearmEvidenceBlocked(
            "P90 REAL v3 PREARM: independent parity failed: " + str(exc)
        ) from exc
    _require(
        parity.get("status") == "PASS"
        and parity.get("cases") == 10
        and parity.get("old_paper_activation") is False
        and parity.get("real_exchange_mutation") is False
        and parity.get("database_mutation") is False,
        "independent parity proof incomplete",
    )
    identity = {
        "release_commit": release_commit,
        "remote_main": remote,
        "source_commit": source_commit,
        "runtime_commit": runtime_commit,
        "state_commit": state_commit,
        "source_clean": True,
    }
    return identity, {
        "proof": parity,
        "module_sha256": hashes,
        "release_commit": release_commit,
    }


def _exact_policy(bundle: Any) -> dict[str, object]:
    card = bundle.card
    entry = card.entry_policy.to_dict()
    execution = dict(entry["execution_policy"])
    capital = card.capital_policy.to_dict()
    protection = dict(card.protection_policy.to_dict()["initial_protection"])
    exit_policy = card.exit_policy.to_dict()
    trailing = dict(exit_policy["trailing"])
    local = dict(exit_policy["local_zone_exit"])
    lifecycle = card.lifecycle_policy.to_dict()
    embargo = dict(lifecycle["second_hardstop_embargo"])
    cap = str(MAX_ACCOUNT_COMMITTED[card.strategy_id])
    _require(
        execution == {
            "order_type": "MARKET",
            "reference_value_path": "fact.p90l90.candidate_price",
            "max_request_age_seconds": 5,
        },
        card.strategy_id + " Entry execution policy drift",
    )
    _require(
        capital["require_capacity"] is True
        and capital["capacity_max_age_seconds"] == 15
        and capital["capacity_min_quality"] == "HIGH"
        and capital["amount_currency"] == "USDT"
        and capital["requested_amount"] == "10"
        and int(capital["leverage"]) == 1
        and capital["margin_mode"] == "ISOLATED"
        and capital["max_simultaneous_committed_usdt"] == cap
        and capital["count_pending_reservations"] is True
        and int(capital["max_open_positions_per_symbol"]) == 1
        and capital["exchange_min_notional_if_exceeds_requested"] == "REJECT",
        card.strategy_id + " capital policy drift",
    )
    _require(
        protection == {
            "role": "CATASTROPHIC_GUARD",
            "stop_loss_enabled": True,
            "stop_loss_pct": "10",
            "stop_loss_price_basis": "ACTUAL_EXECUTED_AVERAGE_ENTRY",
            "exchange_resident_required": True,
            "take_profit_enabled": True,
            "take_profit_reference_path": "fact.p90l90.opposite_raw_l53_target",
            "trigger_by": "LastPrice",
            "tpsl_mode": "Full",
        },
        card.strategy_id + " initial protection drift",
    )
    _require(
        trailing == {
            "enabled": True,
            "activation_profit_pct": "0.5",
            "distance_pct": "0.35",
            "reference": "BEST_FAVORABLE_PRICE_SINCE_ENTRY",
            "update_effective_from": "NEXT_ORDERED_TRADE",
            "never_loosen": True,
        },
        card.strategy_id + " TRAIL035 drift",
    )
    _require(
        local["enabled"] is True
        and local["operator"] == "P90L90_DYNAMIC_OPPOSITE_RAW_L53"
        and local["target"] == "OPPOSITE_RAW_BOUNDARY"
        and local["update_mode"] == "EACH_CAUSAL_GEOMETRY_CHANGE"
        and local["minimum_favorable_pct"] == "0.5"
        and local["target_fact_path"] == "fact.p90l90.opposite_raw_l53_target"
        and local["marketable_action"] == "CLOSE_MARKET",
        card.strategy_id + " dynamic RAW TP drift",
    )
    _require(
        exit_policy["execution_policy"] == {"max_request_age_seconds": 5},
        card.strategy_id + " Exit request TTL drift",
    )
    _require(
        embargo == {
            "enabled": True,
            "hardstop_count": 2,
            "rolling_hours": 48,
            "new_entry_pause_hours": 48,
            "close_existing_positions": False,
            "domain": "STRATEGY_VERSION",
        },
        card.strategy_id + " SECOND48 drift",
    )
    _require(
        lifecycle["position_mode"] == "ONE_WAY"
        and int(lifecycle["max_open_positions_per_symbol"]) == 1
        and lifecycle["emergency_policy"] == {
            "enabled": True,
            "operator_kill": "MAINNET_GATE_OFF",
            "initial_stop_loss_pct": "10",
            "terminal_loss_containment": "INITIAL_PROTECTION",
        },
        card.strategy_id + " lifecycle/emergency policy drift",
    )
    return {
        "entry_plan_fingerprint": bundle.entry_plan.entry_plan_fingerprint,
        "exit_plan_fingerprint": bundle.exit_plan.exit_plan_fingerprint,
        "requested_usdt": "10",
        "max_committed_usdt": cap,
        "margin_mode": "ISOLATED",
        "leverage": 1,
        "initial_stop_loss_pct": "10",
        "trailing_activation_pct": "0.5",
        "trailing_distance_pct": "0.35",
        "exit_request_ttl_seconds": 5,
        "second48": embargo,
    }


def prepare_p90l90_real_v3_prearm_evidence(
    connection: Any,
    *,
    release_commit: str,
    now: datetime,
    source_root: Path = Path("/srv/cripta/source_checkout"),
    runtime_root: Path = Path("/srv/cripta/runtime/current"),
) -> dict[str, object]:
    """Validate and write technical PASS evidence for all exact five contexts.

    MAINNET_GATE_EXPLICIT_OWNER_APPROVAL is deliberately absent.  The caller
    must later invoke the separate explicit owner arm transaction.
    """
    _require(now.tzinfo is not None and now.utcoffset() is not None, "now must be aware")
    current = now.astimezone(UTC)
    valid_until = current + timedelta(seconds=90)

    identity, parity = _release_and_parity_evidence(
        release_commit=release_commit,
        source_root=source_root,
        runtime_root=runtime_root,
    )
    for unit in _SERVICE_UNITS:
        _service_active(unit)

    # All DB-derived evidence below must come from one serializable snapshot.
    # External release/service proofs above perform no PostgreSQL reads.
    connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")

    observer = _read_json(
        Path("/var/lib/cripta/universal_entry_observer/status.json"),
        "Universal Entry observer status",
    )
    observer_checked_at = datetime.now(UTC)
    observer_at = datetime.fromisoformat(str(observer.get("updated_at") or ""))
    _require(observer_at.tzinfo is not None, "observer timestamp invalid")
    observer_symbols = cast(list[object], observer.get("symbols") or [])
    observer_entry_plans = cast(list[object], observer.get("active_entry_plans") or [])
    observer_exit_plans = cast(list[object], observer.get("active_exit_plans") or [])
    observer_active_strategies = int(str(observer.get("active_strategies") or 0))
    _require(
        observer.get("state") == "RUNNING"
        and observer.get("observer_ready") is True
        and observer.get("source_commit") == release_commit
        and observer_active_strategies == 2
        and set(observer_symbols) == set().union(*ALLOWED_SYMBOLS.values())
        and len(observer_entry_plans) == 2
        and len(observer_exit_plans) == 2
        and 0
        <= (observer_checked_at - observer_at.astimezone(UTC)).total_seconds()
        <= 15,
        "source-only observer not exact/fresh",
    )

    main_lifecycle = _read_json(
        Path("/var/lib/cripta/lifecycle_supervisor/status.json"),
        "MAIN Lifecycle status",
    )
    sub_lifecycle = _read_json(
        Path(f"/var/lib/cripta/lifecycle_account/account-{_SUB_ACCOUNT}/status.json"),
        "SUB Lifecycle status",
    )
    sub_position = _read_json(
        Path(f"/var/lib/cripta/account_position/account-{_SUB_ACCOUNT}/status.json"),
        "SUB account-position status",
    )
    active_fault_codes = cast(
        list[object], main_lifecycle.get("active_fault_codes") or []
    )
    _require(
        main_lifecycle.get("state") == "RUNNING" and not active_fault_codes,
        "MAIN Lifecycle unhealthy",
    )
    critical_raw = main_lifecycle.get("critical_delivery")
    if not isinstance(critical_raw, dict):
        raise P90PrearmEvidenceBlocked(
            "P90 REAL v3 PREARM: critical delivery status missing"
        )
    critical = cast(dict[str, object], critical_raw)
    critical_pending = int(str(critical.get("pending_or_unacknowledged") or 0))
    _require(
        critical_pending == 0,
        "CRITICAL fault delivery has pending/unacknowledged items",
    )
    critical_delivery_status = (
        "PASS"
        if critical.get("configured") is True
        else "OWNER_WAIVED_FOR_P90_PORTAL_MICRO_LIVE"
    )
    _require(
        sub_lifecycle.get("state") == "VERIFIED_ACCOUNT_SCAN"
        and sub_lifecycle.get("trading_rights") == "NONE",
        "SUB Lifecycle evidence unhealthy",
    )
    _require(
        sub_position.get("state") == "SAMPLED_PARTIAL"
        and sub_position.get("trading_rights") == "NONE",
        "SUB account-position observer unhealthy",
    )

    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    _require(gate == (False,), "mainnet gate is not closed")
    _require(
        connection.execute(
            "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true"
        ).fetchone() == (0,),
        "execution permission already active",
    )
    _require(
        connection.execute(
            "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'"
        ).fetchone() == (0,),
        "LIVE-arm session already active",
    )
    _require(
        connection.execute(
            "SELECT count(*) FROM control.account_live_arm_sessions WHERE state='ACTIVE'"
        ).fetchone() == (0,),
        "account LIVE-arm session already active",
    )

    contexts, routes = p90l90_real_v3_contexts_and_routes(
        connection, release_commit=release_commit, now=current
    )
    _require(len(contexts) == 5 and len(routes) == 2, "exact five contexts/two routes missing")
    for strategy_id, (_binding, account_id, _connector) in routes.items():
        _require(
            account_id == _ACCOUNT_BY_STRATEGY[strategy_id],
            strategy_id + " dedicated account drift",
        )

    epoch = connection.execute(
        """SELECT epoch_id,source_commit,market_source_id,source_ws_ready_at,
                  native_mirror_ready_at
             FROM strategy_entry.p90l90_real_source_epochs
            WHERE status='ACTIVE'
            ORDER BY created_at DESC"""
    ).fetchall()
    _require(len(epoch) == 1, "exact one ACTIVE source epoch required")
    epoch_id, epoch_commit, market_source, source_ready, mirror_ready = epoch[0]
    _require(
        str(epoch_commit) == release_commit
        and str(market_source) == BYBIT_PUBLIC_MARKET_SOURCE_ID
        and _aware(source_ready, "source WS") <= current
        and _aware(mirror_ready, "native mirror") <= current,
        "source epoch identity/readiness mismatch",
    )
    checkpoint_rows = connection.execute(
        """SELECT symbol,source_commit,writer_release_commit
             FROM strategy_entry.p90l90_real_causal_checkpoints
            WHERE epoch_id=%s
            ORDER BY symbol""",
        (epoch_id,),
    ).fetchall()
    expected_symbols = set().union(*ALLOWED_SYMBOLS.values())
    _require(
        len(checkpoint_rows) == 5
        and {str(row[0]) for row in checkpoint_rows} == expected_symbols
        and all(str(row[1]) == release_commit and str(row[2]) == release_commit
                for row in checkpoint_rows),
        "five-symbol causal checkpoint proof incomplete",
    )

    bundles = {
        bundle.card.strategy_id: bundle
        for bundle in load_active_strategy_bundles(connection)
        if bundle.card.strategy_version == VERSION
        and bundle.card.strategy_id in {MAIN_ID, SUB_ID}
    }
    _require(set(bundles) == {MAIN_ID, SUB_ID}, "exact active v3 bundles missing")
    policy_evidence = {sid: _exact_policy(bundle) for sid, bundle in bundles.items()}
    _require(
        set(observer_entry_plans)
        == {bundle.entry_plan.entry_plan_fingerprint for bundle in bundles.values()}
        and set(observer_exit_plans)
        == {bundle.exit_plan.exit_plan_fingerprint for bundle in bundles.values()},
        "observer loaded Plan fingerprints differ from active v3",
    )

    _require(
        connection.execute(
            "SELECT to_regclass('runtime.exchange_position_slot_claims')"
        ).fetchone()[0] is not None,
        "physical slot claim table missing",
    )
    _require(
        connection.execute(
            "SELECT to_regclass('runtime.capital_reservations')"
        ).fetchone()[0] is not None,
        "capital reservation table missing",
    )

    account_evidence: dict[str, dict[str, object]] = {}
    position_modes: dict[tuple[str, str], tuple[object, ...]] = {}
    for strategy_id, (_binding, account_id, connector_id) in routes.items():
        proof = P90PrivateProofClient(
            account=account_id, connector=connector_id, timeout_seconds=3.0
        ).read_flat_account()
        proof_checked_at = datetime.now(UTC)
        _require(
            proof.trading_account_id == account_id
            and proof.source_ref == "bybit:signed-flat-linear-usdt:exact-account"
            and proof.open_positions == 0
            and proof.open_orders == 0
            and proof.pending_commands == 0
            and 0 <= (proof_checked_at - proof.observed_at).total_seconds() <= 15,
            strategy_id + " signed private flat proof invalid/stale",
        )
        generation = connection.execute(
            """SELECT generation_id,state,completed_at,available_balance,
                      positions_count,active_orders_count,identity_status
                 FROM runtime.account_state_generations
                WHERE trading_account_id=%s
                  AND state='COMPLETE'
                  AND identity_status='PROVEN'
                  AND completed_at IS NOT NULL
                ORDER BY completed_at DESC LIMIT 1""",
            (account_id,),
        ).fetchone()
        generation_checked_at = datetime.now(UTC)
        _require(generation is not None, strategy_id + " account generation missing")
        completed = _aware(generation[2], strategy_id + " generation")
        available = Decimal(str(generation[3]))
        _require(
            str(generation[1]) == "COMPLETE"
            and str(generation[6]) == "PROVEN"
            and int(generation[4]) == 0
            and int(generation[5]) == 0
            and available.is_finite()
            and available >= REQUESTED_USDT
            and 0 <= (generation_checked_at - completed).total_seconds() <= 15,
            strategy_id + " account generation not fresh/flat/capable of one 10 USDT Entry",
        )
        reconciliation = connection.execute(
            """SELECT id,finished_at_epoch_ms,ok,positions,orders,identity_status
                 FROM runtime.reconciliation_runs
                WHERE trading_account_id=%s
                ORDER BY id DESC LIMIT 1""",
            (account_id,),
        ).fetchone()
        reconciliation_checked_ms = int(datetime.now(UTC).timestamp() * 1000)
        _require(
            reconciliation is not None
            and bool(reconciliation[2])
            and int(reconciliation[3]) == 0
            and int(reconciliation[4]) == 0
            and str(reconciliation[5]) == "PROVEN"
            and 0 <= reconciliation_checked_ms - int(reconciliation[1]) <= 15_000,
            strategy_id + " reconciliation missing/stale/non-flat",
        )
        outstanding = Decimal(str(connection.execute(
            """SELECT coalesce(sum(requested_amount),0)
                 FROM runtime.capital_reservations
                WHERE strategy_id=%s AND state<>'RELEASED'""",
            (strategy_id,),
        ).fetchone()[0]))
        cap = MAX_ACCOUNT_COMMITTED[strategy_id]
        _require(
            outstanding.is_finite()
            and outstanding >= 0
            and outstanding + REQUESTED_USDT <= cap,
            strategy_id + " Strategy capital envelope has no next 10 USDT slot",
        )
        pm_rows = connection.execute(
            """SELECT DISTINCT ON (instrument)
                      instrument,account_ref,product_category,position_mode,position_idx,
                      observed_at,fresh_until,identity_status
                 FROM runtime.position_mode_states
                WHERE trading_account_id=%s
                  AND instrument=ANY(%s)
                ORDER BY instrument,observed_at DESC""",
            (account_id, sorted(ALLOWED_SYMBOLS[strategy_id])),
        ).fetchall()
        position_mode_checked_at = datetime.now(UTC)
        _require(
            len(pm_rows) == len(ALLOWED_SYMBOLS[strategy_id]),
            strategy_id + " position-mode cohort incomplete",
        )
        for row in pm_rows:
            symbol = str(row[0])
            _require(
                symbol in ALLOWED_SYMBOLS[strategy_id]
                and str(row[1]) == "BYBIT:UNIFIED"
                and str(row[2]) == "LINEAR"
                and str(row[3]) == "ONE_WAY"
                and int(row[4]) == 0
                and str(row[7]) == "PROVEN"
                and _aware(row[6], strategy_id + "/" + symbol + " position mode")
                >= position_mode_checked_at,
                strategy_id + "/" + symbol + " position mode invalid/stale",
            )
            position_modes[(strategy_id, symbol)] = tuple(row)
        account_evidence[strategy_id] = {
            "trading_account_id": account_id,
            "connector_id": connector_id,
            "signed_uid": proof.signed_uid,
            "private_generation_id": proof.private_generation_id,
            "private_source_ref": proof.source_ref,
            "private_observed_at": proof.observed_at.isoformat(),
            "account_generation_id": str(generation[0]),
            "account_generation_completed_at": completed.isoformat(),
            "available_balance_usdt": str(available),
            "reconciliation_id": int(reconciliation[0]),
            "reconciliation_finished_at_epoch_ms": int(reconciliation[1]),
            "strategy_committed_usdt": str(outstanding),
            "strategy_cap_usdt": str(cap),
        }

    try:
        rollback_ref = Path("/var/lib/cripta/release/LAST_BACKUP").read_text(
            encoding="utf-8"
        ).strip()
    except OSError as exc:
        raise P90PrearmEvidenceBlocked(
            f"P90 REAL v3 PREARM: rollback reference unreadable: {exc}"
        ) from exc
    _require(bool(rollback_ref), "rollback reference missing")

    def put(
        code: str,
        scope_type: str,
        scope_key: str,
        evidence: dict[str, object],
        *,
        ttl: bool,
        status: str = "PASS",
    ) -> None:
        identity_key = "|".join(
            (release_commit, code, scope_type, scope_key, current.isoformat())
        )
        evidence_id = "p90-v3-prearm-" + hashlib.sha256(
            identity_key.encode()
        ).hexdigest()[:32]
        connection.execute(
            """INSERT INTO control.live_arm_evidence(
                   evidence_id,check_code,scope_type,scope_key,status,
                   checked_at,valid_until,release_commit,source,evidence)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,
                      %s,%s::jsonb)""",
            (
                evidence_id,
                code,
                scope_type,
                scope_key,
                status,
                current,
                valid_until if ttl else None,
                release_commit,
                _EVIDENCE_SOURCE,
                json.dumps(evidence, sort_keys=True, default=str),
            ),
        )

    global_checks: dict[str, tuple[dict[str, object], bool]] = {
        "CANON_CURRENT": (
            {"contract": "P90L90_REAL_V3", "version": VERSION, "issue": 243},
            False,
        ),
        "REMOTE_COMMIT_VERIFIED": (
            {"remote_main": identity["remote_main"]},
            False,
        ),
        "SOURCE_LIVE_IDENTITY": (identity, True),
        "TESTS": (
            {
                "release_contract": release_commit,
                "independent_parity": parity["proof"],
                "policy_checks": policy_evidence,
            },
            False,
        ),
        "LIVE_EQUIVALENCE": (
            {
                "source_only": True,
                "epoch_id": str(epoch_id),
                "market_source_id": str(market_source),
                "symbols": sorted(expected_symbols),
                "accounts": account_evidence,
                "observer_trading_effect": observer.get("trading_effect"),
                "gate": 0,
                "execution_permissions": 0,
                "exchange_mutation": False,
            },
            True,
        ),
        "PAPER_REAL_DECISION_PARITY": (parity, False),
        "EXCHANGE_ACCOUNT_IDENTITY": (
            {"accounts": account_evidence, "signed_private_ipc": True},
            True,
        ),
        "PHYSICAL_SLOT_CLAIM_CONTRACT": (
            {"table": "runtime.exchange_position_slot_claims", "position_idx": 0},
            False,
        ),
        "CAPITAL_RESERVATION_CONTRACT": (
            {
                "table": "runtime.capital_reservations",
                "requested_usdt": "10",
                "main_cap_usdt": "20",
                "sub_cap_usdt": "30",
                "actual_available_checked_per_entry": True,
            },
            False,
        ),
        "LIFECYCLE_SUPERVISOR_BEHAVIOR": (
            {
                "main": main_lifecycle,
                "sub": sub_lifecycle,
                "sub_account_position": sub_position,
                "services": list(_SERVICE_UNITS),
            },
            True,
        ),
        "CRITICAL_FAULT_DELIVERY": (
            {
                "critical_delivery": critical,
                "r1_waiver_reused": False,
                "p90_portal_micro_live_waiver": (
                    critical_delivery_status
                    == "OWNER_WAIVED_FOR_P90_PORTAL_MICRO_LIVE"
                ),
                "owner_decision": "2026-10-30",
                "portal_service_required_active": True,
                "owner_webhook_deferred": (
                    critical_delivery_status
                    == "OWNER_WAIVED_FOR_P90_PORTAL_MICRO_LIVE"
                ),
            },
            True,
        ),
        "RECONCILIATION_PATH": ({"accounts": account_evidence}, True),
        "ROLLBACK_OR_KILL_PATH": (
            {"rollback_ref": rollback_ref, "operator_kill": "MAINNET_GATE_OFF"},
            False,
        ),
    }
    for code, (global_evidence, ttl) in global_checks.items():
        put(
            code,
            "GLOBAL",
            "GLOBAL",
            global_evidence,
            ttl=ttl,
            status=(
                critical_delivery_status
                if code == "CRITICAL_FAULT_DELIVERY"
                else "PASS"
            ),
        )

    for context in contexts:
        bundle = bundles[context.strategy_id]
        policy = policy_evidence[context.strategy_id]
        strategy_checks: dict[str, dict[str, object]] = {
            "EXACT_STRATEGY_ACTIVATION": {
                "strategy_id": context.strategy_id,
                "strategy_version": context.strategy_version,
                "strategy_config_fingerprint": context.strategy_config_fingerprint,
                "strategy_activation_id": context.strategy_activation_id,
                "source_only_prearm": True,
            },
            "ENTRY_PLAN_EXECUTABLE": {
                "entry_plan_fingerprint": bundle.entry_plan.entry_plan_fingerprint,
                "observer_loaded": True,
                "policy": policy,
            },
            "EXIT_PLAN_EXECUTABLE": {
                "exit_plan_fingerprint": bundle.exit_plan.exit_plan_fingerprint,
                "observer_loaded": True,
                "policy": policy,
            },
            "INITIAL_PROTECTION_EXECUTABLE": {
                "stop_loss_pct": "10",
                "basis": "ACTUAL_EXECUTED_AVERAGE_ENTRY",
                "exchange_resident_required": True,
                "initial_tp": "OPPOSITE_RAW_L5_3",
            },
            "TERMINAL_LOSS_CONTAINMENT_PATH": {
                "terminal_loss_containment": "INITIAL_PROTECTION",
                "stop_loss_pct": "10",
            },
            "EMERGENCY_POLICY_SUPPORTED": {
                "operator_kill": "MAINNET_GATE_OFF",
                "position_mode": "ONE_WAY",
                "position_idx": 0,
            },
        }
        for code, context_evidence in strategy_checks.items():
            scope_type, scope_key = scope_for_check(code, context)
            put(code, scope_type, scope_key, context_evidence, ttl=False)

        pm = position_modes[(context.strategy_id, context.symbol)]
        account = account_evidence[context.strategy_id]
        symbol_checks: tuple[tuple[str, dict[str, object]], ...] = (
            (
                "POSITION_MODE_FRESH",
                {
                    "symbol": context.symbol,
                    "position_mode": str(pm[3]),
                    "observed_at": _aware(pm[5], "position mode observed").isoformat(),
                    "fresh_until": _aware(pm[6], "position mode fresh").isoformat(),
                },
            ),
            (
                "POSITION_IDX_EXPECTED",
                {"symbol": context.symbol, "position_idx": int(str(pm[4]))},
            ),
            (
                "MICRO_LIVE_LIMITS",
                {
                    "requested_amount_usdt": "10",
                    "leverage": 1,
                    "margin_mode": "ISOLATED",
                    "strategy_max_committed_usdt":
                        str(MAX_ACCOUNT_COMMITTED[context.strategy_id]),
                    "strategy_current_committed_usdt":
                        account["strategy_committed_usdt"],
                    "available_balance_usdt": account["available_balance_usdt"],
                    "next_entry_capacity_required_usdt": "10",
                },
            ),
        )
        for code, symbol_evidence in symbol_checks:
            scope_type, scope_key = scope_for_check(code, context)
            put(code, scope_type, scope_key, symbol_evidence, ttl=True)

    for context in contexts:
        decision = evaluate_live_arm(
            connection,
            context=context,
            now=current,
            require_owner_approval=False,
        )
        if not decision.ready:
            raise P90PrearmEvidenceBlocked(
                "P90 REAL v3 PREARM: durable evidence incomplete "
                + context.strategy_id
                + "/"
                + context.symbol
                + ": "
                + ",".join(decision.failed_codes)
            )
    return {
        "status": "PREARM_TECHNICAL_READY",
        "strategies": 2,
        "accounts": 2,
        "symbols": 5,
        "release_commit": release_commit,
        "source_epoch_id": str(epoch_id),
        "owner_arm_approval_recorded": False,
        "mainnet_gate_enabled": False,
