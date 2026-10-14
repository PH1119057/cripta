"""Read-only historical audit: Entry decisions and proof-gap durations.

Reports observed evidence, not verdicts that a block was unjustified.
Does not write to Exchange, database or filesystem.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any


def classify_protection(state: str) -> str:
    return {
        "UNKNOWN": "PROOF_UNAVAILABLE",
        "NOT_VERIFIED": "PROTECTION_NOT_CONFIRMED",
        "VERIFIED": "CONFIRMED_AT_OBSERVATION",
    }.get(state, "UNCLASSIFIED")


def summarize(
    decisions: list[tuple[str, str]],
    faults: list[tuple[Any, ...]],
    *,
    since: datetime,
    now: datetime,
) -> dict[str, Any]:
    by_decision: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for code, reason in decisions:
        by_decision[code] = by_decision.get(code, 0) + 1
        key = code + ": " + reason
        by_reason[key] = by_reason.get(key, 0) + 1
    by_proof: dict[str, dict[str, float | int]] = {}
    records = []
    for fid, code, pos, start, end, payload in faults:
        evidence = payload if isinstance(payload, dict) else {}
        classification = classify_protection(str(evidence.get("protection_state") or ""))
        duration = max(0.0, (min(end or now, now) - max(start, since)).total_seconds())
        group = by_proof.setdefault(
            classification, {"count": 0, "duration_seconds": 0.0, "still_open": 0}
        )
        group["count"] += 1
        group["duration_seconds"] += duration
        if end is None:
            group["still_open"] += 1
        records.append({"fault_id": fid, "fault_code": code, "position_id": pos,
                        "classification": classification, "reason": evidence.get("reason"),
                        "start": start.isoformat(), "end": end.isoformat() if end else None,
                        "window_duration_seconds": duration})
    return {"window_start": since.isoformat(), "window_end": now.isoformat(),
            "entry_counts": by_decision, "entry_reason_counts": by_reason,
            "protection_proof": by_proof, "incidents": records,
            "caveats": [
                "Blocked does not establish false positive without historical LIVE-arm evidence.",
                        "UNKNOWN does not establish missing Exchange stop.",
                        "RESOLVED does not establish continuous protection."]}


def collect(connection: Any, *, since: datetime, now: datetime) -> dict[str, Any]:
    # Explicitly read-only transaction; no persistent DB modifications.
    with connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        decisions = connection.execute(
            """SELECT decision_code,reason FROM strategy_entry.entry_decisions
               WHERE decided_at >= %s AND decided_at <= %s""", (since, now)
        ).fetchall()
        faults = connection.execute(
            """SELECT fault_id,fault_code,strategy_position_id,detected_at,resolved_at,payload
                 FROM runtime.lifecycle_faults
                WHERE detected_at <= %s AND (resolved_at IS NULL OR resolved_at >= %s)
                  AND fault_code IN ('POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION',
                                     'POSITION_WITHOUT_EXIT_OWNER')
                ORDER BY detected_at,fault_id""", (now, since)
        ).fetchall()
    return summarize(decisions, faults, since=since, now=now)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    if args.hours < 1 or args.hours > 720:
        parser.error("--hours must be 1..720")
    import psycopg
    now = datetime.now(UTC)
    since = now - timedelta(hours=args.hours)
    dsn = os.environ.get(
        "CRIPTA_DATABASE_DSN",
        "dbname=cripta user=cripta host=/var/run/postgresql",
    )
    with psycopg.connect(dsn, autocommit=True) as connection:
        print(json.dumps(collect(connection, since=since, now=now), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
