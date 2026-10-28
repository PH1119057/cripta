"""Read-only, credential-bound Bybit position proof for P90 REAL Exit.

This adapter must be instantiated inside the account-specific private worker
using that worker's mounted credential and its existing signed GET transport.
Callbacks for private WS continuity and pending commands must be independent
runtime owners; their absence/uncertainty is a hard block. A signed REST read
is not a WS heartbeat, native trade cursor or Exchange mutation permit.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from bybit_workbench.account_state_generation import (
    AccountStateGenerationUnavailable,
    current_complete_account_state_generation,
)
from bybit_workbench.exchange.bybit.account_connector_attestation import (
    attest_bybit_account_connector,
)
from bybit_workbench.exchange.bybit.account_identity_snapshot import (
    normalize_authenticated_api_key_info,
)
from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import TradeDirection
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

from .p90l90_real_observation import ScopedPrivatePositionEvidence


class P90RealSignedPositionUnverified(RuntimeError):
    """An independent account, freshness or Exchange read gate failed."""


SignedGet = Callable[[str, dict[str, str]], Mapping[str, object]]
RuntimeProof = Callable[[StrategyPosition], bool]


class P90RealSignedPositionReader:
    """Produce one signed, exact-account Exit position fact, with no POST.

    signed_get MUST use this worker's mounted Bybit credential, and never
    accept a caller-supplied credential or account. The caller must wire
    verified_private_ws_continuity to an independently proven live WS epoch,
    and no_pending_exit_command to an account-scoped command-ledger owner.
    Merely returning True from a stub is not real runtime attestation.
    """

    def __init__(
        self,
        connection: Any,
        *,
        trading_account_id: str,
        account_connector_id: str,
        mounted_credential_ref: str,
        signed_get: SignedGet,
        verified_private_ws_continuity: RuntimeProof,
        no_pending_exit_command: RuntimeProof,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_age_seconds: int = 15,
    ) -> None:
        if (
            not trading_account_id or not account_connector_id
            or not mounted_credential_ref
            or not callable(signed_get)
            or not callable(verified_private_ws_continuity)
            or not callable(no_pending_exit_command)
            or type(max_age_seconds) is not int
            or not 0 < max_age_seconds <= 15
        ):
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_READER_CONFIG_INVALID"
            )
        self._db = connection
        self._account = trading_account_id
        self._connector = account_connector_id
        self._credential_ref = mounted_credential_ref
        self._get = signed_get
        self._ws = verified_private_ws_continuity
        self._no_pending = no_pending_exit_command
        self._clock = clock
        self._age = max_age_seconds

    def _signed_get(
        self, endpoint: str, params: dict[str, str],
    ) -> Mapping[str, object]:
        try:
            return self._get(endpoint, params)
        except Exception:
            # No raw signed response, credentials or transport URL in errors.
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_EXCHANGE_TRANSPORT_UNAVAILABLE"
            ) from None

    @staticmethod
    def _verified_response(raw: Mapping[str, object], endpoint: str) -> Mapping[str, object]:
        if (
            not isinstance(raw, Mapping)
            or type(raw.get("retCode")) is not int
            or raw["retCode"] != 0
            or not isinstance(raw.get("result"), Mapping)
        ):
            raise P90RealSignedPositionUnverified(
                f"P90_REAL_SIGNED_{endpoint}_UNAVAILABLE"
            )
        result = raw["result"]
        assert isinstance(result, Mapping)
        return result

    def __call__(
        self, position: StrategyPosition, cursor: TradeCursor,
    ) -> ScopedPrivatePositionEvidence:
        """Native tick adapter; cursor provenance is checked by its owner."""
        if cursor.symbol != position.symbol:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_POSITION_LINEAGE_INVALID"
            )
        return self._read_for_event(position, cursor.traded_at)

    def for_closed_boundary(
        self, position: StrategyPosition, closed_at: datetime,
    ) -> ScopedPrivatePositionEvidence:
        """Closed-5m port; its caller must prove exact causal candle history."""
        return self._read_for_event(position, closed_at)

    def _read_for_event(
        self, position: StrategyPosition, event_at: datetime,
    ) -> ScopedPrivatePositionEvidence:
        now = self._clock()
        if (
            now.tzinfo is None or now.utcoffset() is None
            or position.strategy_id not in ALLOWED_SYMBOLS
            or position.strategy_version != VERSION
            or position.symbol not in ALLOWED_SYMBOLS[position.strategy_id]
            or position.trading_account_id != self._account
            or position.identity_status != "PROVEN"
            or not position.strategy_execution_binding_id
            or position.position_idx != 0
            or position.exchange_position_key != exchange_position_key(
                self._account, position.symbol, 0,
            )
            or event_at.tzinfo is None
            or event_at > now
            or position.fill_at > event_at
            or position.initial_protection_confirmed_at is None
            or position.initial_protection_confirmed_at > now
        ):
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_POSITION_LINEAGE_INVALID"
            )
        # Runtime-only facts are independently owned. Unknown means BLOCK,
        # and no REST read or UID information is requested in that state.
        try:
            ws_proven = self._ws(position)
            commands_clear = self._no_pending(position)
        except Exception:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_PRIVATE_RUNTIME_PROOF_UNAVAILABLE"
            ) from None
        if ws_proven is not True:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_PRIVATE_WS_CONTINUITY_UNVERIFIED"
            )
        if commands_clear is not True:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_PENDING_EXIT_COMMAND_UNVERIFIED"
            )
        try:
            generation = current_complete_account_state_generation(
                self._db, account_ref=position.account_ref,
                trading_account_id=self._account,
            )
        except AccountStateGenerationUnavailable as exc:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_GENERATION_UNAVAILABLE"
            ) from exc
        age = (now.astimezone(UTC) - generation.completed_at).total_seconds()
        if (
            generation.trading_account_id != self._account
            or generation.account_ref != position.account_ref
            or generation.started_at > generation.completed_at
            or generation.completed_at < position.fill_at
            or generation.completed_at < position.initial_protection_confirmed_at
            or generation.positions_count < 1
            or not 0 <= age <= self._age
        ):
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_GENERATION_STALE_OR_WRONG_ACCOUNT"
            )
        connector = self._db.execute(
            """SELECT account_connector_id FROM runtime.account_state_generations
                WHERE generation_id=%s AND trading_account_id=%s
                  AND account_connector_id=%s AND identity_status='PROVEN'
                  AND state='COMPLETE'""",
            (generation.generation_id, self._account, self._connector),
        ).fetchone()
        if connector != (self._connector,):
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_GENERATION_CONNECTOR_MISMATCH"
            )
        uid: str | None = None

        def read_own_identity() -> Mapping[str, object]:
            nonlocal uid
            raw = self._signed_get("/v5/user/query-api", {})
            snapshot = normalize_authenticated_api_key_info(
                raw, observed_at=now, signed_get_proven=True,
            )
            uid = str(snapshot["account_user_id"])
            return raw

        identity = attest_bybit_account_connector(
            self._db, trading_account_id=self._account,
            account_connector_id=self._connector,
            verified_credential_ref=self._credential_ref,
            signed_account_get=read_own_identity,
            now=now, max_age_seconds=self._age,
        )
        if not identity.ready or not uid:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_CONNECTOR_UID_UNVERIFIED"
            )
        # Only one signed Bybit open position for this symbol is acceptable.
        # The response is never persisted here (may contain account metadata).
        signed = self._verified_response(
            self._signed_get("/v5/position/list", {
                "category": "linear", "symbol": position.symbol,
            }), "POSITION",
        )
        rows = signed.get("list")
        if not isinstance(rows, list):
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_POSITION_LIST_INCOMPLETE"
            )
        try:
            active = [
                row for row in rows if isinstance(row, Mapping)
                and Decimal(str(row.get("size") or "0")) > 0
            ]
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_POSITION_SIZE_INVALID"
            ) from exc
        if len(active) != 1:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_EXACTLY_ONE_POSITION_REQUIRED"
            )
        observed = active[0]
        side = "Buy" if position.direction is TradeDirection.LONG else "Sell"
        try:
            signed_size = Decimal(str(observed.get("size")))
            signed_fill = Decimal(str(observed.get("avgPrice")))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_POSITION_FILL_OR_SIDE_MISMATCH"
            ) from exc
        if (
            observed.get("symbol") != position.symbol
            or str(observed.get("positionIdx")) != "0"
            or observed.get("side") != side
            or not signed_size.is_finite() or not signed_fill.is_finite()
            or signed_size != position.actual_qty
            or signed_fill != position.actual_avg_fill
        ):
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_POSITION_FILL_OR_SIDE_MISMATCH"
            )
        instrument = self._verified_response(
            self._signed_get("/v5/market/instruments-info", {
                "category": "linear", "symbol": position.symbol,
            }), "INSTRUMENT",
        )
        items = instrument.get("list")
        if not isinstance(items, list) or len(items) != 1:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_INSTRUMENT_NOT_UNIQUE"
            )
        product = items[0]
        if (
            not isinstance(product, Mapping)
            or product.get("symbol") != position.symbol
            or product.get("status") != "Trading"
            or product.get("quoteCoin") != "USDT"
            or product.get("settleCoin") != "USDT"
            or not isinstance(product.get("priceFilter"), Mapping)
        ):
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_INSTRUMENT_IDENTITY_INVALID"
            )
        try:
            tick = Decimal(str(product["priceFilter"]["tickSize"]))
        except (InvalidOperation, TypeError, ValueError, KeyError) as exc:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_INSTRUMENT_TICK_INVALID"
            ) from exc
        if not tick.is_finite() or tick <= 0:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_INSTRUMENT_TICK_INVALID"
            )
        # Every member needed for actual protective Exit is a signed read,
        # never an order ACK, strategy prediction or PAPER projection.
        try:
            sl = Decimal(str(observed.get("stopLoss")))
            tp = Decimal(str(observed.get("takeProfit")))
            mark = Decimal(str(observed.get("markPrice")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_PROTECTION_INCOMPLETE"
            ) from exc
        long = position.direction is TradeDirection.LONG
        if (
            any(not v.is_finite() or v <= 0 for v in (sl, tp, mark))
            or sl % tick != 0 or tp % tick != 0
            or not (sl < mark < tp and tp > position.actual_avg_fill
                    if long else tp < mark < sl and tp < position.actual_avg_fill)
        ):
            raise P90RealSignedPositionUnverified(
                "P90_REAL_SIGNED_PROTECTION_UNEXECUTABLE"
            )
        return ScopedPrivatePositionEvidence(
            trading_account_id=self._account,
            strategy_execution_binding_id=position.strategy_execution_binding_id,
            connector_id=self._connector,
            signed_provider_uid=uid,
            generation_id=generation.generation_id,
            exchange_position_key=position.exchange_position_key,
            symbol=position.symbol,
            position_idx=position.position_idx,
            observed_at=now.astimezone(UTC),
            source_ref="bybit:signed-position-list:exact-account",
            position=dict(observed), tick_size=tick,
            pending_exit_mutation=False,
        )
