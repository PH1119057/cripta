from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol


class AccountStateGenerationUnavailable(RuntimeError):
    """No current COMPLETE account-state generation is safe for admission."""


@dataclass(frozen=True, slots=True)
class AccountStateGeneration:
    generation_id: str
    started_at: datetime
    completed_at: datetime
    account_ref: str
    account_type: str
    total_equity: Decimal
    wallet_balance: Decimal
    available_balance: Decimal
    positions_count: int
    active_orders_count: int
    position_mode_refs: Mapping[str, str]


class CursorLike(Protocol):
    def fetchone(self) -> Sequence[object] | None: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...


def current_complete_account_state_generation(
    connection: ConnectionLike,
    *,
    account_ref: str,
    acquire_account_lock: bool = False,
) -> AccountStateGeneration:
    if acquire_account_lock:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (account_ref,),
        )

    row = connection.execute(
        """SELECT generation_id,state,started_at,completed_at,account_ref,account_type,
                  total_equity,wallet_balance,available_balance,positions_count,
                  active_orders_count,position_mode_refs,error
             FROM runtime.account_state_generations
            WHERE account_ref=%s
              AND state IN ('COMPLETE','FAILED')
            ORDER BY started_at DESC,generation_id DESC
            LIMIT 1""",
        (account_ref,),
    ).fetchone()
    if row is None:
        raise AccountStateGenerationUnavailable(
            f"account state generation missing for {account_ref}"
        )
    generation_id = str(row[0])
    state = str(row[1])
    if state != "COMPLETE":
        detail = str(row[12] or "")
        raise AccountStateGenerationUnavailable(
            f"latest account state generation is {state}: {generation_id}"
            + (f" ({detail})" if detail else "")
        )

    started_at = row[2]
    completed_at = row[3]
    if not isinstance(started_at, datetime) or not isinstance(completed_at, datetime):
        raise AccountStateGenerationUnavailable(
            f"account state generation timestamps invalid: {generation_id}"
        )
    if started_at.tzinfo is None or completed_at.tzinfo is None:
        raise AccountStateGenerationUnavailable(
            f"account state generation timestamps are naive: {generation_id}"
        )

    account_type = str(row[5] or "")
    if account_type != "UNIFIED":
        raise AccountStateGenerationUnavailable(
            f"account state generation account type mismatch: {account_type or 'UNKNOWN'}"
        )
    if row[6] is None or row[7] is None or row[8] is None:
        raise AccountStateGenerationUnavailable(
            f"account state generation capital is incomplete: {generation_id}"
        )
    if row[9] is None or row[10] is None:
        raise AccountStateGenerationUnavailable(
            f"account state generation inventory is incomplete: {generation_id}"
        )

    raw_refs = row[11]
    if isinstance(raw_refs, str):
        raw_refs = json.loads(raw_refs)
    if not isinstance(raw_refs, Mapping):
        raise AccountStateGenerationUnavailable(
            f"account state generation position-mode refs invalid: {generation_id}"
        )
    refs = {str(k): str(v) for k, v in raw_refs.items() if str(k) and str(v)}

    return AccountStateGeneration(
        generation_id=generation_id,
        started_at=started_at.astimezone(UTC),
        completed_at=completed_at.astimezone(UTC),
        account_ref=str(row[4]),
        account_type=account_type,
        total_equity=Decimal(str(row[6])),
        wallet_balance=Decimal(str(row[7])),
        available_balance=Decimal(str(row[8])),
        positions_count=int(str(row[9])),
        active_orders_count=int(str(row[10])),
        position_mode_refs=refs,
    )