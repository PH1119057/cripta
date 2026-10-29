"""Read-only PostgreSQL resolution of TWO exact P90L90 REAL account routes.

DB account/connector metadata is only a roster, NOT signed Bybit identity,
private WS continuity, owner LIVE arm, release parity, or permission to trade.
The account-specific private worker must independently re-attest every actual
Exchange mutation. No credentials leave the database through this adapter.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .p90l90_legacy_strategy import MAIN_ID, SUB_ID
from .p90l90_real_contract_v3 import ALLOWED_SYMBOLS, VERSION

_ACCOUNT = re.compile(r"ta-[0-9a-f]{32}\Z")
_KIND = {MAIN_ID: "MAIN", SUB_ID: "SUB"}


class P90RealAccountRoutesUnverified(RuntimeError):
    """No unique account/connector binding exists for both REAL v3 Strategies."""


@dataclass(frozen=True, slots=True)
class P90RealAccountRoute:
    strategy_id: str
    activation_id: str
    strategy_config_fingerprint: str
    strategy_execution_binding_id: str
    trading_account_id: str
    account_connector_id: str
    account_kind: str
    registered_exchange_uid: str


@dataclass(frozen=True, slots=True)
class P90RealAccountRoutes:
    main: P90RealAccountRoute
    sub: P90RealAccountRoute

    @property
    def account_by_strategy(self) -> dict[str, str]:
        return {
            self.main.strategy_id: self.main.trading_account_id,
            self.sub.strategy_id: self.sub.trading_account_id,
        }

    @property
    def connector_by_strategy(self) -> dict[str, str]:
        return {
            self.main.strategy_id: self.main.account_connector_id,
            self.sub.strategy_id: self.sub.account_connector_id,
        }


def read_p90_real_account_routes(
    connection: Any,
    *,
    bundles: Sequence[object],
    at: datetime,
) -> P90RealAccountRoutes:
    """Reject every ambiguous physical/account binding before startup.

    Caller still needs signed Bybit UID, private WS, complete account state
    generations, active account LIVE arms, release identity, verified native
    market transport and post-fill Exit protection. This function grants NONE
    of those gates and cannot touch Exchange or change PostgreSQL state.
    """
    if at.tzinfo is None or at.utcoffset() is None:
        raise P90RealAccountRoutesUnverified("P90_REAL_ACCOUNT_ROUTE_TIME_UNVERIFIED")
    if len(bundles) != 2:
        raise P90RealAccountRoutesUnverified(
            "P90_REAL_ACCOUNT_ROUTE_TWO_INDEPENDENT_V3_BUNDLES_REQUIRED"
        )
    approved_at = at.astimezone(UTC)
    result: dict[str, P90RealAccountRoute] = {}
    for raw in bundles:
        card = getattr(raw, "card", None)
        activation = getattr(raw, "activation", None)
        plan = getattr(raw, "entry_plan", None)
        strategy_id = getattr(card, "strategy_id", None)
        if (
            strategy_id not in _KIND
            or strategy_id in result
            or getattr(card, "strategy_version", None) != VERSION
            or frozenset(getattr(card, "symbols", ())) != ALLOWED_SYMBOLS[strategy_id]
            or getattr(activation, "enabled", None) is not True
            or getattr(activation, "strategy_id", None) != strategy_id
            or getattr(activation, "strategy_version", None) != VERSION
            or getattr(activation, "strategy_config_fingerprint", None)
               != getattr(card, "strategy_config_fingerprint", None)
            or getattr(plan, "strategy_activation_id", None)
               != getattr(activation, "activation_id", None)
            or getattr(plan, "strategy_config_fingerprint", None)
               != getattr(card, "strategy_config_fingerprint", None)
            or frozenset(getattr(plan, "symbols", ())) != ALLOWED_SYMBOLS[strategy_id]
        ):
            raise P90RealAccountRoutesUnverified(
                "P90_REAL_ACCOUNT_ROUTE_STRATEGY_ACTIVATION_NOT_EXACT"
            )
        # Do not filter by a "good" account or connector in WHERE: a second
        # live physical binding, even if it is broken, must not be ignored.
        # Only an ACTIVE connector is eligible; zero or two connectors both
        # block because the exact credential-local worker is not identified.
        rows = connection.execute(
            """SELECT b.strategy_execution_binding_id,b.trading_account_id,
                      b.execution_leg,b.execution_role,b.allocation_mode,
                      a.account_kind,a.account_type,a.environment,a.venue_id,
                      a.status,a.external_account_ref,
                      c.account_connector_id,c.connector_kind,c.adapter_id,c.status,
                      (nullif(btrim(c.credential_ref),'') IS NOT NULL)
                 FROM strategy_entry.strategy_execution_bindings b
                 LEFT JOIN identity.trading_accounts a
                   ON a.trading_account_id=b.trading_account_id
                 LEFT JOIN identity.account_connectors c
                   ON c.trading_account_id=b.trading_account_id
                  AND c.status='ACTIVE' AND c.retired_at IS NULL
                WHERE b.strategy_activation_id=%s
                  AND b.binding_state='ACTIVE'
                  AND b.effective_from<=%s
                  AND (b.effective_to IS NULL OR b.effective_to>%s)
                ORDER BY b.strategy_execution_binding_id,c.account_connector_id""",
            (activation.activation_id, approved_at, approved_at),
        ).fetchall()
        if len(rows) != 1:
            raise P90RealAccountRoutesUnverified(
                "P90_REAL_ACCOUNT_ROUTE_MISSING_OR_MULTIPLE_EFFECTIVE_BINDINGS"
            )
        r = rows[0]
        if not isinstance(r, (tuple, list)) or len(r) != 16:
            raise P90RealAccountRoutesUnverified(
                "P90_REAL_ACCOUNT_ROUTE_ROW_SCHEMA_INVALID"
            )
        (
            binding_id, account_id, leg, role, allocation,
            kind, account_type, environment, venue, account_status,
            uid, connector_id, connector_kind, adapter, connector_status,
            credential_ref_present,
        ) = r
        uid = str(uid or "")
        if (
            not isinstance(binding_id, str) or not binding_id
            or not isinstance(account_id, str)
            or _ACCOUNT.fullmatch(account_id) is None
            or leg != "PRIMARY"
            or role != "PRIMARY"
            or allocation != "DEDICATED_STRATEGY"
            or kind != _KIND[strategy_id]
            or account_type != "UNIFIED"
            or environment != "MAINNET"
            or venue != "venue-bybit"
            or account_status != "ACTIVE"
            or not uid.isascii() or not uid.isdigit()
            or int(uid or "0") <= 0
            or not isinstance(connector_id, str) or not connector_id
            or connector_kind != "CEX_PRIVATE_RUNTIME"
            or adapter != "BYBIT_V5_PRIVATE"
            or connector_status != "ACTIVE"
            or credential_ref_present is not True
        ):
            raise P90RealAccountRoutesUnverified(
                "P90_REAL_ACCOUNT_ROUTE_REGISTERED_OWNER_OR_CREDENTIAL_INVALID"
            )
        result[strategy_id] = P90RealAccountRoute(
            strategy_id=strategy_id,
            activation_id=activation.activation_id,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            strategy_execution_binding_id=binding_id,
            trading_account_id=account_id,
            account_connector_id=connector_id,
            account_kind=kind,
            registered_exchange_uid=uid,
        )
    if set(result) != set(_KIND):
        raise P90RealAccountRoutesUnverified(
            "P90_REAL_ACCOUNT_ROUTE_INCOMPLETE_MAIN_SUB"
        )
    main, sub = result[MAIN_ID], result[SUB_ID]
    if (
        main.trading_account_id == sub.trading_account_id
        or main.account_connector_id == sub.account_connector_id
        or main.registered_exchange_uid == sub.registered_exchange_uid
        or main.strategy_execution_binding_id == sub.strategy_execution_binding_id
    ):
        raise P90RealAccountRoutesUnverified(
            "P90_REAL_ACCOUNT_ROUTE_MAIN_SUB_IDENTITY_COLLISION"
        )
    return P90RealAccountRoutes(main=main, sub=sub)
