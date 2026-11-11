"""Read-only cripta-actor preflight for the protected R06 mode switch.

Never opens a public WS/REST decision reader, mutates Strategy/Execution,
or substitutes local account-state status for signed MAIN/SUB FLAT proof.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from operations.monitoring.p90l90_real_source_bootstrap import (
    preflight_p90_real_private_flat_source,
)
from operations.monitoring.r06_entry_publication_shadow_probe import (
    SOURCE_CHECKOUT,
    verify_shadow_read,
)
from operations.monitoring.r06_fresh_entry_boundary import (
    _require_no_exposure_or_requests,
)
from operations.monitoring.r06_pg_only_entry import _bundles

INSTALLED = Path("/srv/cripta/runtime/current/INSTALLED_COMMIT")
STATUS = Path("/var/lib/cripta/universal_entry_observer/status.json")
DSN = "dbname=cripta user=cripta host=/var/run/postgresql"
P90_IDS = frozenset((
    "p90l90_legacy_main_ltc_inj",
    "p90l90_legacy_sub_dot_arb_apt",
))


class R06SwitchPreflightBlocked(RuntimeError):
    """Current physical proof is incomplete; never authorize a mode switch."""


def current_release() -> str:
    sha = INSTALLED.read_text(encoding="utf-8").strip()
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise R06SwitchPreflightBlocked("R06_RELEASE_IDENTITY_INVALID")
    return sha


def _require_after(conn: psycopg.Connection, sha: str, source: str) -> dict[str, object]:
    rows = conn.execute(
        """SELECT strategy_id,strategy_version,source_session_id,
                  consumer_release_commit,bootstrap_publication_id,
                  last_publication_id,causal_state
             FROM strategy_entry.p90_scoped_native_consumer_checkpoints
            ORDER BY strategy_id"""
    ).fetchall()
    if (
        len(rows) != 2
        or {r[0] for r in rows} != P90_IDS
        or any(
            r[2] != source or r[3] != sha
            or type(r[4]) is not int or r[4] < 1
            or type(r[5]) is not int or r[5] < r[4]
            or not isinstance(r[6], dict)
            or r[6].get("version") != 1
            for r in rows
        )
    ):
        raise R06SwitchPreflightBlocked("R06_DUAL_DECISION_CHECKPOINT_UNVERIFIED")
    raw = json.loads(STATUS.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise R06SwitchPreflightBlocked("R06_OBSERVER_STATUS_INVALID")
    at = datetime.fromisoformat(str(raw.get("updated_at", "")))
    sensor = raw.get("sensor_status")
    if (
        at.utcoffset() != timedelta(0)
        or not timedelta(0) <= datetime.now(UTC) - at <= timedelta(seconds=10)
        or raw.get("state") != "R06_PG_NO_ORDER_SOURCE_ONLY"
        or raw.get("source_commit") != sha
        or raw.get("observer_epoch_id") != source
        or not isinstance(sensor, dict)
        or sensor.get("observer_market_ws") != "DETACHED_BY_R06_MODE"
        or sensor.get("financial_ready") is not False
        or sensor.get("native_pg_delivery_scope")
           != "R06_NO_ORDER_ENTRY_DECISION_ONLY"
    ):
        raise R06SwitchPreflightBlocked("R06_PHYSICAL_NO_ORDER_SOURCE_NOT_READY")
    return {
        "result": "R06_PHYSICAL_PG_ONLY_NO_ORDER_PASS",
        "release": sha,
        "source_session": source,
        "strategy_checkpoint_count": 2,
        "last_publication_ids": [r[5] for r in rows],
        "trading_effect": "NONE",
    }


def verify(mode: str) -> dict[str, object]:
    if mode not in ("before", "detached", "after"):
        raise R06SwitchPreflightBlocked("R06_UNKNOWN_PROBE_MODE")
    sha = current_release()
    with psycopg.connect(
        DSN + " options='-c default_transaction_read_only=on'",
        autocommit=True,
        connect_timeout=5,
    ) as conn:
        if conn.execute("SHOW transaction_read_only").fetchone() != ("on",):
            raise R06SwitchPreflightBlocked("R06_DB_READ_ONLY_NOT_ENFORCED")
        bundles = _bundles(conn)
        _require_no_exposure_or_requests(conn)
        if mode != "after":
            # Credential-local signed MAIN/SUB proof, with its own bounded
            # generation barrier. Never accept stale 0/0 or mocked proof.
            preflight_p90_real_private_flat_source(conn, bundles=bundles)
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL statement_timeout=25000")
            evidence = verify_shadow_read(
                conn, observed_at=datetime.now(UTC),
                source_checkout=SOURCE_CHECKOUT, loaded_commit=sha,
            )
            if mode == "after":
                return _require_after(
                    conn, sha, str(evidence["source_session_id"])
                )
        return {
            "result": "R06_PG_SWITCH_PREFLIGHT_PASS",
            "mode": mode,
            "release": sha,
            "source_session": evidence["source_session_id"],
            "five_symbol_history_verified": True,
            "signed_main_sub_flat_verified": True,
            "trading_effect": "NONE",
        }


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise R06SwitchPreflightBlocked("R06_MODE_REQUIRED")
        result = verify(sys.argv[1])
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        # No credentials, signed documents, Exchange payloads or secrets.
        print(json.dumps({
            "result": "R06_SWITCH_PREFLIGHT_BLOCKED",
            "reason_type": type(exc).__name__,
            "reason": str(exc)[:200],
            "trading_effect": "NONE",
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
