"""Inert account-scoped position observer; no trading or Exit mutation rights.

Source input is one proven TradingAccount's current hot price projection.
All extrema are sampled/partial. Old M3 datasets and consumers remain intact.
"""

from __future__ import annotations

import json
import os
import re
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bybit_workbench.account_position_observer import build_sample

_ACCOUNT = re.compile(r"^ta-[a-f0-9]{32}$")
_running = True


def _stop(_signum: int, _frame: object) -> None:
    global _running
    _running = False


def collect_once(connection: Any, *, trading_account_id: str, now: datetime) -> dict[str, object]:
    """Fail closed on stale/missing generation, account ambiguity and bad prices."""
    if not _ACCOUNT.fullmatch(trading_account_id):
        raise ValueError("ACCOUNT_POSITION_ACCOUNT_ID_INVALID")
    clock = now.astimezone(UTC)
    row = connection.execute(
        """SELECT state,completed_at
             FROM runtime.account_state_generations
            WHERE trading_account_id=%s AND identity_status='PROVEN'
            ORDER BY started_at DESC LIMIT 1""", (trading_account_id,)
    ).fetchone()
    if row is None or row["state"] != "COMPLETE" or row["completed_at"] is None:
        return {"state": "UNKNOWN", "reason": "GENERATION_NOT_COMPLETE", "sampled": 0}
    age = (clock - row["completed_at"].astimezone(UTC)).total_seconds()
    if not 0 <= age <= 90:
        return {"state": "UNKNOWN", "reason": "GENERATION_STALE", "sampled": 0}
    positions = connection.execute(
        """SELECT p.position_id,p.symbol,p.position_idx,p.side,p.actual_avg_fill,
                  h.payload_json
             FROM runtime.position_ownership p
             LEFT JOIN runtime.hot_positions h
               ON h.trading_account_id=p.trading_account_id
              AND h.identity_status='PROVEN'
              AND h.symbol=p.symbol AND h.position_idx=p.position_idx
            WHERE p.state='OPEN' AND p.identity_status='PROVEN'
              AND p.trading_account_id=%s
            ORDER BY p.position_id""", (trading_account_id,)
    ).fetchall()
    count = 0
    for position in positions:
        payload = position["payload_json"]
        if payload is None:
            return {"state": "UNKNOWN", "reason": "ACCOUNT_POSITION_HOT_MISSING", "sampled": count}
        raw = json.loads(payload) if isinstance(payload, str) else payload
        if not isinstance(raw, dict) or not raw.get("markPrice"):
            return {"state": "UNKNOWN", "reason": "ACCOUNT_POSITION_MARK_MISSING", "sampled": count}
        previous = connection.execute(
            """SELECT sampled_mfe_pct,sampled_mae_pct
                 FROM supervisor.account_position_snapshots
                WHERE trading_account_id=%s AND position_id=%s
                ORDER BY observed_at DESC LIMIT 1""",
            (trading_account_id, position["position_id"]),
        ).fetchone()
        sample = build_sample(
            trading_account_id=trading_account_id,
            position_id=str(position["position_id"]),
            symbol=str(position["symbol"]),
            position_idx=int(position["position_idx"]),
            side=str(position["side"]),
            entry_price=position["actual_avg_fill"],
            mark_price=raw["markPrice"],
            previous_mfe=None if previous is None else previous["sampled_mfe_pct"],
            previous_mae=None if previous is None else previous["sampled_mae_pct"],
        )
        connection.execute(
            """INSERT INTO supervisor.account_position_snapshots(
                   trading_account_id,position_id,symbol,position_idx,side,
                   observed_at,source,completeness,entry_price,mark_price,
                   return_pct,sampled_mfe_pct,sampled_mae_pct,protection_state,
                   geometry_json,context_json
               ) VALUES (%s,%s,%s,%s,%s,%s,'ACCOUNT_SCOPED_HOT_MARK',
                         'SAMPLED_PARTIAL',%s,%s,%s,%s,%s,'UNKNOWN',
                         '{}'::jsonb,'{}'::jsonb) ON CONFLICT DO NOTHING""",
            (sample.trading_account_id, sample.position_id, sample.symbol,
             sample.position_idx, sample.side, clock, sample.entry_price,
             sample.mark_price, sample.return_pct, sample.sampled_mfe_pct,
             sample.sampled_mae_pct),
        )
        count += 1
    return {"state": "SAMPLED_PARTIAL", "sampled": count, "trading_rights": "NONE"}


def _write_status(path: Path, data: dict[str, object]) -> None:
    if not path.parent.is_dir():
        raise RuntimeError("ACCOUNT_POSITION_STATUS_DIRECTORY_NOT_PREPARED")
    tmp = path.with_name("status.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    if os.environ.get("CRIPTA_ACCOUNT_POSITION_OBSERVER") != "ENABLED":
        raise SystemExit("ACCOUNT_POSITION_OBSERVER_DISABLED")
    account_id = os.environ.get("CRIPTA_TRADING_ACCOUNT_ID", "")
    if not _ACCOUNT.fullmatch(account_id):
        raise SystemExit("ACCOUNT_POSITION_ACCOUNT_ID_INVALID")
    status = Path(os.environ.get("CRIPTA_ACCOUNT_POSITION_STATUS_PATH", ""))
    if status.name != "status.json" or status.parent.name != "account-" + account_id:
        raise SystemExit("ACCOUNT_POSITION_STATUS_SCOPE_INVALID")
    interval = int(os.environ.get("CRIPTA_ACCOUNT_POSITION_POLL_SECONDS", "15"))
    if interval < 10:
        raise SystemExit("ACCOUNT_POSITION_INTERVAL_INVALID")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    with psycopg.connect(
        os.environ.get(
            "CRIPTA_DATABASE_DSN",
            "dbname=cripta user=cripta host=/var/run/postgresql application_name=account-position",
        ),
        row_factory=dict_row,
    ) as connection:
        while _running:
            try:
                with connection.transaction():
                    record = collect_once(
                        connection, trading_account_id=account_id, now=datetime.now(UTC)
                    )
                output = {"observed_at": datetime.now(UTC).isoformat(), **record}
            except Exception as exc:
                connection.rollback()
                output = {
                    "observed_at": datetime.now(UTC).isoformat(),
                    "state": "UNKNOWN",
                    "error_class": type(exc).__name__,
                    "trading_rights": "NONE",
                }
            _write_status(status, output)
            time.sleep(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
