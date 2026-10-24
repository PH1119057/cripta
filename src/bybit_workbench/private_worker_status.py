"""Account-scoped private runtime state paths (R05), no filesystem writes.

The current MAIN runtime retains its historical status.json reader contract.
Each explicitly provisioned SUB worker has a separate status and temporary
filename so two services cannot overwrite one another's liveness evidence.
"""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_STATUS_DIR = Path("/var/lib/cripta/private_runtime")
DEFAULT_MAIN_BOT_INSTANCE = "m3-mainnet-primary"


def private_worker_status_path(
    *, trading_account_id: str, account_kind: str,
    directory: Path = DEFAULT_STATUS_DIR,
) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", trading_account_id):
        raise ValueError("PRIVATE_WORKER_TRADING_ACCOUNT_ID_INVALID")
    if account_kind == "MAIN":
        return directory / "status.json"
    if account_kind == "SUB":
        return directory / f"status-{trading_account_id}.json"
    raise ValueError("PRIVATE_WORKER_ACCOUNT_KIND_INVALID")


def assert_sub_worker_isolation(
    *, account_kind: str, bot_instance_id: str, credential_name: str,
) -> None:
    if account_kind != "SUB":
        return
    if (
        not bot_instance_id
        or bot_instance_id == DEFAULT_MAIN_BOT_INSTANCE
        or not credential_name
        or credential_name == "bybit-mainnet"
    ):
        raise ValueError("SUB_PRIVATE_WORKER_ISOLATION_NOT_CONFIGURED")
