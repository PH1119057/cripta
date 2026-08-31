#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

import psycopg
from bybit_workbench.strategy_dispatcher.service import PassiveDispatcherService

DSN = os.environ.get("CRIPTA_DSN", "dbname=cripta user=cripta host=/var/run/postgresql")


def prepare_database() -> None:
    with psycopg.connect(DSN) as db:
        db.execute("CREATE SCHEMA IF NOT EXISTS strategy_dispatcher")
        db.execute("""CREATE TABLE IF NOT EXISTS strategy_dispatcher.runs(
            snapshot_id text PRIMARY KEY,
            observed_at timestamptz NOT NULL,
            mayak_version text NOT NULL,
            architecture_version text NOT NULL,
            data_quality text NOT NULL,
            service_version text NOT NULL,
            profile_count integer NOT NULL,
            trading_effect text NOT NULL CHECK(trading_effect='NONE'),
            payload jsonb NOT NULL,
            stored_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
        db.execute(
            "CREATE INDEX IF NOT EXISTS strategy_dispatcher_runs_at "
            "ON strategy_dispatcher.runs(observed_at DESC)"
        )
        db.execute("""CREATE TABLE IF NOT EXISTS strategy_dispatcher.assessments(
            assessment_id text PRIMARY KEY,
            snapshot_id text NOT NULL REFERENCES strategy_dispatcher.runs(snapshot_id),
            observed_at timestamptz NOT NULL,
            dispatcher_version text NOT NULL,
            profile_id text NOT NULL,
            profile_version text NOT NULL,
            suitability double precision,
            confidence double precision NOT NULL,
            status text NOT NULL,
            payload jsonb NOT NULL,
            stored_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
        db.execute(
            "ALTER TABLE strategy_dispatcher.assessments "
            "ADD COLUMN IF NOT EXISTS mayak_snapshot_id text"
        )
        db.execute(
            "ALTER TABLE strategy_dispatcher.assessments "
            "ADD COLUMN IF NOT EXISTS created_at timestamptz"
        )
        db.execute(
            "ALTER TABLE strategy_dispatcher.assessments "
            "ADD COLUMN IF NOT EXISTS data_quality text"
        )
        db.execute(
            "ALTER TABLE strategy_dispatcher.assessments "
            "ADD COLUMN IF NOT EXISTS coverage jsonb"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS strategy_dispatcher_assessments_at "
            "ON strategy_dispatcher.assessments(observed_at DESC)"
        )
        db.commit()


def persist_envelope(envelope: dict[str, Any]) -> None:
    snapshot = envelope["snapshot"]
    snapshot_id = str(snapshot["snapshot_id"])
    with psycopg.connect(DSN) as db:
        db.execute(
            """INSERT INTO strategy_dispatcher.runs(
                snapshot_id,observed_at,mayak_version,architecture_version,data_quality,
                service_version,profile_count,trading_effect,payload)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(snapshot_id) DO NOTHING""",
            (
                snapshot_id,
                snapshot["observed_at"],
                snapshot["mayak_version"],
                snapshot["architecture_version"],
                snapshot["data_quality"],
                envelope["service_version"],
                envelope["profile_count"],
                envelope["trading_effect"],
                json.dumps(envelope, ensure_ascii=False),
            ),
        )
        for assessment in envelope["assessments"]:
            db.execute(
                """INSERT INTO strategy_dispatcher.assessments(
                    assessment_id,snapshot_id,observed_at,dispatcher_version,
                    profile_id,profile_version,suitability,confidence,status,payload,
                    mayak_snapshot_id,created_at,data_quality,coverage)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(assessment_id) DO NOTHING""",
                (
                    assessment["assessment_id"],
                    snapshot_id,
                    assessment["observed_at"],
                    assessment["dispatcher_version"],
                    assessment["profile_id"],
                    assessment["profile_version"],
                    assessment["suitability"],
                    assessment["confidence"],
                    assessment["status"],
                    json.dumps(assessment, ensure_ascii=False),
                    assessment.get("mayak_snapshot_id"),
                    assessment.get("created_at"),
                    assessment.get("data_quality"),
                    json.dumps(assessment.get("coverage"), ensure_ascii=False),
                ),
            )
        db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Пассивный runtime Диспетчера стратегий")
    parser.add_argument(
        "--mayak-status", type=Path, default=Path("/var/lib/cripta/mayak_v2/status.json")
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=Path("/srv/cripta/config/strategy_dispatcher/profiles"),
    )
    parser.add_argument(
        "--state-root", type=Path, default=Path("/var/lib/cripta/strategy_dispatcher")
    )
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        raise ValueError("poll-seconds must be positive")
    service = PassiveDispatcherService(
        mayak_status_path=args.mayak_status,
        profile_dir=args.profile_dir,
        state_root=args.state_root,
    )
    prepare_database()
    stopped = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped:
        envelope = service.run_once()
        persist_envelope(envelope)
        end = time.monotonic() + args.poll_seconds
        while not stopped and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
