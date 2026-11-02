from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90PrivateProofClient,
)

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_REQUEST = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
_ALLOWED = {
    (
        "ta-2122c81ecff14f3b85b102db605212f7",
        "ac-ed73de20410548d6841aeafa5f196f70",
    ),
    (
        "ta-853ac5fcd58185697396d33cd57459fa",
        "ac-ec57819f875f1bfd929a382d6dc8a125",
    ),
}


def _installed_commit() -> str:
    runtime = Path("/srv/cripta/runtime/current/INSTALLED_COMMIT").read_text(
        encoding="utf-8"
    ).strip().lower()
    state = Path("/var/lib/cripta/release/INSTALLED_COMMIT").read_text(
        encoding="utf-8"
    ).strip().lower()
    if runtime != state or _SHA.fullmatch(runtime) is None:
        raise RuntimeError("P90_ACCOUNT_WS_RECOVERY_RELEASE_IDENTITY_UNVERIFIED")
    return runtime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True)
    parser.add_argument("--connector", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--request-id", required=True)
    args = parser.parse_args()

    if args.operator != "owner":
        raise RuntimeError("P90_ACCOUNT_WS_RECOVERY_OWNER_REQUIRED")
    if _REQUEST.fullmatch(args.request_id) is None:
        raise RuntimeError("P90_ACCOUNT_WS_RECOVERY_REQUEST_ID_INVALID")
    if (args.account, args.connector) not in _ALLOWED:
        raise RuntimeError("P90_ACCOUNT_WS_RECOVERY_ACCOUNT_SCOPE_INVALID")

    release_commit = _installed_commit()
    proof = P90PrivateProofClient(
        account=args.account,
        connector=args.connector,
        timeout_seconds=15.0,
    ).read_flat_account()
    recovered_at = datetime.now(UTC)
    if (
        proof.trading_account_id != args.account
        or proof.source_ref != "bybit:signed-flat-linear-usdt:exact-account"
        or proof.open_positions != 0
        or proof.open_orders != 0
        or proof.pending_commands != 0
        or proof.observed_at.tzinfo is None
        or proof.observed_at.utcoffset() is None
        or not 0 <= (recovered_at - proof.observed_at).total_seconds() <= 15
    ):
        raise RuntimeError("P90_ACCOUNT_WS_RECOVERY_PRIVATE_PROOF_INVALID")

    with psycopg.connect(
        "dbname=cripta user=cripta host=/var/run/postgresql "
        "application_name=p90-account-ws-safety-recovery"
    ) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        row = connection.execute(
            """SELECT control.recover_p90_account_ws_safety(
                   %s,%s,%s,%s,%s,%s
               )""",
            (
                args.account,
                args.connector,
                release_commit,
                proof.private_generation_id,
                proof.signed_uid,
                recovered_at,
            ),
        ).fetchone()
        if row != (args.account,):
            raise RuntimeError("P90_ACCOUNT_WS_RECOVERY_RESULT_INVALID")
        connection.commit()

    print(json.dumps({
        "status": "RECOVERED",
        "request_id": args.request_id,
        "operator": args.operator,
        "release_commit": release_commit,
        "trading_account_id": args.account,
        "account_connector_id": args.connector,
        "private_generation_id": proof.private_generation_id,
        "signed_uid": proof.signed_uid,
        "recovered_at": recovered_at.isoformat(),
        "exchange_mutation": False,
        "gate_mutation": False,
        "owner_arm": False,
    }, sort_keys=True))


# Safe, bounded error vocabulary; never expose raw signed Exchange or DB details.
_DIAGNOSTIC_CODES = frozenset({
    "P90_ACCOUNT_WS_RECOVERY_OWNER_REQUIRED",
    "P90_ACCOUNT_WS_RECOVERY_REQUEST_ID_INVALID",
    "P90_ACCOUNT_WS_RECOVERY_ACCOUNT_SCOPE_INVALID",
    "P90_ACCOUNT_WS_RECOVERY_RELEASE_IDENTITY_UNVERIFIED",
    "P90_ACCOUNT_WS_RECOVERY_PRIVATE_PROOF_INVALID",
    "P90_ACCOUNT_WS_RECOVERY_RESULT_INVALID",
})


def _main_with_safe_error() -> None:
    try:
        main()
    except Exception as exc:
        reason = str(exc)
        if reason in _DIAGNOSTIC_CODES:
            code = reason
        elif isinstance(exc, psycopg.Error):
            code = "P90_ACCOUNT_WS_RECOVERY_DB_REJECTED"
        else:
            code = "P90_ACCOUNT_WS_RECOVERY_PROOF_OR_RUNTIME_ERROR"
        print("P90_ACCOUNT_WS_RECOVERY_ERROR=" + code, file=sys.stderr)
        raise SystemExit(20) from None


if __name__ == "__main__":
    _main_with_safe_error()
