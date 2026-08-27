import json
from dataclasses import dataclass, field
from typing import Protocol

from bybit_workbench.domain.types import AppMode


@dataclass(frozen=True, slots=True)
class BybitCredentials:
    profile: AppMode
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    name: str | None = None

    def __post_init__(self) -> None:
        if self.profile is AppMode.REPLAY:
            raise ValueError("Replay profile cannot contain Bybit credentials")
        if not self.api_key.strip() or not self.api_secret.strip():
            raise ValueError("API key and secret are required")
        if self.api_key != self.api_key.strip() or self.api_secret != self.api_secret.strip():
            raise ValueError("credentials cannot contain surrounding whitespace")
        if self.name is not None and not self.name.strip():
            raise ValueError("credential profile name cannot be blank")

    @property
    def masked_key(self) -> str:
        if len(self.api_key) <= 8:
            return "*" * len(self.api_key)
        return f"{self.api_key[:4]}…{self.api_key[-4:]}"

    @property
    def profile_name(self) -> str:
        return self.name or self.profile.value


class KeyringBackend(Protocol):
    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def get_password(self, service_name: str, username: str) -> str | None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class WindowsCredentialStore:
    SERVICE_PREFIX = "BybitStrategyWorkbench"
    USERNAME = "credentials"

    def __init__(self, backend: KeyringBackend | None = None) -> None:
        self._backend = backend

    def save(self, credentials: BybitCredentials) -> None:
        payload = json.dumps(
            {"api_key": credentials.api_key, "api_secret": credentials.api_secret},
            separators=(",", ":"),
        )
        self._keyring().set_password(
            self._service(credentials.profile, credentials.name),
            self.USERNAME,
            payload,
        )

    def load(self, profile: AppMode, *, name: str | None = None) -> BybitCredentials | None:
        self._validate_profile(profile)
        payload = self._keyring().get_password(self._service(profile, name), self.USERNAME)
        if payload is None:
            return None
        try:
            decoded = json.loads(payload)
            return BybitCredentials(profile, decoded["api_key"], decoded["api_secret"], name)
        except Exception as exc:
            label = name or profile.value
            raise RuntimeError(f"credential profile {label} is corrupted") from exc

    def delete(self, profile: AppMode, *, name: str | None = None) -> None:
        self._validate_profile(profile)
        try:
            self._keyring().delete_password(self._service(profile, name), self.USERNAME)
        except Exception as exc:
            if exc.__class__.__name__ != "PasswordDeleteError":
                raise

    def _keyring(self) -> KeyringBackend:
        if self._backend is None:
            try:
                import keyring
            except ImportError as exc:
                raise RuntimeError("keyring is not installed") from exc
            self._backend = keyring
        return self._backend

    @classmethod
    def _service(cls, profile: AppMode, name: str | None = None) -> str:
        cls._validate_profile(profile)
        label = name or profile.value
        if any(character in label for character in "\r\n\0"):
            raise ValueError("invalid credential profile name")
        return f"{cls.SERVICE_PREFIX}/{label}"

    @staticmethod
    def _validate_profile(profile: AppMode) -> None:
        if profile is AppMode.REPLAY:
            raise ValueError("Replay has no credential profile")
