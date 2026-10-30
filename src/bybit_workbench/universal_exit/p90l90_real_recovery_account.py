"""Credential-local signed account inventory for P90 REAL restart recovery.

A restart with existing exposure cannot use the fresh-epoch FLAT proof.  This
reader proves the *entire* USDT-linear inventory of one mounted credential:
current private WS, current COMPLETE account generation, signed account UID,
all non-zero positions, zero active orders and zero nonterminal local commands.
It performs signed GETs only and never mutates Exchange state.

The Observer must still match every returned position to an exact durable
StrategyPosition/ExitPlan and prove native MarketSource continuity separately.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from bybit_workbench.exchange.bybit.account_connector_attestation import (
    attest_bybit_account_connector,
)
from bybit_workbench.exchange.bybit.account_identity_snapshot import (
    normalize_authenticated_api_key_info,
)

SignedGet = Callable[[str, dict[str, str]], Mapping[str, object]]
WsProof = Callable[[], bool]

_POSITION_KEYS = (
    "symbol", "side", "positionIdx", "size", "avgPrice",
    "stopLoss", "takeProfit", "trailingStop", "markPrice",
)


class P90RealRecoveryAccountUnverified(RuntimeError):
    """Current signed account inventory cannot be proven exact."""


@dataclass(frozen=True, slots=True)
class P90RealSignedRecoveryAccount:
    trading_account_id: str
    signed_uid: str
    private_generation_id: str
    source_ref: str
    observed_at: datetime
    positions: tuple[Mapping[str, object], ...]
    open_orders: int
    pending_commands: int


class P90RealSignedRecoveryAccountReader:
    """Read complete account inventory from one credential-local worker."""

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
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_READER_CONFIG_INVALID"
            )
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
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_SIGNED_GET_UNAVAILABLE"
            ) from None
        if (
            not isinstance(raw, Mapping)
            or type(raw.get("retCode")) is not int
            or raw.get("retCode") != 0
            or not isinstance(raw.get("result"), Mapping)
        ):
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_SIGNED_RESPONSE_INVALID"
            )
        return raw

    @staticmethod
    def _number(value: object, *, allow_zero: bool) -> Decimal:
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_POSITION_NUMBER_INVALID"
            ) from None
        if (
            not parsed.is_finite()
            or parsed < 0
            or (not allow_zero and parsed <= 0)
        ):
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_POSITION_NUMBER_INVALID"
            )
        return parsed

    def _positions(self) -> tuple[Mapping[str, object], ...]:
        raw = self._signed(
            "/v5/position/list",
            {"category": "linear", "settleCoin": "USDT", "limit": "200"},
        )
        result = raw["result"]
        assert isinstance(result, Mapping)
        rows = result.get("list")
        if (
            result.get("category") != "linear"
            or not isinstance(rows, list)
            or result.get("nextPageCursor") not in ("", None)
        ):
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_POSITION_INVENTORY_INCOMPLETE"
            )
        positions: list[Mapping[str, object]] = []
        for raw_row in rows:
            if not isinstance(raw_row, Mapping):
                raise P90RealRecoveryAccountUnverified(
                    "P90_REAL_RECOVERY_POSITION_ROW_INVALID"
                )
            size = self._number(raw_row.get("size"), allow_zero=True)
            if size == 0:
                continue
            if (
                not isinstance(raw_row.get("symbol"), str)
                or not raw_row["symbol"]
                or raw_row.get("side") not in {"Buy", "Sell"}
                or str(raw_row.get("positionIdx")) != "0"
            ):
                raise P90RealRecoveryAccountUnverified(
                    "P90_REAL_RECOVERY_POSITION_IDENTITY_INVALID"
                )
            for key in ("avgPrice", "stopLoss", "takeProfit", "markPrice"):
                self._number(raw_row.get(key), allow_zero=False)
            self._number(raw_row.get("trailingStop"), allow_zero=True)
            positions.append({
                key: raw_row.get(key)
                for key in _POSITION_KEYS
            })
        positions.sort(key=lambda row: str(row["symbol"]))
        symbols = [str(row["symbol"]) for row in positions]
        if len(symbols) != len(set(symbols)):
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_DUPLICATE_SYMBOL_POSITION"
            )
        return tuple(positions)

    def _require_no_orders(self) -> None:
        raw = self._signed(
            "/v5/order/realtime",
            {
                "category": "linear", "settleCoin": "USDT",
                "openOnly": "0", "limit": "50",
            },
        )
        result = raw["result"]
        assert isinstance(result, Mapping)
        if (
            result.get("category") != "linear"
            or not isinstance(result.get("list"), list)
            or result["list"]
            or result.get("nextPageCursor") not in ("", None)
        ):
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_OPEN_ORDER_OR_INCOMPLETE_INVENTORY"
            )

    def __call__(self) -> P90RealSignedRecoveryAccount:
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None or now.utcoffset() is None
            or getattr(
                getattr(getattr(self._db, "info", None), "transaction_status", None),
                "name", None,
            ) != "INTRANS"
        ):
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_TRANSACTION_INVALID"
            )
        try:
            live_ws = self._ws()
        except Exception:
            live_ws = False
        if live_ws is not True:
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_PRIVATE_WS_UNVERIFIED"
            )
        generation = self._db.execute(
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
            not isinstance(generation, (tuple, list)) or len(generation) != 6
            or not isinstance(generation[0], str) or not generation[0]
            or generation[1] != "COMPLETE"
            or not isinstance(generation[3], datetime)
            or generation[3].tzinfo is None or generation[3].utcoffset() is None
            or not 0 <= (now - generation[3]).total_seconds() <= self._age
            or type(generation[4]) is not int or generation[4] < 0
            or type(generation[5]) is not int or generation[5] != 0
        ):
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_ACCOUNT_GENERATION_UNVERIFIED"
            )
        pending = self._db.execute(
            """SELECT count(*) FROM runtime.trade_commands
                WHERE trading_account_id=%s
                  AND state NOT IN ('completed','failed','cancelled','rejected')""",
            (self._account,),
        ).fetchone()
        if pending != (0,):
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_PENDING_ACCOUNT_COMMAND"
            )
        identity_raw = self._signed("/v5/user/query-api", {})
        try:
            identity = normalize_authenticated_api_key_info(
                identity_raw, observed_at=now, signed_get_proven=True,
            )
            signed_uid = str(identity["account_user_id"])
        except Exception:
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_SIGNED_UID_UNVERIFIED"
            ) from None
        if (
            not signed_uid.isascii() or not signed_uid.isdigit()
            or int(signed_uid) <= 0
        ):
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_SIGNED_UID_INVALID"
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
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_CONNECTOR_UID_MISMATCH"
            )
        positions = self._positions()
        self._require_no_orders()
        if generation[4] != len(positions):
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_GENERATION_POSITION_COUNT_MISMATCH"
            )
        try:
            live_ws_after = self._ws()
        except Exception:
            live_ws_after = False
        if live_ws_after is not True:
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_PRIVATE_WS_LOST_DURING_READ"
            )
        finished = self._clock()
        if (
            not isinstance(finished, datetime)
            or finished.tzinfo is None or finished.utcoffset() is None
            or not 0 <= (finished - now).total_seconds() <= self._age
        ):
            raise P90RealRecoveryAccountUnverified(
                "P90_REAL_RECOVERY_SIGNED_READ_WINDOW_EXPIRED"
            )
        return P90RealSignedRecoveryAccount(
            trading_account_id=self._account,
            signed_uid=signed_uid,
            private_generation_id=str(generation[0]),
            source_ref="bybit:signed-recovery-linear-usdt:exact-account",
            observed_at=finished.astimezone(UTC),
            positions=positions,
            open_orders=0,
            pending_commands=0,
        )
