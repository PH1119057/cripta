import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from bybit_workbench.domain.types import AppMode

MAINNET_GLOBAL_REST_URL = "https://api.bybit.com"
MAINNET_KZ_REST_URL = "https://api.bybit.kz"
MAINNET_REST_URLS = frozenset({MAINNET_GLOBAL_REST_URL, MAINNET_KZ_REST_URL})
MAINNET_GLOBAL_PUBLIC_WS_URL = "wss://stream.bybit.com/v5/public/linear"
MAINNET_GLOBAL_PRIVATE_WS_URL = "wss://stream.bybit.com/v5/private"
MAINNET_KZ_PUBLIC_WS_URL = "wss://stream.bybit.kz/v5/public/linear"
MAINNET_KZ_PRIVATE_WS_URL = "wss://stream.bybit.kz/v5/private"


@dataclass(frozen=True, slots=True)
class EndpointProfile:
    mode: AppMode
    rest_url: str | None
    public_ws_url: str | None
    private_ws_url: str | None
    allows_network_orders: bool


PROFILES: dict[AppMode, EndpointProfile] = {
    AppMode.REPLAY: EndpointProfile(AppMode.REPLAY, None, None, None, False),
    AppMode.TESTNET: EndpointProfile(
        AppMode.TESTNET,
        "https://api-testnet.bybit.com",
        "wss://stream-testnet.bybit.com/v5/public/linear",
        "wss://stream-testnet.bybit.com/v5/private",
        True,
    ),
    AppMode.DEMO: EndpointProfile(
        AppMode.DEMO,
        "https://api-demo.bybit.com",
        "wss://stream.bybit.com/v5/public/linear",
        "wss://stream-demo.bybit.com/v5/private",
        True,
    ),
    AppMode.LIVE: EndpointProfile(
        AppMode.LIVE,
        MAINNET_GLOBAL_REST_URL,
        MAINNET_GLOBAL_PUBLIC_WS_URL,
        MAINNET_GLOBAL_PRIVATE_WS_URL,
        True,
    ),
}


def parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


class AppSettingsInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: AppMode
    database_path: Path
    allow_live_trading: bool
    enable_testnet_execution: bool
    rest_url_override: str | None = None
    public_ws_url_override: str | None = None
    private_ws_url_override: str | None = None
    credential_profile_name: str = "BotW-Mainnet"


@dataclass(frozen=True, slots=True)
class AppSettings:
    mode: AppMode = AppMode.REPLAY
    database_path: Path = Path("var/workbench.db")
    allow_live_trading: bool = False
    enable_testnet_execution: bool = False
    rest_url_override: str | None = None
    public_ws_url_override: str | None = None
    private_ws_url_override: str | None = None
    credential_profile_name: str = "BotW-Mainnet"

    @classmethod
    def from_environment(cls) -> "AppSettings":
        raw_mode = os.getenv("BYBIT_WORKBENCH_PROFILE", AppMode.REPLAY.value).lower()
        try:
            mode = AppMode(raw_mode)
        except ValueError as exc:
            raise ValueError(f"unknown application profile: {raw_mode!r}") from exc
        validated = AppSettingsInput.model_validate(
            {
                "mode": mode,
                "database_path": Path(os.getenv("BYBIT_WORKBENCH_DB_PATH", "var/workbench.db")),
                "allow_live_trading": parse_bool(os.getenv("BYBIT_WORKBENCH_ALLOW_LIVE_TRADING")),
                "enable_testnet_execution": parse_bool(
                    os.getenv("BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION")
                ),
                "rest_url_override": os.getenv("BYBIT_WORKBENCH_REST_URL") or None,
                "public_ws_url_override": (os.getenv("BYBIT_WORKBENCH_PUBLIC_WS_URL") or None),
                "private_ws_url_override": (os.getenv("BYBIT_WORKBENCH_PRIVATE_WS_URL") or None),
                "credential_profile_name": (
                    os.getenv("BYBIT_WORKBENCH_CREDENTIAL_PROFILE") or "BotW-Mainnet"
                ),
            }
        )
        return cls(**validated.model_dump())

    def validate_startup(self) -> None:
        if self.enable_testnet_execution and self.mode is not AppMode.TESTNET:
            raise PermissionError("Testnet execution can only be enabled in the Testnet profile")
        for name, value, scheme in (
            ("rest_url_override", self.rest_url_override, "https"),
            ("public_ws_url_override", self.public_ws_url_override, "wss"),
            ("private_ws_url_override", self.private_ws_url_override, "wss"),
        ):
            if value is not None and urlparse(value).scheme != scheme:
                raise ValueError(f"{name} must use {scheme}://")
        if not self.credential_profile_name.strip():
            raise ValueError("credential_profile_name is required")

    @property
    def endpoint_profile(self) -> EndpointProfile:
        base = PROFILES[self.mode]
        selected_rest = self.rest_url_override or base.rest_url
        default_public = base.public_ws_url
        default_private = base.private_ws_url
        if self.mode is AppMode.LIVE:
            if selected_rest == MAINNET_KZ_REST_URL:
                default_public = MAINNET_KZ_PUBLIC_WS_URL
                default_private = MAINNET_KZ_PRIVATE_WS_URL
            elif selected_rest == MAINNET_GLOBAL_REST_URL:
                default_public = MAINNET_GLOBAL_PUBLIC_WS_URL
                default_private = MAINNET_GLOBAL_PRIVATE_WS_URL
        return EndpointProfile(
            mode=self.mode,
            rest_url=selected_rest,
            public_ws_url=self.public_ws_url_override or default_public,
            private_ws_url=self.private_ws_url_override or default_private,
            allows_network_orders=base.allows_network_orders,
        )

    @property
    def testnet_execution_allowed(self) -> bool:
        return self.mode is AppMode.TESTNET and self.enable_testnet_execution

    @property
    def is_mainnet(self) -> bool:
        return self.mode is AppMode.LIVE
