from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

PROFILE_BY_DIRECTION = {
    "long": "M3_V1_LONG_ENTRY",
    "short": "M3_V1_SHORT_ENTRY",
}


def prepare_database(connection: Any) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS monitoring.entry_dispatcher_shadow_decisions(
        chain_id text PRIMARY KEY,
        m3_setup_id text NOT NULL,
        symbol text NOT NULL,
        direction text NOT NULL,
        signal_at timestamptz NOT NULL,
        baseline_decision text NOT NULL,
        shadow_dispatcher_decision text NOT NULL,
        consumed_context_type text NOT NULL CHECK(consumed_context_type='CONSUMED_CONTEXT'),
        consumed_dispatcher_assessment_id text,
        consumed_mayak_snapshot_id text,
        assessment_observed_at timestamptz,
        strategy_decision_at timestamptz NOT NULL,
        profile_id text NOT NULL,
        profile_version text,
        data_quality text,
        coverage jsonb,
        decision_reason_ru text NOT NULL,
        counterfactual_state text NOT NULL DEFAULT 'PENDING',
        counterfactual_path jsonb NOT NULL DEFAULT '{}'::jsonb,
        trading_effect text NOT NULL CHECK(trading_effect='NONE'),
        payload jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS entry_dispatcher_shadow_signal_at "
        "ON monitoring.entry_dispatcher_shadow_decisions(signal_at DESC)"
    )
    connection.commit()


def consume_for_signal(
    connection: Any,
    *,
    signal_id: str,
    symbol: str,
    direction: str,
    signal_at: datetime,
) -> dict[str, Any]:
    normalized_direction = direction.lower()
    profile_id = PROFILE_BY_DIRECTION[normalized_direction]
    row = connection.execute(
        """SELECT assessment_id,snapshot_id,mayak_snapshot_id,observed_at,
                  profile_version,status,data_quality,coverage,payload
           FROM strategy_dispatcher.assessments
           WHERE profile_id=%s AND observed_at<=%s
           ORDER BY observed_at DESC,stored_at DESC LIMIT 1""",
        (profile_id, signal_at),
    ).fetchone()
    decision_at = datetime.now(UTC)
    assessment = None
    if row is not None:
        assessment = {
            "assessment_id": row[0],
            "snapshot_id": row[1],
            "mayak_snapshot_id": row[2] or row[1],
            "observed_at": row[3],
            "profile_version": row[4],
            "status": row[5],
            "data_quality": row[6],
            "coverage": row[7],
            "payload": row[8],
        }
    result = build_shadow_decision(
        signal_id=signal_id,
        symbol=symbol,
        direction=normalized_direction,
        signal_at=signal_at,
        strategy_decision_at=decision_at,
        profile_id=profile_id,
        assessment=assessment,
    )
    connection.execute(
        """INSERT INTO monitoring.entry_dispatcher_shadow_decisions(
            chain_id,m3_setup_id,symbol,direction,signal_at,baseline_decision,
            shadow_dispatcher_decision,consumed_context_type,
            consumed_dispatcher_assessment_id,consumed_mayak_snapshot_id,
            assessment_observed_at,strategy_decision_at,profile_id,profile_version,
            data_quality,coverage,decision_reason_ru,trading_effect,payload)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'NONE',%s)
            ON CONFLICT(chain_id) DO NOTHING""",
        (
            result["chain_id"], signal_id, symbol, normalized_direction, signal_at,
            result["baseline_decision"], result["shadow_dispatcher_decision"],
            "CONSUMED_CONTEXT", result["consumed_dispatcher_assessment_id"],
            result["consumed_mayak_snapshot_id"], result["assessment_observed_at"],
            decision_at, profile_id, result["profile_version"], result["data_quality"],
            json.dumps(result["coverage"], ensure_ascii=False),
            result["decision_reason_ru"], json.dumps(result, ensure_ascii=False, default=str),
        ),
    )
    return result


def build_shadow_decision(
    *,
    signal_id: str,
    symbol: str,
    direction: str,
    signal_at: datetime,
    strategy_decision_at: datetime,
    profile_id: str,
    assessment: dict[str, Any] | None,
) -> dict[str, Any]:
    if signal_at.tzinfo is None or strategy_decision_at.tzinfo is None:
        raise ValueError("chain timestamps must be timezone-aware")
    if assessment is None:
        shadow_decision = "NO_CONTEXT"
        reason = "До сигнала нет причинно допустимой оценки Диспетчера"
        assessment_id = mayak_id = assessment_at = profile_version = data_quality = None
        coverage = None
    else:
        assessment_at = assessment["observed_at"]
        if assessment_at.tzinfo is None or assessment_at > signal_at:
            raise ValueError("future Dispatcher assessment cannot be consumed")
        assessment_id = str(assessment["assessment_id"])
        mayak_id = str(assessment["mayak_snapshot_id"])
        profile_version = str(assessment["profile_version"])
        data_quality = assessment.get("data_quality")
        coverage = assessment.get("coverage")
        shadow_decision = str(assessment["status"])
        reason = (
            "Контекст потреблён причинно; пороги профиля не исследованы"
            if shadow_decision == "RESEARCH_REQUIRED"
            else "Контекст Диспетчера потреблён в SHADOW без торгового влияния"
        )
    chain_id = hashlib.sha256(f"{signal_id}:dispatcher-shadow-v1".encode()).hexdigest()
    return {
        "chain_id": chain_id,
        "m3_setup_id": signal_id,
        "symbol": symbol,
        "direction": direction,
        "signal_at": signal_at.isoformat(),
        "baseline_decision": "ALLOW_BASELINE",
        "shadow_dispatcher_decision": shadow_decision,
        "context_type": "CONSUMED_CONTEXT",
        "consumed_dispatcher_assessment_id": assessment_id,
        "consumed_mayak_snapshot_id": mayak_id,
        "assessment_observed_at": assessment_at.isoformat() if assessment_at else None,
        "strategy_decision_at": strategy_decision_at.isoformat(),
        "profile_id": profile_id,
        "profile_version": profile_version,
        "data_quality": data_quality,
        "coverage": coverage,
        "decision_reason_ru": reason,
        "counterfactual_state": "PENDING",
        "counterfactual_path": {},
        "trading_effect": "NONE",
    }
