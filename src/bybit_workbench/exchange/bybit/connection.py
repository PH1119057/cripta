from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from bybit_workbench.app.config import (
    MAINNET_GLOBAL_REST_URL,
    MAINNET_KZ_REST_URL,
    PROFILES,
    AppSettings,
)
from bybit_workbench.app.credentials import BybitCredentials
from bybit_workbench.domain.types import AppMode

from .acceptance import MainnetAcceptanceRunner
from .access_diagnostics import MainnetAccessDiagnosticsRunner
from .direct_ws import DirectBybitWebSocketBridge
from .health import HealthMonitor
from .mainnet_connection_test import MainnetConnectionTester
from .pybit_ws import PybitWebSocketBridge
from .rate_limit import AsyncRateLimiter
from .rest import BybitReadOnlyAdapter
from .streams import BybitStreamProcessor
from .testnet_execution import BybitTestnetExecutionAdapter
from .transport import PybitReadOnlyTransport
from .write_transport import PybitTestnetWriteTransport, _PybitMainnetWriteTransport

if TYPE_CHECKING:
    from bybit_workbench.execution.mainnet_safety import (
        ExecutionArmingController,
        IdempotencyStore,
        MainnetMutationGateway,
        MainnetSafetyStateProvider,
    )
    from bybit_workbench.execution.mainnet_state import MainnetReadinessContext


@dataclass(frozen=True, slots=True)
class PybitConstructors:
    http: Callable[..., Any]
    websocket: Callable[..., Any]


@dataclass(slots=True)
class ReadOnlyBybitConnection:
    adapter: BybitReadOnlyAdapter
    processor: BybitStreamProcessor
    bridge: PybitWebSocketBridge | DirectBybitWebSocketBridge
    health: HealthMonitor

    def close(self) -> None:
        self.bridge.close()


@dataclass(frozen=True, slots=True)
class MainnetExecutionConnection:
    """Separated Mainnet read/write routes behind one safety boundary."""

    gateway: MainnetMutationGateway
    reader: BybitReadOnlyAdapter
    state_provider: MainnetSafetyStateProvider


def create_read_only_connection(
    settings: AppSettings,
    credentials: BybitCredentials,
    symbol: str,
    *,
    constructors: PybitConstructors | None = None,
) -> ReadOnlyBybitConnection:
    settings.validate_startup()
    if settings.mode is AppMode.REPLAY:
        raise ValueError("Replay mode cannot create a Bybit connection")
    if credentials.profile is not settings.mode:
        raise ValueError("credential profile does not match application mode")
    using_default_constructors = constructors is None
    if constructors is None:
        _require_default_pybit_endpoints(settings)
        constructors = _load_pybit_constructors()
    testnet = settings.mode is AppMode.TESTNET
    demo = settings.mode is AppMode.DEMO
    common_private = {
        "testnet": testnet,
        "demo": demo,
        "api_key": credentials.api_key,
        "api_secret": credentials.api_secret,
        **_regional_session_kwargs(settings.endpoint_profile.rest_url),
    }
    http_session = constructors.http(
        **common_private,
        **_read_only_http_resilience_kwargs(),
    )
    _select_http_endpoint(http_session, settings.endpoint_profile.rest_url)
    websocket_policy = {
        "retries": 3,
        "restart_on_error": False,
    }
    health = HealthMonitor()
    processor = BybitStreamProcessor(symbol, health)
    adapter = BybitReadOnlyAdapter(PybitReadOnlyTransport(http_session))
    endpoint_profile = settings.endpoint_profile
    bridge: PybitWebSocketBridge | DirectBybitWebSocketBridge
    if (
        using_default_constructors
        and settings.mode is AppMode.LIVE
        and endpoint_profile.rest_url in {MAINNET_GLOBAL_REST_URL, MAINNET_KZ_REST_URL}
    ):
        public_url = endpoint_profile.public_ws_url
        private_url = endpoint_profile.private_ws_url
        if public_url is None or private_url is None:
            raise ValueError("regional Mainnet WebSocket URLs are required")
        bridge = DirectBybitWebSocketBridge(
            public_url,
            private_url,
            credentials,
            processor,
            health,
        )
    else:
        public_session = constructors.websocket(
            testnet=testnet,
            demo=False,
            channel_type="linear",
            **websocket_policy,
            **_regional_session_kwargs(endpoint_profile.rest_url),
        )
        private_session = constructors.websocket(
            **common_private,
            channel_type="private",
            **websocket_policy,
        )
        bridge = PybitWebSocketBridge(public_session, private_session, processor, health)
    return ReadOnlyBybitConnection(adapter, processor, bridge, health)


def create_mainnet_connection_tester(
    settings: AppSettings,
    credentials: BybitCredentials,
    *,
    constructors: PybitConstructors | None = None,
) -> MainnetConnectionTester:
    """Create a diagnostic that owns no write methods and performs no automatic fallback."""

    settings.validate_startup()
    if settings.mode is not AppMode.LIVE:
        raise ValueError("Mainnet connection test requires the live network profile")
    if credentials.profile is not AppMode.LIVE:
        raise ValueError("Mainnet connection test requires Mainnet credentials")
    if credentials.name is not None and credentials.name != settings.credential_profile_name:
        raise ValueError("credential profile name does not match application settings")
    constructors = constructors or _load_pybit_constructors()
    session = constructors.http(
        testnet=False,
        demo=False,
        api_key=credentials.api_key,
        api_secret=credentials.api_secret,
        **_regional_session_kwargs(settings.endpoint_profile.rest_url),
        **_read_only_http_resilience_kwargs(),
    )
    endpoint = settings.endpoint_profile.rest_url
    if endpoint is None:
        raise ValueError("Mainnet REST endpoint is required")
    _select_http_endpoint(session, endpoint)
    return MainnetConnectionTester(
        BybitReadOnlyAdapter(PybitReadOnlyTransport(session)),
        endpoint,
    )


def create_mainnet_access_diagnostics_runner(
    settings: AppSettings,
    credentials: BybitCredentials,
    *,
    constructors: PybitConstructors | None = None,
) -> MainnetAccessDiagnosticsRunner:
    """Build a non-ordering Mainnet access diagnostic for the selected endpoint."""

    settings.validate_startup()
    if settings.mode is not AppMode.LIVE:
        raise ValueError("Mainnet access diagnostics require LIVE mode")
    if credentials.profile is not AppMode.LIVE:
        raise ValueError("Mainnet access diagnostics require Mainnet credentials")
    if credentials.name is not None and credentials.name != settings.credential_profile_name:
        raise ValueError("credential profile name does not match application settings")
    endpoint = settings.endpoint_profile.rest_url
    if endpoint is None:
        raise ValueError("Mainnet REST endpoint is required")
    constructors = constructors or _load_pybit_constructors()
    session = constructors.http(
        testnet=False,
        demo=False,
        api_key=credentials.api_key,
        api_secret=credentials.api_secret,
        **_regional_session_kwargs(endpoint),
        **_read_only_http_resilience_kwargs(),
    )
    _select_http_endpoint(session, endpoint)
    return MainnetAccessDiagnosticsRunner(session, endpoint)


def create_mainnet_acceptance_runner(
    settings: AppSettings,
    credentials: BybitCredentials,
    *,
    constructors: PybitConstructors | None = None,
) -> MainnetAcceptanceRunner:
    """Create the pass-7 GET-only acceptance runner with no write transport."""

    settings.validate_startup()
    if settings.mode is not AppMode.LIVE:
        raise ValueError("Mainnet acceptance requires the live network profile")
    if credentials.profile is not AppMode.LIVE:
        raise ValueError("Mainnet acceptance requires Mainnet credentials")
    if credentials.name is not None and credentials.name != settings.credential_profile_name:
        raise ValueError("credential profile name does not match application settings")
    constructors = constructors or _load_pybit_constructors()
    endpoint = settings.endpoint_profile.rest_url
    if endpoint is None:
        raise ValueError("Mainnet REST endpoint is required")
    session = constructors.http(
        testnet=False,
        demo=False,
        api_key=credentials.api_key,
        api_secret=credentials.api_secret,
        **_regional_session_kwargs(endpoint),
        **_read_only_http_resilience_kwargs(),
    )
    _select_http_endpoint(session, endpoint)
    return MainnetAcceptanceRunner(
        BybitReadOnlyAdapter(PybitReadOnlyTransport(session)),
        endpoint,
        expected_profile_name=settings.credential_profile_name,
    )


def create_testnet_execution_adapter(
    settings: AppSettings,
    credentials: BybitCredentials,
    *,
    constructors: PybitConstructors | None = None,
) -> BybitTestnetExecutionAdapter:
    settings.validate_startup()
    if not settings.testnet_execution_allowed:
        raise PermissionError("Testnet execution is locked by the external configuration switch")
    if credentials.profile is not AppMode.TESTNET:
        raise ValueError("Testnet execution requires Testnet credentials")
    if constructors is None:
        _require_default_pybit_endpoints(settings)
        constructors = _load_pybit_constructors()
    session = constructors.http(
        testnet=True,
        demo=False,
        api_key=credentials.api_key,
        api_secret=credentials.api_secret,
    )
    limiter = AsyncRateLimiter()
    read_adapter = BybitReadOnlyAdapter(PybitReadOnlyTransport(session, limiter))
    write_transport = PybitTestnetWriteTransport(session, settings.mode, limiter)
    return BybitTestnetExecutionAdapter(write_transport, read_adapter)


def create_mainnet_mutation_gateway(
    settings: AppSettings,
    credentials: BybitCredentials,
    arming: ExecutionArmingController,
    idempotency: IdempotencyStore,
    *,
    state_provider: MainnetSafetyStateProvider | None = None,
    constructors: PybitConstructors | None = None,
) -> MainnetMutationGateway:
    """Build the only supported Mainnet write path; starts blocked by its arming controller."""

    from bybit_workbench.execution.mainnet_safety import MainnetMutationGateway

    settings.validate_startup()
    if settings.mode is not AppMode.LIVE or credentials.profile is not AppMode.LIVE:
        raise PermissionError("Mainnet mutation gateway requires Mainnet settings and credentials")
    if credentials.name is not None and credentials.name != settings.credential_profile_name:
        raise ValueError("credential profile name does not match application settings")
    constructors = constructors or _load_pybit_constructors()
    session = constructors.http(
        testnet=False,
        demo=False,
        api_key=credentials.api_key,
        api_secret=credentials.api_secret,
        **_regional_session_kwargs(settings.endpoint_profile.rest_url),
        **_mainnet_write_http_kwargs(),
    )
    endpoint = settings.endpoint_profile.rest_url
    if endpoint is None:
        raise ValueError("Mainnet REST endpoint is required")
    _select_http_endpoint(session, endpoint)
    raw_delegate = _PybitMainnetWriteTransport(session, AppMode.LIVE)
    return MainnetMutationGateway(
        raw_delegate,
        arming,
        idempotency,
        state_provider,
        endpoint=endpoint,
    )


def create_mainnet_execution_connection(
    settings: AppSettings,
    credentials: BybitCredentials,
    arming: ExecutionArmingController,
    idempotency: IdempotencyStore,
    context_provider: Callable[[], MainnetReadinessContext | None],
    *,
    constructors: PybitConstructors | None = None,
) -> MainnetExecutionConnection:
    """Construct the sole desktop Mainnet write route and its GET verifier."""

    from bybit_workbench.execution.mainnet_safety import MainnetMutationGateway
    from bybit_workbench.execution.mainnet_state import (
        RestBackedMainnetSafetyStateProvider,
    )

    settings.validate_startup()
    if settings.mode is not AppMode.LIVE or credentials.profile is not AppMode.LIVE:
        raise PermissionError("Mainnet execution connection requires Mainnet credentials")
    if credentials.name is not None and credentials.name != settings.credential_profile_name:
        raise ValueError("credential profile name does not match application settings")
    endpoint = settings.endpoint_profile.rest_url
    if endpoint is None:
        raise ValueError("Mainnet REST endpoint is required")
    constructors = constructors or _load_pybit_constructors()
    # Keep the mutation session deliberately non-retrying: an ambiguous POST must
    # never be replayed blindly.  All preflight/reconciliation GETs use a separate
    # read-only session with the conservative retry policy used by the desktop
    # read connection.
    write_session = constructors.http(
        testnet=False,
        demo=False,
        api_key=credentials.api_key,
        api_secret=credentials.api_secret,
        **_regional_session_kwargs(endpoint),
        **_mainnet_write_http_kwargs(),
    )
    read_session = constructors.http(
        testnet=False,
        demo=False,
        api_key=credentials.api_key,
        api_secret=credentials.api_secret,
        **_regional_session_kwargs(endpoint),
        **_read_only_http_resilience_kwargs(),
    )
    _select_http_endpoint(write_session, endpoint)
    _select_http_endpoint(read_session, endpoint)
    limiter = AsyncRateLimiter()
    reader = BybitReadOnlyAdapter(PybitReadOnlyTransport(read_session, limiter))
    provider = RestBackedMainnetSafetyStateProvider(
        reader,
        endpoint,
        context_provider,
    )
    gateway = MainnetMutationGateway(
        _PybitMainnetWriteTransport(write_session, AppMode.LIVE, limiter),
        arming,
        idempotency,
        provider,
        endpoint=endpoint,
    )
    return MainnetExecutionConnection(gateway, reader, provider)



def set_mainnet_symbol_leverage(
    settings: AppSettings,
    credentials: BybitCredentials,
    symbol: str,
    leverage: str,
    *,
    constructors: PybitConstructors | None = None,
) -> str:
    """Set one-way linear leverage on the selected Mainnet symbol.

    This is an explicit operator configuration action, intentionally kept outside
    the execution mutation gateway. Entry submission remains independently armed
    and safety-gated.
    """

    settings.validate_startup()
    if settings.mode is not AppMode.LIVE:
        raise PermissionError("leverage configuration requires the LIVE profile")
    if credentials.profile is not AppMode.LIVE:
        raise PermissionError("leverage configuration requires Mainnet credentials")
    if credentials.name is not None and credentials.name != settings.credential_profile_name:
        raise ValueError("credential profile name does not match application settings")
    selected_symbol = symbol.strip().upper()
    if not selected_symbol:
        raise ValueError("symbol is required")
    selected_leverage = leverage.strip()
    if selected_leverage not in {"1", "2", "3", "5", "7", "10"}:
        raise ValueError("supported leverage values are 1, 2, 3, 5, 7, 10")
    endpoint = settings.endpoint_profile.rest_url
    if endpoint is None:
        raise ValueError("Mainnet REST endpoint is required")
    constructors = constructors or _load_pybit_constructors()
    session = constructors.http(
        testnet=False,
        demo=False,
        api_key=credentials.api_key,
        api_secret=credentials.api_secret,
        **_regional_session_kwargs(endpoint),
        **_read_only_http_resilience_kwargs(),
    )
    _select_http_endpoint(session, endpoint)
    # Do fresh server-side guards here instead of trusting the GUI read-only
    # snapshot.  This keeps leverage configuration usable while the read-only
    # runtime is reconnecting, without allowing a stale/disconnected GUI state
    # to change leverage underneath an open position or active order.
    position_rows = _pybit_result_rows(
        session.get_positions(category="linear", symbol=selected_symbol),
        "position precheck",
    )
    if any(_positive_decimal(row.get("size")) for row in position_rows):
        raise RuntimeError("нельзя менять плечо: на Bybit есть открытая позиция по символу")
    order_rows = _pybit_result_rows(
        session.get_open_orders(category="linear", symbol=selected_symbol, openOnly=0),
        "open-order precheck",
    )
    if order_rows:
        raise RuntimeError("нельзя менять плечо: на Bybit есть открытые ордера по символу")

    response = session.set_leverage(
        category="linear",
        symbol=selected_symbol,
        buyLeverage=selected_leverage,
        sellLeverage=selected_leverage,
    )
    if not isinstance(response, Mapping):
        raise TypeError("Bybit set-leverage response must be a mapping")
    ret_code = int(response.get("retCode", -1))
    # 110043 means the requested leverage was already active.  Treat it as
    # idempotent success rather than showing an operator error.
    if ret_code not in {0, 110043}:
        message = str(response.get("retMsg") or "unknown Bybit error")
        raise RuntimeError(f"Bybit set leverage failed: retCode={ret_code} {message}")
    return selected_leverage


def _pybit_result_rows(response: object, label: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(response, Mapping):
        raise TypeError(f"Bybit {label} response must be a mapping")
    ret_code = int(response.get("retCode", -1))
    if ret_code != 0:
        message = str(response.get("retMsg") or "unknown Bybit error")
        raise RuntimeError(f"Bybit {label} failed: retCode={ret_code} {message}")
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise TypeError(f"Bybit {label} result must be a mapping")
    rows = result.get("list")
    if not isinstance(rows, list):
        raise TypeError(f"Bybit {label} result.list must be a list")
    if not all(isinstance(row, Mapping) for row in rows):
        raise TypeError(f"Bybit {label} result.list contains a non-mapping row")
    return tuple(row for row in rows if isinstance(row, Mapping))


def _positive_decimal(value: object) -> bool:
    try:
        return Decimal(str(value or "0")) > 0
    except (InvalidOperation, ValueError):
        return False


def _load_pybit_constructors() -> PybitConstructors:
    try:
        import pybit.unified_trading as unified_trading  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("pybit is not installed") from exc
    _ensure_pybit_regional_public_ws_template(unified_trading)
    return PybitConstructors(unified_trading.HTTP, unified_trading.WebSocket)


def _ensure_pybit_regional_public_ws_template(module: Any) -> None:
    """Patch pybit 5.17 public WS template so regional TLDs actually take effect.

    pybit 5.17 accepts ``tld="kz"`` in its WebSocket manager, but its public
    V5 template hard-codes ``.com``. Private WS already uses ``{TLD}``.
    We only rewrite the known upstream template and fail closed if a future
    pybit release changes it in an unexpected way.
    """

    regional_template = "wss://{SUBDOMAIN}.{DOMAIN}.{TLD}/v5/public/{CHANNEL_TYPE}"
    current = getattr(module, "PUBLIC_WSS", None)
    if current == regional_template:
        return
    if current == "wss://{SUBDOMAIN}.{DOMAIN}.com/v5/public/{CHANNEL_TYPE}":
        module.PUBLIC_WSS = regional_template
        return
    raise RuntimeError(
        "unsupported pybit public WebSocket template; refusing implicit endpoint fallback"
    )


def _require_default_pybit_endpoints(settings: AppSettings) -> None:
    base = PROFILES[settings.mode]
    selected = settings.endpoint_profile
    if settings.mode is AppMode.LIVE:
        if selected.rest_url not in {MAINNET_GLOBAL_REST_URL, MAINNET_KZ_REST_URL}:
            raise ValueError(
                "desktop Mainnet supports only api.bybit.kz and api.bybit.com; "
                "inject a custom transport for any other endpoint"
            )
        if (
            settings.public_ws_url_override is not None
            or settings.private_ws_url_override is not None
        ):
            raise ValueError(
                "custom Mainnet WebSocket overrides require an explicitly injected "
                "transport/session"
            )
        return
    if (
        selected.public_ws_url != base.public_ws_url
        or selected.private_ws_url != base.private_ws_url
    ):
        raise ValueError(
            "custom WebSocket overrides require an explicitly injected transport/session"
        )


def _select_http_endpoint(session: Any, endpoint: str | None) -> None:
    if endpoint is None:
        return
    selected = endpoint.rstrip("/")
    if selected not in {MAINNET_GLOBAL_REST_URL, MAINNET_KZ_REST_URL} and not selected.startswith(
        "https://"
    ):
        raise ValueError("custom Bybit REST endpoint must use https://")
    if hasattr(session, "endpoint"):
        session.endpoint = selected


def _mainnet_write_http_kwargs() -> dict[str, Any]:
    """KZ-friendly write timing without retrying ambiguous network failures.

    The authenticated timestamp is created before requests establishes or reuses the
    HTTPS connection, so the default five-second receive window is unnecessarily
    tight on a higher-latency regional route.  A longer timeout/window gives one
    mutation attempt room to complete while ``force_retry=False`` preserves the
    no-blind-retry invariant for network errors.
    """

    return {
        "timeout": 20,
        "recv_window": 10_000,
        "force_retry": False,
    }


def _read_only_http_resilience_kwargs() -> dict[str, Any]:
    """Conservative retry policy for GET-only sessions.

    pybit retries network errors only when ``force_retry`` is enabled. This helper
    must never be applied to a write-capable HTTP session because retrying an
    ambiguous POST could duplicate a mutation.
    """

    return {
        "timeout": 20,
        "force_retry": True,
        "max_retries": 2,
        "retry_delay": 1,
    }


def _regional_session_kwargs(endpoint: str | None) -> dict[str, str]:
    if endpoint == MAINNET_KZ_REST_URL:
        return {"domain": "bybit", "tld": "kz"}
    return {}
