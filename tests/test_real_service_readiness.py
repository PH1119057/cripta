import pytest

from bybit_workbench.real_service_readiness import (
    ServiceReadinessError,
    ServiceSnapshot,
    ServiceSpec,
    UnitState,
    plan_service_starts,
)

SHA = "a" * 40
ACCOUNT = "ta-" + "1" * 32
CONNECTOR = "ac-" + "2" * 32


def plan(specs, states, sha=SHA):
    return plan_service_starts(
        release_commit=sha,
        trading_account_id=ACCOUNT,
        account_connector_id=CONNECTOR,
        verified_specs=specs,
        snapshots=states,
    )


def snap(unit, state, *, behavior=True, sha=SHA):
    return ServiceSnapshot(unit, state, sha, behavior)


def test_start_dependencies_first_and_never_start_active():
    specs = [
        ServiceSpec("cripta-entry.service", ("cripta-private.service",), True),
        ServiceSpec("cripta-private.service", (), True),
    ]
    snapshots = {
        "cripta-private.service": snap("cripta-private.service", UnitState.INACTIVE),
        "cripta-entry.service": snap("cripta-entry.service", UnitState.INACTIVE),
    }
    result = plan(specs, snapshots)
    assert result.may_start
    assert result.starts == ("cripta-private.service", "cripta-entry.service")
    assert not result.ready
    snapshots["cripta-private.service"] = snap("cripta-private.service", UnitState.ACTIVE)
    assert plan(specs, snapshots).starts == ("cripta-entry.service",)


def test_all_active_ready():
    specs = [ServiceSpec("cripta-entry.service")]
    result = plan(specs, {"cripta-entry.service": snap("cripta-entry.service", UnitState.ACTIVE)})
    assert result.ready and not result.starts


@pytest.mark.parametrize("state", [
    UnitState.FAILED, UnitState.UNKNOWN, UnitState.ACTIVATING, UnitState.DEACTIVATING
])
def test_non_inactive_states_block_without_any_start(state):
    specs = [ServiceSpec("cripta-entry.service", (), True),
             ServiceSpec("cripta-exit.service", (), True)]
    result = plan(specs, {
        "cripta-entry.service": snap("cripta-entry.service", UnitState.INACTIVE),
        "cripta-exit.service": snap("cripta-exit.service", state),
    })
    assert not result.may_start
    assert result.starts == ()
    assert result.blockers


def test_active_behavior_not_ready_blocks_without_restart():
    unit = "cripta-entry.service"
    result = plan([ServiceSpec(unit, (), True)],
                  {unit: snap(unit, UnitState.ACTIVE, behavior=False)})
    assert result.blockers == (f"SERVICE_BEHAVIOR_NOT_READY:{unit}",)
    assert not result.starts


def test_exact_release_mismatch_blocks():
    unit = "cripta-private.service"
    result = plan([ServiceSpec(unit, (), True)],
                  {unit: snap(unit, UnitState.INACTIVE, sha="b" * 40)})
    assert result.blockers == (f"SERVICE_RELEASE_MISMATCH:{unit}",)


def test_active_missing_release_proof_blocks():
    unit = "cripta-entry.service"
    result = plan([ServiceSpec(unit)],
                  {unit: snap(unit, UnitState.ACTIVE, sha=None)})
    assert result.blockers == (f"SERVICE_RELEASE_UNVERIFIED:{unit}",)


def test_unknown_or_missing_service_block():
    specs = [ServiceSpec("cripta-entry.service")]
    with pytest.raises(ServiceReadinessError, match="SERVICE_SNAPSHOT_INCOMPLETE"):
        plan(specs, {})
    with pytest.raises(ServiceReadinessError, match="SERVICE_REGISTRY_INVALID"):
        plan([ServiceSpec("evil.service;rm -rf /")], {})


def test_invalid_scope_rejected():
    unit = "cripta-entry.service"
    with pytest.raises(ServiceReadinessError, match="RELEASE_IDENTITY_INVALID"):
        plan([ServiceSpec(unit)], {unit: snap(unit, UnitState.ACTIVE)}, sha="main")


def test_cycle_and_missing_dependency_rejected():
    one = "cripta-one.service"
    two = "cripta-two.service"
    with pytest.raises(ServiceReadinessError, match="SERVICE_REGISTRY_DEPENDENCY_CYCLE"):
        plan([ServiceSpec(one, (two,)), ServiceSpec(two, (one,))],
             {one: snap(one, UnitState.ACTIVE), two: snap(two, UnitState.ACTIVE)})
    with pytest.raises(ServiceReadinessError, match="SERVICE_REGISTRY_DEPENDENCY_INVALID"):
        plan([ServiceSpec(one, (two,))], {one: snap(one, UnitState.ACTIVE)})
