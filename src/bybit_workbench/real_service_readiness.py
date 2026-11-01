"""Pure, fail-closed planning for owner-requested REAL service preparation.

No OS actions, database writes, Exchange credentials, LIVE arms or gate
mutations are performed here. Inventory must be externally verified as an
immutable exact-release account-route registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable, Mapping

_SHA = re.compile(r"^[0-9a-f]{40}$")
_UNIT = re.compile(r"^cripta-[a-z0-9][a-z0-9-]*(?:@[a-z0-9][a-z0-9-]*)?\.service$")
_ACCOUNT = re.compile(r"^ta-[a-z0-9]{32}$")
_CONNECTOR = re.compile(r"^ac-[a-z0-9]{32}$")


class ServiceReadinessError(ValueError):
    """Stable, operator-reportable fail-closed planning failure."""


class UnitState(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    ACTIVATING = "activating"
    DEACTIVATING = "deactivating"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ServiceSpec:
    unit: str
    dependencies: tuple[str, ...] = ()
    start_when_inactive: bool = False
    required_for_entry: bool = True


@dataclass(frozen=True)
class ServiceSnapshot:
    unit: str
    state: UnitState
    loaded_commit: str | None
    behavior_ready: bool


@dataclass(frozen=True)
class ServiceStartPlan:
    release_commit: str
    trading_account_id: str
    account_connector_id: str
    starts: tuple[str, ...]
    checks: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.starts and not self.blockers

    @property
    def may_start(self) -> bool:
        return bool(self.starts) and not self.blockers


def _scope_identity(release_commit: str, account_id: str, connector_id: str) -> None:
    if not _SHA.fullmatch(release_commit):
        raise ServiceReadinessError("RELEASE_IDENTITY_INVALID")
    if not _ACCOUNT.fullmatch(account_id):
        raise ServiceReadinessError("ACCOUNT_IDENTITY_INVALID")
    if not _CONNECTOR.fullmatch(connector_id):
        raise ServiceReadinessError("CONNECTOR_IDENTITY_INVALID")


def plan_service_starts(
    *,
    release_commit: str,
    trading_account_id: str,
    account_connector_id: str,
    verified_specs: Iterable[ServiceSpec],
    snapshots: Mapping[str, ServiceSnapshot],
) -> ServiceStartPlan:
    """Plan starts without doing them, blocking if any prerequisite is unsafe.

    verified_specs MUST be supplied by an independent trusted release/account
    route owner. This pure function cannot authenticate a caller's registry.
    """
    _scope_identity(release_commit, trading_account_id, account_connector_id)
    specs: dict[str, ServiceSpec] = {}
    for spec in verified_specs:
        if not _UNIT.fullmatch(spec.unit) or spec.unit in specs:
            raise ServiceReadinessError("SERVICE_REGISTRY_INVALID")
        specs[spec.unit] = spec
    if not specs:
        raise ServiceReadinessError("SERVICE_REGISTRY_EMPTY")
    for spec in specs.values():
        if len(set(spec.dependencies)) != len(spec.dependencies):
            raise ServiceReadinessError("SERVICE_REGISTRY_DUPLICATE_DEPENDENCY")
        if any(dep not in specs or dep == spec.unit for dep in spec.dependencies):
            raise ServiceReadinessError("SERVICE_REGISTRY_DEPENDENCY_INVALID")
    if set(snapshots) != set(specs):
        raise ServiceReadinessError("SERVICE_SNAPSHOT_INCOMPLETE")

    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ServiceReadinessError("SERVICE_REGISTRY_DEPENDENCY_CYCLE")
        if name in visited:
            return
        visiting.add(name)
        for dependency in sorted(specs[name].dependencies):
            visit(dependency)
        visiting.remove(name)
        visited.add(name)
        ordered.append(name)

    for name in sorted(specs):
        visit(name)

    starts: list[str] = []
    blockers: list[str] = []
    for name in ordered:
        spec = specs[name]
        snapshot = snapshots[name]
        if snapshot.unit != name or not isinstance(snapshot.state, UnitState):
            blockers.append(f"SERVICE_STATE_UNVERIFIED:{name}")
            continue
        if snapshot.loaded_commit is not None and snapshot.loaded_commit != release_commit:
            blockers.append(f"SERVICE_RELEASE_MISMATCH:{name}")
            continue
        if snapshot.state == UnitState.ACTIVE:
            if snapshot.loaded_commit is None:
                blockers.append(f"SERVICE_RELEASE_UNVERIFIED:{name}")
            elif spec.required_for_entry and not snapshot.behavior_ready:
                blockers.append(f"SERVICE_BEHAVIOR_NOT_READY:{name}")
        elif snapshot.state == UnitState.INACTIVE and spec.start_when_inactive:
            starts.append(name)
        else:
            blockers.append(f"SERVICE_NOT_RECOVERABLE:{name}:{snapshot.state.value}")

    if blockers:
        starts.clear()
    return ServiceStartPlan(
        release_commit=release_commit,
        trading_account_id=trading_account_id,
        account_connector_id=account_connector_id,
        starts=tuple(starts),
        checks=tuple(ordered),
        blockers=tuple(blockers),
    )
