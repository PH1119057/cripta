from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

DB_DSN = os.environ.get(
    "CRIPTA_DATABASE_DSN",
    "dbname=cripta user=cripta host=/var/run/postgresql application_name=exit-migration-readiness",
)


@dataclass(frozen=True, slots=True)
class ExitMigrationReadiness:
    active_exit_plans: int
    active_exit_plans_with_executable_rules: int
    active_exit_plans_without_executable_rules: tuple[str, ...]
    open_legacy_positions: int
    open_universal_positions: int
    universal_exit_claims: int
    universal_shadow_evaluations: int
    open_lifecycle_faults: int
    mainnet_gate_enabled: bool
    real_execution_permissions: int
    shadow_comparison_state: str
    technical_blockers: tuple[str, ...]
    owner_decision_required: bool
    live_cutover_authorized: bool


def _has_executable_rules(plan_json: object) -> bool:
    if not isinstance(plan_json, dict):
        return False
    exit_policy = plan_json.get("exit_policy")
    if not isinstance(exit_policy, dict):
        return False
    rules = exit_policy.get("rules")
    return isinstance(rules, list) and len(rules) > 0


def inspect_readiness(connection: psycopg.Connection[Any]) -> ExitMigrationReadiness:
    active_rows = connection.execute(
        """SELECT a.strategy_id,a.strategy_version,x.exit_plan_fingerprint,x.plan_json
             FROM strategy_entry.strategy_activations a
             JOIN strategy_entry.exit_plans x
               ON x.strategy_id=a.strategy_id
              AND x.strategy_version=a.strategy_version
              AND x.strategy_config_fingerprint=a.strategy_config_fingerprint
            WHERE a.enabled=true
            ORDER BY a.strategy_id,a.strategy_version"""
    ).fetchall()

    with_rules: list[str] = []
    without_rules: list[str] = []
    for row in active_rows:
        identity = f"{row['strategy_id']}:{row['strategy_version']}:{row['exit_plan_fingerprint']}"
        if _has_executable_rules(row["plan_json"]):
            with_rules.append(identity)
        else:
            without_rules.append(identity)

    position_counts = connection.execute(
        """SELECT
              count(*) FILTER (
                  WHERE bot_instance_id='universal-entry'
                    AND state IN ('OPEN','RECONCILIATION_REQUIRED')
              ) AS universal_count,
              count(*) FILTER (
                  WHERE bot_instance_id IS DISTINCT FROM 'universal-entry'
                    AND state IN ('OPEN','RECONCILIATION_REQUIRED')
              ) AS legacy_count
           FROM runtime.position_ownership"""
    ).fetchone()
    if position_counts is None:
        raise RuntimeError("position ownership counts unavailable")

    open_universal = int(position_counts["universal_count"])
    open_legacy = int(position_counts["legacy_count"])

    claims_row = connection.execute(
        """SELECT count(*) AS n
             FROM runtime.position_exit_claims c
             JOIN runtime.position_ownership p
               ON p.position_id=c.strategy_position_id
            WHERE p.bot_instance_id='universal-entry'
              AND p.state IN ('OPEN','RECONCILIATION_REQUIRED')
              AND c.status='CLAIMED'"""
    ).fetchone()
    if claims_row is None:
        raise RuntimeError("Universal Exit claim count unavailable")
    claims = int(claims_row["n"])

    shadow_row = connection.execute(
        """SELECT count(*) AS n
             FROM strategy_exit.shadow_evaluations e
             JOIN runtime.position_ownership p
               ON p.position_id=e.strategy_position_id
            WHERE p.bot_instance_id='universal-entry'"""
    ).fetchone()
    if shadow_row is None:
        raise RuntimeError("Universal Exit shadow evaluation count unavailable")
    shadow_evaluations = int(shadow_row["n"])

    faults_row = connection.execute(
        "SELECT count(*) AS n FROM runtime.lifecycle_faults WHERE state='OPEN'"
    ).fetchone()
    if faults_row is None:
        raise RuntimeError("lifecycle fault count unavailable")
    open_faults = int(faults_row["n"])
    gate_row = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    if gate_row is None:
        raise RuntimeError("mainnet execution gate missing")
    mainnet_enabled = bool(gate_row["enabled"])

    permissions_table = connection.execute(
        "SELECT to_regclass('control.strategy_execution_permissions') AS rel"
    ).fetchone()
    if permissions_table is None or permissions_table["rel"] is None:
        real_permissions = 0
    else:
        permissions_row = connection.execute(
            "SELECT count(*) AS n FROM control.strategy_execution_permissions"
        ).fetchone()
        if permissions_row is None:
            raise RuntimeError("strategy execution permission count unavailable")
        real_permissions = int(permissions_row["n"])

    blockers: list[str] = []
    if without_rules:
        blockers.append("ACTIVE_EXIT_PLAN_WITHOUT_EXECUTABLE_RULES")
    if open_universal == 0:
        comparison_state = "NO_LIVE_UNIVERSAL_SAMPLE"
        blockers.append("NO_LIVE_UNIVERSAL_SHADOW_SAMPLE")
    elif shadow_evaluations == 0:
        comparison_state = "LIVE_SAMPLE_WITHOUT_SHADOW_EVALUATION"
        blockers.append("UNIVERSAL_POSITION_WITHOUT_SHADOW_EXIT_EVIDENCE")
    else:
        comparison_state = "LIVE_SHADOW_EVIDENCE_PRESENT"
    if open_universal != claims:
        blockers.append("UNIVERSAL_EXIT_CLAIM_COVERAGE_INCOMPLETE")
    if open_faults:
        blockers.append("OPEN_LIFECYCLE_FAULTS")
    if mainnet_enabled:
        blockers.append("MAINNET_GATE_MUST_REMAIN_CLOSED_DURING_READINESS")
    if real_permissions:
        blockers.append("REAL_EXECUTION_PERMISSIONS_PRESENT_DURING_READINESS")

    return ExitMigrationReadiness(
        active_exit_plans=len(active_rows),
        active_exit_plans_with_executable_rules=len(with_rules),
        active_exit_plans_without_executable_rules=tuple(without_rules),
        open_legacy_positions=open_legacy,
        open_universal_positions=open_universal,
        universal_exit_claims=claims,
        universal_shadow_evaluations=shadow_evaluations,
        open_lifecycle_faults=open_faults,
        mainnet_gate_enabled=mainnet_enabled,
        real_execution_permissions=real_permissions,
        shadow_comparison_state=comparison_state,
        technical_blockers=tuple(blockers),
        owner_decision_required=True,
        live_cutover_authorized=False,
    )


def main() -> int:
    with psycopg.connect(DB_DSN, row_factory=dict_row) as connection:
        report = inspect_readiness(connection)
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
