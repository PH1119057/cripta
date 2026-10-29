"""Credential-local signed *flat* USDT-linear inventory for a fresh REAL epoch.

An empty DB roster, order ACK or cached status is not Exchange proof. This
reader runs only in the credential-owning private worker; it requires current
in-process private WS continuity, the latest complete account generation,
no pending durable account command, signed Bybit identity and independent
signed full USDT-linear position/open-order inventory. No Exchange POST.
The source epoch owner separately checks both accounts and native WS coverage.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from bybit_workbench.exchange.bybit.account_connector_attestation import (
    attest_bybit_account_connector,
)
from bybit_workbench.exchange.bybit.account_identity_snapshot import (
    normalize_authenticated_api_key_info,
)
from bybit_workbench.universal_entry.p90l90_real_source_epoch_start import (
    P90RealSignedFlatAccount,
)

SignedGet = Callable[[str, dict[str, str]], Mapping[str, object]]
WsProof = Callable[[], bool]


class P90RealFlatAccountUnverified(RuntimeError):
    """Current signed MAIN/SUB flat inventory was not independently proven."""


class P90RealSignedFlatAccountReader:
    """Read-only signed flat USDT-linear scope from one mounted credential."""

    def __init__(
        self,
        connection: Any,
        *,
        trading_account_id: str,
        account_connector_id: str,
        mounted_credential_ref: str,
        signed_get: SignedGet,
        verified_private_ws_continuity: WsProof,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_age_seconds: int = 15,
    ) -> None:
        if (
            not trading_account_id or not account_connector_id
            or not mounted_credential_ref or not callable(signed_get)
            or not callable(verified_private_ws_continuity)
            or not callable(clock)
            or type(max_age_seconds) is not int
            or not 0 < max_age_seconds <= 15
        ):
            raise P90RealFlatAccountUnverified("P90_REAL_FLAT_READER_CONFIG_INVALID")
        self._db = connection
        self._account = trading_account_id
        self._connector = account_connector_id
        self._credential_ref = mounted_credential_ref
        self._get = signed_get
        self._ws = verified_private_ws_continuity
        self._clock = clock
        self._age = max_age_seconds

    def _signed(self, endpoint: str, params: dict[str, str]) -> Mapping[str, object]:
        try:
            raw = self._get(endpoint, params)
        except Exception:
            # The underlying signed URL/response/credential must not escape.
            raise P90RealFlatAccountUnverified(
                "P90_REAL_FLAT_SIGNED_GET_UNAVAILABLE"
            ) from None
        if (
            not isinstance(raw, Mapping)
            or type(raw.get("retCode")) is not int
            or raw.get("retCode") != 0
            or not isinstance(raw.get("result"), Mapping)
        ):
            raise P90RealFlatAccountUnverified(
                "P90_REAL_FLAT_SIGNED_RESPONSE_INVALID"
            )
        return raw

    def _empty_exchange_rows(self, endpoint: str, params: dict[str, str]) -> None:
        raw = self._signed(endpoint, params)
        result = raw["result"]
        assert isinstance(result, Mapping)
        if (
            result.get("category") != "linear"
            or not isinstance(result.get("list"), list)
            or result["list"]
            or result.get("nextPageCursor") not in ("", None)
        ):
            # One nonempty page or an unexamined next page is NOT flat.
            raise P90RealFlatAccountUnverified(
                "P90_REAL_FLAT_SIGNED_INVENTORY_NONEMPTY_OR_INCOMPLETE"
            )

    def __call__(self) -> P90RealSignedFlatAccount:
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
            or getattr(
                getattr(getattr(self._db, "info", None), "transaction_status", None),
                "name", None,
            ) != "INTRANS"
        ):
            raise P90RealFlatAccountUnverified("P90_REAL_FLAT_TRANSACTION_INVALID")
        try:
            live_ws = self._ws()
        except Exception:
            live_ws = False
        if live_ws is not True:
            raise P90RealFlatAccountUnverified(
                "P90_REAL_FLAT_PRIVATE_WS_UNVERIFIED"
            )
        row = self._db.execute(
            """SELECT generation_id,state,started_at,completed_at,
                      positions_count,active_orders_count
                 FROM runtime.account_state_generations
                WHERE trading_account_id=%s AND account_connector_id=%s
                  AND identity_status='PROVEN'
                  AND state IN ('COMPLETE','FAILED')
                ORDER BY started_at DESC,generation_id DESC LIMIT 1""",
            (self._account, self._connector),
        ).fetchone()
        if (
            not isinstance(row, (tuple, list)) or len(row) != 6
            or not isinstance(row[0], str) or not row[0]
            or row[1] != "COMPLETE"
            or not isinstance(row[3], datetime)
            or row[3].tzinfo is None or row[3].utcoffset() is None
            or not 0 <= (now - row[3]).total_seconds() <= self._age
            or type(row[4]) is not int or row[4] != 0
            or type(row[5]) is not int or row[5] != 0
        ):
            raise P90RealFlatAccountUnverified(
                "P90_REAL_FLAT_ACCOUNT_GENERATION_UNVERIFIED"
            )
        pending = self._db.execute(
            """SELECT count(*) FROM runtime.trade_commands
                WHERE trading_account_id=%s
                  AND state NOT IN ('completed','failed','cancelled','rejected')""",
            (self._account,),
        ).fetchone()
        if pending != (0,):
            raise P90RealFlatAccountUnverified(
                "P90_REAL_FLAT_PENDING_ACCOUNT_COMMAND"
            )
        identity_raw = self._signed("/v5/user/query-api", {})
        try:
            identity = normalize_authenticated_api_key_info(
                identity_raw, observed_at=now, signed_get_proven=True,
            )
            signed_uid = str(identity["account_user_id"])
        except Exception:
            raise P90RealFlatAccountUnverified(
                "P90_REAL_FLAT_SIGNED_UID_UNVERIFIED"
            ) from None
        if (
            not signed_uid.isascii()
            or not signed_uid.isdigit()
            or int(signed_uid) <= 0
        ):
            raise P90RealFlatAccountUnverified(
                "P90_REAL_FLAT_SIGNED_UID_INVALID"
            )
        attestation = attest_bybit_account_connector(
            self._db,
            trading_account_id=self._account,
            account_connector_id=self._connector,
            verified_credential_ref=self._credential_ref,
            signed_account_get=lambda: identity_raw,
            now=now,
            max_age_seconds=self._age,
        )
        if not attestation.ready:
            raise P90RealFlatAccountUnverified(
                "P90_REAL_FLAT_CONNECTOR_UID_MISMATCH"
            )
        # Bybit V5 requires settleCoin for symbol-less linear account queries.
        # Any position or pending/conditional order blocks new epoch, and
        # hidden pagination does not silently become zero.
        self._empty_exchange_rows(
            "/v5/position/list",
            {"category": "linear", "settleCoin": "USDT", "limit": "200"},
        )
        self._empty_exchange_rows(
            "/v5/order/realtime",
            {"category": "linear", "settleCoin": "USDT",
             "openOnly": "0", "limit": "50"},
        )
        if not self._ws():
            raise P90RealFlatAccountUnverified(
                "P90_REAL_FLAT_PRIVATE_WS_LOST_DURING_READ"
            )
        finished = self._clock()
        if (
            not isinstance(finished, datetime) or finished.tzinfo is None
            or finished.utcoffset() is None
            or not 0 <= (finished - now).total_seconds() <= self._age
        ):
            raise P90RealFlatAccountUnverified(
                "P90_REAL_FLAT_SIGNED_READ_WINDOW_EXPIRED"
            )
        return P90RealSignedFlatAccount(
            trading_account_id=self._account,
            signed_uid=signed_uid,
            private_generation_id=row[0],
            source_ref="bybit:signed-flat-linear-usdt:exact-account",
            observed_at=finished.astimezone(UTC),
            open_positions=0,
            open_orders=0,
            pending_commands=0,
        )
