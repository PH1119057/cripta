#!/usr/bin/env python3
"""P90L90 REAL v3 source-only PREARM operator rail; NEVER OWNER-ARMS LIVE.

SCRIPT: operations/implementation/control_p90l90_real_v3_prearm.py
EFFECTIVE_ACTOR: Unix cripta; PostgreSQL session/current_user cripta.
READ_PATHS: exact installed runtime, source checkout Git identity, production
            Strategy/Identity/Control/runtime read-model and service statuses.
WRITE_PATHS: none (filesystem).
DB_WRITES:
  --enable   exactly the narrow two-Strategy PREARM activation/binding transition;
  --evidence only durable technical control.live_arm_evidence PASS rows after
             current source/private/account/lifecycle checks;
  --disable  exactly the narrow two-Strategy PREARM disable transition.
CREATE/RENAME/DELETE: none.
EXCHANGE_MUTATION: none.
LIVE_ARM / execution permission / mainnet gate mutation: forbidden here.
OWNER ARM remains a separate authenticated Dashboard action after evidence.
"""
from __future__ import annotations

import argparse
import os
import pwd
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ROOT = Path("/srv/cripta/runtime/current")
SOURCE = Path("/srv/cripta/source_checkout")
SHA = re.compile(r"[0-9a-f]{40}\Z")
SCRIPT_REL = Path("operations/implementation/control_p90l90_real_v3_prearm.py")

if not (ROOT / "src").is_dir():
    raise SystemExit("P90_REAL_V3_PREARM_INSTALLED_SOURCE_MISSING")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# isort: off
from bybit_workbench.p90l90_real_arm_control import (  # noqa: E402
    prearm_p90l90_real_v3,
)
from bybit_workbench.p90l90_real_prearm_evidence import (  # noqa: E402
    prepare_p90l90_real_v3_prearm_evidence,
)
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (  # noqa: E402
    VERSION,
)
# isort: on


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise RuntimeError("P90_REAL_V3_PREARM_BLOCKED:" + reason)


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            args, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("P90_REAL_V3_PREARM_BLOCKED:GIT_IDENTITY") from exc


def installed_identity() -> str:
    release = ROOT.resolve()
    commit = release.name
    require(os.geteuid() == pwd.getpwnam("cripta").pw_uid, "UNIX_ACTOR")
    require(SHA.fullmatch(commit) is not None, "INSTALLED_COMMIT_INVALID")
    require(
        (ROOT / "INSTALLED_COMMIT").read_text(encoding="utf-8").strip() == commit,
        "INSTALLED_COMMIT_MARKER_DRIFT",
    )
    require(
        Path(sys.prefix).resolve() == (release / ".venv").resolve(),
        "WRONG_PINNED_VENV",
    )
    require(
        Path(__file__).resolve() == (release / SCRIPT_REL).resolve(),
        "INSTALLED_SCRIPT_IDENTITY",
    )
    source = _git("git", "-C", str(SOURCE), "rev-parse", "HEAD")
    remote = _git(
        "git", "-C", str(SOURCE), "ls-remote", "origin", "refs/heads/main"
    ).split()[0]
    dirty = _git("git", "-C", str(SOURCE), "status", "--porcelain=v1")
    require(not dirty, "SOURCE_WORKTREE_DIRTY")
    require(source == remote == commit, "REMOTE_SOURCE_INSTALLED_IDENTITY_DRIFT")
    return commit


def state(connection: psycopg.Connection) -> dict[str, object]:
    require(
        connection.execute("SELECT session_user,current_user").fetchone()
        == ("cripta", "cripta"),
        "DB_ACTOR",
    )
    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    permissions = connection.execute(
        "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true"
    ).fetchone()
    live = connection.execute(
        "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'"
    ).fetchone()
    account_live = connection.execute(
        "SELECT count(*) FROM control.account_live_arm_sessions WHERE state='ACTIVE'"
    ).fetchone()
    rows = connection.execute(
        """SELECT a.strategy_id,a.enabled,b.binding_state,b.trading_account_id
             FROM strategy_entry.strategy_activations a
             JOIN strategy_entry.strategy_execution_bindings b
               ON b.strategy_activation_id=a.activation_id
            WHERE a.strategy_version=%s
            ORDER BY a.strategy_id""",
        (VERSION,),
    ).fetchall()
    require(gate == (False,), "MAINNET_GATE_NOT_CLOSED")
    require(permissions == (0,), "EXECUTION_PERMISSION_ACTIVE")
    require(live == (0,), "LIVE_ARM_SESSION_ACTIVE")
    require(account_live == (0,), "ACCOUNT_LIVE_ARM_SESSION_ACTIVE")
    require(len(rows) == 2, "EXACT_TWO_V3_ACTIVATION_BINDINGS_REQUIRED")
    return {
        "gate_enabled": False,
        "execution_permissions": 0,
        "live_arm_sessions": 0,
        "account_live_arm_sessions": 0,
        "strategies": [
            {
                "strategy_id": str(row[0]),
                "enabled": bool(row[1]),
                "binding_state": str(row[2]),
                "trading_account_id": str(row[3]),
            }
            for row in rows
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("check", "enable", "evidence", "disable")
    )
    parser.add_argument("--operator")
    parser.add_argument("--request-id")
    args = parser.parse_args()

    commit = installed_identity()
    mutating_prearm = args.action in {"enable", "disable"}
    if mutating_prearm:
        require(bool(args.operator and args.operator.strip()), "OPERATOR_REQUIRED")
        require(
            bool(args.request_id and len(args.request_id.strip()) >= 8),
            "REQUEST_ID_REQUIRED",
        )
    else:
        require(args.operator is None and args.request_id is None, "UNEXPECTED_OWNER_ARGS")

    with psycopg.connect("dbname=cripta user=cripta") as connection:
        if args.action == "check":
            connection.execute("BEGIN READ ONLY")
            current = state(connection)
            connection.rollback()
            print(
                "P90_REAL_V3_PREARM_CHECK=PASS "
                f"INSTALLED_COMMIT={commit} STATE={current}"
            )
            return

        if args.action == "evidence":
            before = state(connection)
            require(
                all(
                    item["enabled"] is True and item["binding_state"] == "ACTIVE"
                    for item in before["strategies"]
                ),
                "SOURCE_ONLY_PREARM_NOT_ACTIVE",
            )
            result = prepare_p90l90_real_v3_prearm_evidence(
                connection,
                release_commit=commit,
                now=datetime.now(UTC),
            )
            print(
                "P90_REAL_V3_PREARM_EVIDENCE=PASS "
                f"INSTALLED_COMMIT={commit} RESULT={result}"
            )
            return

        operator = str(args.operator)
        request_id = str(args.request_id)
        result = prearm_p90l90_real_v3(
            connection,
            release_commit=commit,
            now=datetime.now(UTC),
            operator=operator,
            request_id=request_id,
            enabled=args.action == "enable",
        )
        after = state(connection)
        expected = args.action == "enable"
        require(
            all(
                item["enabled"] is expected
                and item["binding_state"] == ("ACTIVE" if expected else "INACTIVE")
                for item in after["strategies"]
            ),
            "POSTWRITE_PREARM_STATE_MISMATCH",
        )
        print(
            f"P90_REAL_V3_PREARM_{args.action.upper()}=PASS "
            f"INSTALLED_COMMIT={commit} RESULT={result}"
        )


if __name__ == "__main__":
    main()
