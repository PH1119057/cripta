"""Explicit owner Control operation; signed GET proof, no Exchange mutation.

Actor Unix/DB cripta. Read installed release and exact account/position state;
write only canonical recovery function/audit/latch transaction. FS writes NONE.
Never run automatically from private runtime, preparation, poll or service start.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import psycopg  # noqa: E402

from bybit_workbench.universal_exit.p90l90_real_private_ipc import (  # noqa: E402
    P90PrivateProofClient,
)

SIGNATURE = (
    "control.recover_closed_p90_mutation_safety("
    "text,text,text,text,text,text,timestamptz,timestamptz,text,text)"
)


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("account", "connector", "position", "operator", "request-id"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    if args.operator != "owner" or re.fullmatch(r"[A-Za-z0-9_-]{8,128}", args.request_id) is None:
        raise RuntimeError("MUTATION_RECOVERY_OWNER_REQUEST_REQUIRED")
    runtime = Path("/srv/cripta/runtime/current").resolve()
    release = (runtime / "INSTALLED_COMMIT").read_text().strip()
    if (runtime.name != release or re.fullmatch(r"[a-f0-9]{40}", release) is None
            or Path("/var/lib/cripta/release/INSTALLED_COMMIT").read_text().strip() != release):
        raise RuntimeError("MUTATION_RECOVERY_INSTALLED_RELEASE_UNPROVEN")
    dsn = "dbname=cripta user=cripta host=/var/run/postgresql"
    with psycopg.connect(dsn, options="-cdefault_transaction_read_only=on") as c:
        if c.execute("SELECT current_user,session_user").fetchone() != ("cripta", "cripta"):
            raise RuntimeError("MUTATION_RECOVERY_ACTOR_UNPROVEN")
        if c.execute("SELECT has_function_privilege(current_user,%s,'EXECUTE')",
                     (SIGNATURE,)).fetchone() != (True,):
            raise RuntimeError("MUTATION_RECOVERY_FUNCTION_PERMISSION_MISSING")
        row = c.execute(
            "SELECT blocked_at FROM runtime.account_entry_safety_blocks "
            "WHERE trading_account_id=%s AND account_connector_id=%s",
            (args.account, args.connector),
        ).fetchone()
        if row is None:
            print(json.dumps({"status": "ALREADY_CLEAR", "account": args.account}))
            return
        blocked_at = row[0]
        position = c.execute(
            "SELECT state FROM runtime.position_ownership "
            "WHERE position_id=%s AND trading_account_id=%s AND identity_status='PROVEN'",
            (args.position, args.account),
        ).fetchone()
        if position != ("CLOSED",):
            raise RuntimeError("MUTATION_RECOVERY_CLOSED_LINEAGE_UNPROVEN")
    proof = P90PrivateProofClient(
        account=args.account, connector=args.connector, timeout_seconds=15,
    ).read_flat_account()
    if (proof.trading_account_id != args.account
            or proof.source_ref != "bybit:signed-flat-linear-usdt:exact-account"
            or proof.open_positions != 0 or proof.open_orders != 0
            or proof.pending_commands != 0
            or not 0 <= (datetime.now(UTC)-proof.observed_at).total_seconds() <= 15):
        raise RuntimeError("MUTATION_RECOVERY_SIGNED_FLAT_UNPROVEN")
    with psycopg.connect(dsn) as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        result = c.execute(
            "SELECT control.recover_closed_p90_mutation_safety("
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (args.account, args.connector, release, args.position,
             proof.private_generation_id, proof.signed_uid, blocked_at,
             proof.observed_at, "owner", args.request_id),
        ).fetchone()
        if result is None:
            raise RuntimeError("MUTATION_RECOVERY_RESULT_MISSING")
        response = result[0]
    print(json.dumps({"result": response, "release": release,
                      "exchange_mutation": False, "owner_arm": False}, default=str))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Print only internal diagnostic codes, never DSN/credentials/signed data.
        first = str(exc).splitlines()[0] if str(exc) else ""
        code = first if re.fullmatch(r"MUTATION_RECOVERY_[A-Z0-9_]+", first) else (
            "MUTATION_RECOVERY_UNVERIFIED"
        )
        print(json.dumps({"status": "BLOCKED", "code": code}), file=sys.stderr)
        raise SystemExit(2) from None
