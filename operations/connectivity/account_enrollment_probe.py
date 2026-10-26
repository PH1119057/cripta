"""Isolated, signed, GET-only Bybit account enrollment diagnostic.

Run only in an explicitly approved systemd credential-mounted unit as cripta.
This command is NOT a Dashboard endpoint, service restart, DB enrollment writer,
or Exchange mutation. It prints stable PASS/BLOCKED tokens, never secrets or UIDs.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg

from bybit_workbench.exchange.bybit.account_enrollment_service import (
    verify_account_enrollment,
)
from bybit_workbench.exchange.bybit.transport import LifecycleAccountReadTransport

_CREDENTIAL_MOUNT_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")
_DB_DSN = "dbname=cripta user=cripta host=/var/run/postgresql"


def _read_mount(config: Mapping[str, str]) -> dict[str, str]:
    """Read an already mounted systemd credential, not an arbitrary file."""
    name = str(config.get("CRIPTA_BYBIT_CREDENTIAL_NAME") or "")
    directory = str(config.get("CREDENTIALS_DIRECTORY") or "")
    if not _CREDENTIAL_MOUNT_NAME.fullmatch(name):
        raise RuntimeError("CREDENTIAL_NAME_INVALID")
    if not directory.startswith("/run/credentials/"):
        raise RuntimeError("SYSTEMD_CREDENTIAL_DIRECTORY_REQUIRED")
    root = Path(directory).resolve(strict=True)
    # Do not accept a symlink leaving the exact credential mount.
    target = root / name
    if not target.is_file() or target.is_symlink() or target.resolve().parent != root:
        raise RuntimeError("MOUNTED_CREDENTIAL_PATH_INVALID")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("MOUNTED_CREDENTIAL_FORMAT_INVALID")
    required = ("api_key", "api_secret", "credential_ref")
    if any(not isinstance(payload.get(k), str) or not payload[k] for k in required):
        raise RuntimeError("MOUNTED_CREDENTIAL_FIELDS_MISSING")
    return {k: payload[k] for k in required}


def verify_mounted_account(
    connection: Any,
    *,
    config: Mapping[str, str],
    load_mounted: Callable[[Mapping[str, str]], Mapping[str, str]],
    signed_get: Callable[[str, str], Mapping[str, object]],
    now: datetime,
) -> tuple[bool, tuple[str, ...]]:
    """Read-only verification. No DB write, no persistent UID or secret output."""
    account = str(config.get("CRIPTA_TRADING_ACCOUNT_ID") or "").strip()
    connector = str(config.get("CRIPTA_ACCOUNT_CONNECTOR_ID") or "").strip()
    proposed_uid = str(config.get("CRIPTA_EXPECTED_PROVIDER_UID") or "").strip()
    kind = str(config.get("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND") or "").strip()
    if not account or not connector or not proposed_uid or kind not in {"MAIN", "SUB"}:
        return False, ("PROBE_EXACT_ACCOUNT_CONFIG_MISSING",)
    expected_name = (
        "bybit-mainnet" if kind == "MAIN" else f"bybit-sub-{account}"
    )
    if config.get("CRIPTA_BYBIT_CREDENTIAL_NAME") != expected_name:
        return False, ("PROBE_WORKER_CREDENTIAL_SCOPE_MISMATCH",)
    try:
        mounted = load_mounted(config)
    except Exception:
        # Do not log paths, exceptions, provider values or secret material.
        return False, ("PROBE_CREDENTIAL_MOUNT_UNAVAILABLE",)
    if not all(mounted.get(k) for k in ("api_key", "api_secret", "credential_ref")):
        return False, ("PROBE_CREDENTIAL_MOUNT_INCOMPLETE",)
    result = verify_account_enrollment(
        connection, trading_account_id=account, account_connector_id=connector,
        proposed_provider_uid=proposed_uid,
        mounted_credential_ref=str(mounted["credential_ref"]),
        signed_account_get=lambda: signed_get(
            str(mounted["api_key"]), str(mounted["api_secret"])
        ),
        now=now,
    )
    return result.ready, result.reasons


def main() -> int:
    """Single-shot private, authenticated GET; stable redacted status only."""
    config = dict(os.environ)
    if config.get("CRIPTA_ACCOUNT_ENROLLMENT_READ_ONLY") != "1":
        print("UID_PROBE=BLOCKED REASON=READ_ONLY_OWNER_GATE_MISSING")
        return 2
    try:
        connection = psycopg.connect(_DB_DSN)
        with connection:
            # Set before every read; never permit an UPDATE from this process.
            connection.execute("SET TRANSACTION READ ONLY")
            def get_info(key: str, secret: str) -> Mapping[str, object]:
                reader = LifecycleAccountReadTransport(
                    api_key=key, api_secret=secret, base_url="https://api.bybit.kz",
                )
                return asyncio.run(reader.get("/v5/user/query-api", {}, authenticated=True))
            ready, reasons = verify_mounted_account(
                connection, config=config, load_mounted=_read_mount,
                signed_get=get_info, now=datetime.now(UTC),
            )
    except Exception:
        print("UID_PROBE=BLOCKED REASON=PROBE_READ_FAILURE")
        return 2
    if ready:
        print("UID_PROBE=PASS")
        return 0
    # Whitelist stable internal failure codes; no external response fragments.
    allowed = tuple(
        reason for reason in reasons
        if reason.isascii() and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", reason)
    )
    print("UID_PROBE=BLOCKED REASONS=" + ",".join(allowed or ("PROBE_FAILED",)))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
