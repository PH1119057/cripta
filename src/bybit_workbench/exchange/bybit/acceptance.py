from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bybit_workbench import __version__
from bybit_workbench.app.config import MAINNET_REST_URLS
from bybit_workbench.domain.models import InstrumentRules, Order

from .models import ApiKeyInfo, BybitPositionSnapshot
from .rest import BybitReadOnlyAdapter


@dataclass(frozen=True, slots=True)
class AcceptanceCheck:
    code: str
    passed: bool
    detail: str
    blocking: bool = True


@dataclass(frozen=True, slots=True)
class MainnetAcceptanceReport:
    schema: str
    workbench_version: str
    generated_at: datetime
    endpoint: str
    symbol: str
    server_time: datetime
    clock_offset_ms: int
    account_role: str
    api_key_note: str
    read_write: bool
    ip_binding_count: int
    deadline_day: int | None
    expired_at: datetime | None
    created_at: datetime | None
    key_type: int | None
    unified_account: bool
    contract_trade_permissions: tuple[str, ...]
    spot_permissions: tuple[str, ...]
    wallet_permissions: tuple[str, ...]
    options_usdc_permissions: tuple[str, ...]
    other_permissions: tuple[tuple[str, tuple[str, ...]], ...]
    account_type: str
    unified_margin_status: int | None
    margin_mode: str | None
    equity: Decimal
    available_balance: Decimal
    maker_fee_rate: Decimal | None
    taker_fee_rate: Decimal | None
    position_mode: str
    selected_symbol_leverages: tuple[Decimal | None, ...]
    open_positions: tuple[dict[str, Any], ...]
    open_orders: tuple[dict[str, Any], ...]
    instrument_rules: dict[str, Any]
    checks: tuple[AcceptanceCheck, ...]

    @property
    def micro_live_ready(self) -> bool:
        return all(check.passed for check in self.checks if check.blocking)

    def to_redacted_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            str(key): _json_safe(value) for key, value in asdict(self).items()
        }
        payload["micro_live_ready"] = self.micro_live_ready
        payload["secret_material_included"] = False
        payload["api_key_value_included"] = False
        payload["ip_addresses_included"] = False
        return payload

    def canonical_sha256(self) -> str:
        encoded = json.dumps(
            self.to_redacted_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class MainnetAcceptanceRunner:
    """GET-only Mainnet acceptance; this module has no write transport dependency."""

    def __init__(
        self,
        adapter: BybitReadOnlyAdapter,
        endpoint: str,
        *,
        expected_profile_name: str,
        max_clock_offset_ms: int = 750,
    ) -> None:
        selected = endpoint.rstrip("/")
        if not selected.startswith("https://"):
            raise ValueError("Mainnet endpoint must use https://")
        if not expected_profile_name.strip():
            raise ValueError("expected_profile_name is required")
        if max_clock_offset_ms < 0:
            raise ValueError("max_clock_offset_ms cannot be negative")
        self.adapter = adapter
        self.endpoint = selected
        self.expected_profile_name = expected_profile_name
        self.max_clock_offset_ms = max_clock_offset_ms

    async def run(
        self,
        symbol: str,
        *,
        local_time: datetime | None = None,
    ) -> MainnetAcceptanceReport:
        selected = symbol.strip().upper()
        if not selected:
            raise ValueError("symbol is required")
        if local_time is not None:
            local = local_time.astimezone(UTC)
            server_time = await self.adapter.server_time()
        else:
            local_before = datetime.now(UTC)
            server_time = await self.adapter.server_time()
            local_after = datetime.now(UTC)
            local = local_before + (local_after - local_before) / 2
        clock_offset_ms = int((server_time - local).total_seconds() * 1_000)
        key_info = await self.adapter.api_key_info()
        instrument = await self.adapter.instrument_rules(selected)
        account = await self.adapter.account_snapshot(selected)
        selected_rows = await self.adapter.position_rows(selected, require_one_way=False)
        positions = await self.adapter.contract_positions(require_one_way=False)
        orders = await self.adapter.contract_open_orders()

        position_mode = _position_mode(selected_rows)
        active_positions = tuple(
            _position_summary(item) for item in positions if item.position.quantity > 0
        )
        active_orders = tuple(_order_summary(item) for item in orders)
        checks = self._checks(
            key_info,
            account_type=account.account_type,
            unified_margin_status=account.unified_margin_status,
            margin_mode=account.margin_mode,
            maker_fee_rate=account.maker_fee_rate,
            taker_fee_rate=account.taker_fee_rate,
            position_mode=position_mode,
            selected_rows=selected_rows,
            active_positions=active_positions,
            active_orders=active_orders,
            clock_offset_ms=clock_offset_ms,
            now=local,
        )
        return MainnetAcceptanceReport(
            schema="mainnet-get-only-acceptance-v1",
            workbench_version=__version__,
            generated_at=datetime.now(UTC),
            endpoint=self.endpoint,
            symbol=selected,
            server_time=server_time,
            clock_offset_ms=clock_offset_ms,
            account_role="master" if key_info.is_master else "subaccount",
            api_key_note=key_info.note,
            read_write=not key_info.read_only,
            ip_binding_count=len(key_info.ip_bindings),
            deadline_day=key_info.deadline_day,
            expired_at=key_info.expired_at,
            created_at=key_info.created_at,
            key_type=key_info.key_type,
            unified_account=key_info.unified_account,
            contract_trade_permissions=key_info.permissions.contract_trade,
            spot_permissions=key_info.permissions.spot,
            wallet_permissions=key_info.permissions.wallet,
            options_usdc_permissions=key_info.permissions.options,
            other_permissions=key_info.permissions.other,
            account_type=account.account_type,
            unified_margin_status=account.unified_margin_status,
            margin_mode=account.margin_mode,
            equity=account.equity,
            available_balance=account.available_balance,
            maker_fee_rate=account.maker_fee_rate,
            taker_fee_rate=account.taker_fee_rate,
            position_mode=position_mode,
            selected_symbol_leverages=tuple(row.leverage for row in selected_rows),
            open_positions=active_positions,
            open_orders=active_orders,
            instrument_rules=_instrument_summary(instrument),
            checks=checks,
        )

    def _checks(
        self,
        key_info: ApiKeyInfo,
        *,
        account_type: str,
        unified_margin_status: int | None,
        margin_mode: str | None,
        maker_fee_rate: Decimal | None,
        taker_fee_rate: Decimal | None,
        position_mode: str,
        selected_rows: tuple[BybitPositionSnapshot, ...],
        active_positions: tuple[dict[str, Any], ...],
        active_orders: tuple[dict[str, Any], ...],
        clock_offset_ms: int,
        now: datetime,
    ) -> tuple[AcceptanceCheck, ...]:
        permissions = key_info.permissions
        required_contract = {"Order", "Position"}
        missing_contract = sorted(required_contract.difference(permissions.contract_trade))
        expiry_ok = key_info.expired_at is None or key_info.expired_at > now
        deadline_ok = key_info.deadline_day is None or key_info.deadline_day > 0
        leverage_ok = bool(selected_rows) and all(
            row.leverage == Decimal("1") for row in selected_rows
        )
        return (
            AcceptanceCheck(
                "endpoint.mainnet",
                self.endpoint in MAINNET_REST_URLS,
                f"selected endpoint={self.endpoint}",
            ),
            AcceptanceCheck(
                "clock.sync",
                abs(clock_offset_ms) <= self.max_clock_offset_ms,
                f"server/local offset={clock_offset_ms} ms",
            ),
            AcceptanceCheck(
                "key.exchange_note",
                True,
                (
                    f"exchange remark={key_info.note!r}; local credential profile="
                    f"{self.expected_profile_name!r}; labels are independent"
                ),
                blocking=False,
            ),
            AcceptanceCheck(
                "key.master_account",
                key_info.is_master and key_info.parent_uid is None,
                "master account required; subaccount is not accepted by this release",
            ),
            AcceptanceCheck(
                "key.read_write",
                not key_info.read_only,
                (
                    "Order/Position mutation capability is required later, "
                    "but this acceptance sends GET only"
                ),
            ),
            AcceptanceCheck(
                "key.personal_type",
                key_info.key_type == 1,
                f"key type={key_info.key_type}; personal type=1 required",
            ),
            AcceptanceCheck(
                "key.ip_bound",
                bool(key_info.ip_bindings),
                f"bound IP count={len(key_info.ip_bindings)}; addresses are omitted from report",
            ),
            AcceptanceCheck(
                "key.expiry",
                expiry_ok and deadline_ok,
                "expiry active and valid" if key_info.expired_at else "no active expiry reported",
            ),
            AcceptanceCheck(
                "permissions.contract_trade",
                not missing_contract,
                "Order and Position present"
                if not missing_contract
                else "missing: " + ", ".join(missing_contract),
            ),
            AcceptanceCheck(
                "permissions.spot_absent",
                not permissions.spot,
                "Spot permission absent" if not permissions.spot else ", ".join(permissions.spot),
            ),
            AcceptanceCheck(
                "permissions.options_usdc_absent",
                not permissions.options,
                "Options/USDC permission absent"
                if not permissions.options
                else ", ".join(permissions.options),
            ),
            AcceptanceCheck(
                "permissions.wallet_absent",
                not permissions.wallet,
                "Wallet transfer/withdraw permission absent"
                if not permissions.wallet
                else ", ".join(permissions.wallet),
            ),
            AcceptanceCheck(
                "permissions.other_absent",
                not _forbidden_other_permissions(permissions.other),
                "no forbidden additional permissions"
                if not _forbidden_other_permissions(permissions.other)
                else ", ".join(
                    name for name, _ in _forbidden_other_permissions(permissions.other)
                ),
            ),
            AcceptanceCheck(
                "account.unified_key",
                key_info.unified_account,
                "API key is bound to Unified Trading Account",
            ),
            AcceptanceCheck(
                "account.unified_wallet",
                account_type == "UNIFIED",
                f"accountType={account_type}",
            ),
            AcceptanceCheck(
                "account.uta2",
                unified_margin_status in {5, 6},
                f"unifiedMarginStatus={unified_margin_status}; expected 5 or 6",
            ),
            AcceptanceCheck(
                "account.isolated_margin",
                margin_mode == "ISOLATED_MARGIN",
                f"marginMode={margin_mode}; Micro-Live policy requires ISOLATED_MARGIN",
            ),
            AcceptanceCheck(
                "position.one_way",
                position_mode == "ONE_WAY",
                f"selected symbol mode={position_mode}",
            ),
            AcceptanceCheck(
                "position.leverage_1x",
                leverage_ok,
                "selected symbol leverage=1x"
                if leverage_ok
                else "selected symbol must be configured to 1x before Micro-Live",
            ),
            AcceptanceCheck(
                "account.no_open_positions",
                not active_positions,
                f"active contract positions={len(active_positions)}",
            ),
            AcceptanceCheck(
                "account.no_open_orders",
                not active_orders,
                f"active contract orders={len(active_orders)}",
            ),
            AcceptanceCheck(
                "fees.available",
                maker_fee_rate is not None and taker_fee_rate is not None,
                f"maker={maker_fee_rate} taker={taker_fee_rate}",
            ),
        )


def write_acceptance_report(report: MainnetAcceptanceReport, path: Path) -> tuple[Path, Path]:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_redacted_dict()
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sha_path = target.with_suffix(target.suffix + ".sha256")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    sha_path.write_text(f"{digest}  {target.name}\n", encoding="ascii")
    return target, sha_path


def _position_mode(rows: tuple[BybitPositionSnapshot, ...]) -> str:
    indexes = {row.position_idx for row in rows}
    if indexes and indexes.issubset({0}):
        return "ONE_WAY"
    if indexes.intersection({1, 2}):
        return "HEDGE"
    return "UNKNOWN"


def _position_summary(item: BybitPositionSnapshot) -> dict[str, Any]:
    return {
        "symbol": item.position.symbol,
        "position_idx": item.position_idx,
        "side": item.position.side.value,
        "quantity": str(item.position.quantity),
        "leverage": None if item.leverage is None else str(item.leverage),
        "stop_loss": None if item.stop_loss is None else str(item.stop_loss),
    }


def _order_summary(item: Order) -> dict[str, Any]:
    return {
        "symbol": item.request.symbol,
        "side": item.request.side.value,
        "order_type": item.request.order_type.value,
        "quantity": str(item.request.quantity),
        "remaining_quantity": str(item.remaining_quantity),
        "reduce_only": item.request.reduce_only,
        "role": item.request.role.value,
        "status": item.status.value,
    }


def _instrument_summary(rules: InstrumentRules) -> dict[str, Any]:
    return {
        "symbol": rules.symbol,
        "tick_size": str(rules.tick_size),
        "qty_step": str(rules.qty_step),
        "min_order_qty": str(rules.min_order_qty),
        "min_notional": str(rules.min_notional),
        "max_order_qty": str(rules.max_order_qty),
        "max_market_order_qty": (
            None if rules.max_market_order_qty is None else str(rules.max_market_order_qty)
        ),
    }


def _forbidden_other_permissions(
    permissions: tuple[tuple[str, tuple[str, ...]], ...],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (name, values)
        for name, values in permissions
        if name != "Derivatives" or any(value != "DerivativesTrade" for value in values)
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value
