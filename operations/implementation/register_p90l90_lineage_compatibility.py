#!/usr/bin/env python3
"""Issue #243: append-only, operator-triggered P90 source compatibility record.

Actor Unix cripta, DB role cripta, installed verified release only.
READ: existing five-symbol immutable archive, checkpoint state, REAL gates.
WRITE: one exact new row in p90l90_data_lineage_compatibility on --register.
Never updates existing seed/entry snapshots; no orders, files or LIVE.
"""
from __future__ import annotations

import argparse
import os
import pwd
import re
import sys
from pathlib import Path

import psycopg

ROOT = Path("/srv/cripta/runtime/current")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from bybit_workbench.universal_entry.p90l90_data_lineage import (  # noqa: E402
    CONTRACT_VERSION, DATA_LINEAGE_ID, ORIGIN_COMMIT,
    ORIGINAL_BAR_SHA256, ORIGINAL_CHECKPOINT_SHA256,
    inspect_p90_data_lineage, verify_p90_lineage_for_registration,
)

_SHA = re.compile(r"^[0-9a-f]{40}$")


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError("P90_LINEAGE_REGISTRATION_BLOCKED:" + code)


def verify_installed(commit: str) -> None:
    require(os.geteuid() == pwd.getpwnam("cripta").pw_uid, "WRONG_UNIX_ACTOR")
    require(_SHA.fullmatch(commit) is not None, "INVALID_RELEASE_SHA")
    require(ROOT.resolve().name == commit, "INSTALLED_RELEASE_NOT_EXACT")
    require((ROOT / "INSTALLED_COMMIT").read_text().strip() == commit,
            "RELEASE_MARKER_NOT_EXACT")
    require(Path(sys.prefix).resolve() == (ROOT / ".venv").resolve(),
            "WRONG_PYTHON_INTERPRETER")
    require(Path(__file__).resolve() == (
        ROOT / "operations/implementation/register_p90l90_lineage_compatibility.py"
    ).resolve(), "SCRIPT_NOT_INSTALLED")


def check_safety(c: psycopg.Connection) -> None:
    require(c.execute(
        "SELECT session_user,current_user"
    ).fetchone() == ("cripta", "cripta"), "DB_ACTOR")
    require(c.execute(
        "SELECT mode,enabled FROM control.execution_gates "
        "WHERE mode IN ('mainnet','testnet') ORDER BY mode"
    ).fetchall() == [("mainnet", False), ("testnet", False)],
        "REAL_GATE_NOT_OFF")
    require(c.execute(
        "SELECT count(*) FROM strategy_entry.execution_permissions "
        "WHERE enabled"
    ).fetchone() == (0,), "REAL_PERMISSION_EXISTS")
    require(c.execute(
        "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'"
    ).fetchone() == (0,), "LIVE_ARM_EXISTS")
    require(c.execute(
        "SELECT count(*) FROM control.account_live_arm_sessions "
        "WHERE state='ACTIVE'"
    ).fetchone() == (0,), "ACCOUNT_LIVE_ARM_EXISTS")


def perform(commit: str, *, register: bool) -> None:
    verify_installed(commit)
    with psycopg.connect("dbname=cripta user=cripta") as c:
        with c.transaction():
            c.execute(
                "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE, "
                + ("READ WRITE" if register else "READ ONLY")
            )
            check_safety(c)
            # Immutable original archive digest and checkpoint lineage MUST
            # independently match the expected seed; no automatic SHA rename.
            verify_p90_lineage_for_registration(c, loaded_commit=commit)
            if register:
                c.execute(
                    """INSERT INTO
                       strategy_entry.p90l90_data_lineage_compatibility(
                         data_lineage_id,loaded_commit,origin_commit,
                         archive_sha256,seed_checkpoint_sha256,
                         original_bar_count,contract_version
                       ) VALUES (%s,%s,%s,%s,%s,17995,%s)
                       ON CONFLICT (data_lineage_id,loaded_commit) DO NOTHING""",
                    (
                        DATA_LINEAGE_ID, commit, ORIGIN_COMMIT,
                        ORIGINAL_BAR_SHA256, ORIGINAL_CHECKPOINT_SHA256,
                        CONTRACT_VERSION,
                    ),
                )
                exact = inspect_p90_data_lineage(c, loaded_commit=commit)
                require(
                    exact.data_lineage_id == DATA_LINEAGE_ID
                    and exact.origin_commit == ORIGIN_COMMIT,
                    "POSTWRITE_ATTESTATION_NOT_EXACT",
                )
    print(
        ("P90_LINEAGE_REGISTERED=YES" if register
         else "P90_LINEAGE_REGISTRATION_CHECK=PASS")
        + f" DATA_LINEAGE_ID={DATA_LINEAGE_ID} ORIGIN={ORIGIN_COMMIT}"
        + f" LOADED={commit} HISTORICAL_ROWS=17995 CHECKPOINTS=5"
        + " HISTORICAL_WRITES=0 REAL_ORDERS=0 LIVE_ARM=OFF"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-commit", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--register", action="store_true")
    args = parser.parse_args()
    perform(args.release_commit, register=args.register)
