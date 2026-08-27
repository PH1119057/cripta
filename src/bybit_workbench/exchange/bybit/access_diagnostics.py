from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AccessProbe:
    name: str
    ok: bool
    ret_code: Any = 0
    ret_msg: str = "OK"
    result: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MainnetAccessDiagnosticsReport:
    endpoint: str
    symbol: str
    lines: tuple[str, ...]


class MainnetAccessDiagnosticsRunner:
    """Non-ordering diagnostics for one selected Mainnet endpoint.

    The runner deliberately has no place/amend/cancel methods. When the account
    mode supports it, its only POST is Bybit's documented ``/v5/order/pre-check``
    endpoint, which calculates hypothetical margin effect and does not create an order.
    """

    def __init__(self, session: Any, endpoint: str) -> None:
        self._session = session
        self.endpoint = endpoint.rstrip("/")

    def run(
        self,
        *,
        symbol: str,
        side: str,
        quantity: str,
        price: str,
    ) -> MainnetAccessDiagnosticsReport:
        selected = symbol.strip().upper()
        if not selected:
            raise ValueError("symbol is required")
        normalized_side = side.strip().title()
        if normalized_side not in {"Buy", "Sell"}:
            raise ValueError("side must be Buy or Sell")
        if not quantity.strip() or not price.strip():
            raise ValueError("quantity and price are required for order pre-check")

        key = _probe(self._session, "get_api_key_information")
        account = _probe(self._session, "get_account_info")
        public_instrument = _probe(
            self._session,
            "get_instruments_info",
            category="linear",
            symbol=selected,
        )
        account_instrument = _probe(
            self._session,
            "get_account_instruments_info",
            category="linear",
            symbol=selected,
        )

        key_result = _result(key)
        account_result = _result(account)
        public_row = _single_symbol_row(public_instrument, selected)
        account_row = _single_symbol_row(account_instrument, selected)
        margin_mode = str(account_result.get("marginMode") or "").upper()
        pre_check_supported = account.ok and margin_mode in {"REGULAR_MARGIN", "PORTFOLIO_MARGIN"}

        limit_pre_check: AccessProbe | None = None
        market_pre_check: AccessProbe | None = None
        if pre_check_supported:
            limit_pre_check = _probe(
                self._session,
                "pre_check_order",
                category="linear",
                symbol=selected,
                side=normalized_side,
                orderType="Limit",
                qty=quantity.strip(),
                price=price.strip(),
                timeInForce="GTC",
                positionIdx=0,
                reduceOnly=False,
            )
            market_pre_check = _probe(
                self._session,
                "pre_check_order",
                category="linear",
                symbol=selected,
                side=normalized_side,
                orderType="Market",
                qty=quantity.strip(),
                positionIdx=0,
                reduceOnly=False,
            )

        lines = [
            f"Endpoint: {self.endpoint}",
            f"Symbol: {selected}",
            _probe_line("API key info", key),
        ]
        if key.ok:
            lines.extend(
                (
                    f"UID: {key_result.get('userID', '—')}",
                    f"KYC: level={key_result.get('kycLevel', '—')} "
                    f"region={key_result.get('kycRegion', '—')}",
                    f"Key: master={key_result.get('isMaster', '—')} "
                    f"readOnly={key_result.get('readOnly', '—')} "
                    f"uta={key_result.get('uta', '—')}",
                    f"Key metadata: type={key_result.get('type', '—')} "
                    f"createdAt={key_result.get('createdAt', '—')} "
                    f"ipBindings={_ip_binding_summary(key_result.get('ips'))}",
                    _permission_line(key_result.get("permissions")),
                )
            )
        lines.append(_probe_line("Account info", account))
        if account.ok:
            lines.append(
                f"Account: marginMode={account_result.get('marginMode', '—')} "
                f"unifiedMarginStatus={account_result.get('unifiedMarginStatus', '—')}"
            )

        lines.append(_probe_line("Public instrument", public_instrument))
        if public_instrument.ok:
            lines.append(_instrument_line("Public", public_row))

        lines.append(_probe_line("Account instrument", account_instrument))
        if account_instrument.ok:
            lines.append(_instrument_line("Account", account_row))
            lines.append(
                "Account availability: "
                + ("FOUND" if account_row is not None else "NOT FOUND")
            )

        if account.ok and margin_mode == "ISOLATED_MARGIN":
            lines.extend(
                (
                    "Limit pre-check (NO ORDER): SKIPPED — ISOLATED_MARGIN",
                    "Market pre-check (NO ORDER): SKIPPED — ISOLATED_MARGIN",
                    "Bybit /v5/order/pre-check does not support Isolated Margin; "
                    "this diagnostic does not determine whether /v5/order/create is permitted.",
                )
            )
        elif limit_pre_check is not None and market_pre_check is not None:
            lines.append(_probe_line("Limit pre-check (NO ORDER)", limit_pre_check))
            if limit_pre_check.ok:
                result = _result(limit_pre_check)
                lines.append(
                    "Limit pre-check margin: "
                    f"preIMR={result.get('preImrE4', '—')} "
                    f"postIMR={result.get('postImrE4', '—')} "
                    f"preMMR={result.get('preMmrE4', '—')} "
                    f"postMMR={result.get('postMmrE4', '—')}"
                )
            lines.append(_probe_line("Market pre-check (NO ORDER)", market_pre_check))
            if market_pre_check.ok:
                result = _result(market_pre_check)
                lines.append(
                    "Market pre-check margin: "
                    f"preIMR={result.get('preImrE4', '—')} "
                    f"postIMR={result.get('postImrE4', '—')} "
                    f"preMMR={result.get('preMmrE4', '—')} "
                    f"postMMR={result.get('postMmrE4', '—')}"
                )
            if not limit_pre_check.ok or not market_pre_check.ok:
                lines.append(
                    "Pre-check is diagnostic only; failure here did not create or retry an order."
                )
        else:
            shown_mode = margin_mode or "UNKNOWN"
            lines.append(
                "Order pre-check: SKIPPED — unsupported or unknown account margin mode: "
                f"{shown_mode}."
            )

        return MainnetAccessDiagnosticsReport(
            endpoint=self.endpoint,
            symbol=selected,
            lines=tuple(lines),
        )


def _ip_binding_summary(value: Any) -> str:
    if not isinstance(value, (list, tuple)) or not value:
        return "NONE"
    return str(len(value))


def _permission_line(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "Permissions: —"
    contract = value.get("ContractTrade")
    derivatives = value.get("Derivatives")
    contract_text = ",".join(str(item) for item in contract) if isinstance(contract, list) else "—"
    derivatives_text = (
        ",".join(str(item) for item in derivatives)
        if isinstance(derivatives, list)
        else "—"
    )
    return f"Permissions: ContractTrade={contract_text} Derivatives={derivatives_text}"


def _probe(session: Any, method_name: str, **kwargs: Any) -> AccessProbe:
    method = getattr(session, method_name, None)
    if method is None:
        return AccessProbe(
            method_name,
            False,
            ret_code="SDK_METHOD_MISSING",
            ret_msg=f"pybit session has no {method_name}()",
        )
    try:
        response = method(**kwargs)
    except Exception as exc:
        error_type = exc.__class__
        if error_type.__name__ == "InvalidRequestError" and error_type.__module__.startswith(
            "pybit"
        ):
            return AccessProbe(
                method_name,
                False,
                ret_code=getattr(exc, "status_code", None),
                ret_msg=_single_line(getattr(exc, "message", None) or str(exc)),
            )
        return AccessProbe(
            method_name,
            False,
            ret_code=exc.__class__.__name__,
            ret_msg=_single_line(str(exc)),
        )
    if not isinstance(response, Mapping):
        return AccessProbe(
            method_name,
            False,
            ret_code="INVALID_RESPONSE",
            ret_msg="response is not a mapping",
        )
    ret_code = response.get("retCode")
    ret_msg = _single_line(str(response.get("retMsg") or "")) or "OK"
    result = response.get("result")
    mapped_result = result if isinstance(result, Mapping) else None
    return AccessProbe(
        method_name,
        ret_code == 0,
        ret_code=ret_code,
        ret_msg=ret_msg,
        result=mapped_result,
    )


def _result(probe: AccessProbe) -> Mapping[str, Any]:
    return probe.result or {}


def _single_symbol_row(probe: AccessProbe, symbol: str) -> Mapping[str, Any] | None:
    if not probe.ok:
        return None
    rows = _result(probe).get("list")
    if not isinstance(rows, list):
        return None
    matches = [
        row
        for row in rows
        if isinstance(row, Mapping) and str(row.get("symbol") or "").upper() == symbol
    ]
    return matches[0] if len(matches) == 1 else None


def _instrument_line(prefix: str, row: Mapping[str, Any] | None) -> str:
    if row is None:
        return f"{prefix}: symbol row not returned"
    leverage = row.get("leverageFilter")
    max_leverage = leverage.get("maxLeverage") if isinstance(leverage, Mapping) else "—"
    return (
        f"{prefix}: status={row.get('status', '—')} "
        f"contract={row.get('contractType', '—')} "
        f"symbolType={row.get('symbolType', '—')} "
        f"unifiedMarginTrade={row.get('unifiedMarginTrade', '—')} "
        f"maxLeverage={max_leverage}"
    )


def _probe_line(label: str, probe: AccessProbe) -> str:
    status = "PASS" if probe.ok else "FAIL"
    return f"{label}: {status} retCode={probe.ret_code} retMsg={probe.ret_msg}"


def _single_line(value: str) -> str:
    compact = " ".join(value.splitlines()).strip()
    if " Request →" in compact:
        compact = compact.split(" Request →", 1)[0].strip()
    if len(compact) > 360:
        compact = compact[:357] + "..."
    return compact
