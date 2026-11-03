"""Regression for P90 REAL open-position restart recovery diagnostics (#399)."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.p90l90_real_account_routes import (
    P90RealAccountRoute,
)
from bybit_workbench.universal_entry.p90l90_real_open_restart import (
    P90RealOpenRestartUnverified,
    _read_recovery_account_bounded,
)
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID
from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90PrivateIpcUnverified,
    P90PrivateProofClient,
    P90PrivateRecoveryBlocked,
    serve_private_proof,
)
from bybit_workbench.universal_exit.p90l90_real_recovery_account import (
    P90_REAL_RECOVERY_SAFE_FAILURE_CODES,
    P90_REAL_RECOVERY_TRANSIENT_FAILURE_CODES,
    P90RealRecoveryAccountUnverified,
)


def _route() -> P90RealAccountRoute:
    return P90RealAccountRoute(
        strategy_id=MAIN_ID,
        activation_id="activation-main",
        strategy_config_fingerprint="f" * 64,
        strategy_execution_binding_id="binding-main",
        trading_account_id="ta-" + "a" * 32,
        account_connector_id="connector-main",
        account_kind="MAIN",
        registered_exchange_uid="12345678",
    )


@pytest.mark.skipif(
    not hasattr(__import__("socket"), "AF_UNIX"),
    reason="requires local UNIX IPC",
)
def test_recovery_producer_returns_safe_blocker_instead_of_incomplete_frame() -> None:
    route = _route()
    ready = threading.Event()
    running = threading.Event()
    running.set()
    failures: list[str] = []

    def handler(_request):
        raise AssertionError("position proof should not be requested")

    def recovery_handler():
        raise P90RealRecoveryAccountUnverified(
            "P90_REAL_RECOVERY_PROTECTION_ORDER_SET_INCOMPLETE"
        )

    def producer() -> None:
        try:
            serve_private_proof(
                account=route.trading_account_id,
                connector=route.account_connector_id,
                handler=handler,
                recovery_handler=recovery_handler,
                running=running.is_set,
                ready=ready.set,
            )
        except Exception as exc:
            failures.append(type(exc).__name__)

    worker = threading.Thread(target=producer, daemon=True)
    worker.start()
    try:
        assert ready.wait(3.0), failures
        client = P90PrivateProofClient(
            account=route.trading_account_id,
            connector=route.account_connector_id,
            timeout_seconds=3.0,
        )
        with pytest.raises(P90PrivateRecoveryBlocked) as blocked:
            client.read_recovery_account()
        assert blocked.value.recovery_code == (
            "P90_REAL_RECOVERY_PROTECTION_ORDER_SET_INCOMPLETE"
        )
    finally:
        running.clear()
        worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert not failures


def test_recovery_safe_vocabulary_is_finite_and_transient_is_subset() -> None:
    assert P90_REAL_RECOVERY_SAFE_FAILURE_CODES
    assert P90_REAL_RECOVERY_TRANSIENT_FAILURE_CODES
    assert (
        P90_REAL_RECOVERY_TRANSIENT_FAILURE_CODES
        < P90_REAL_RECOVERY_SAFE_FAILURE_CODES
    )
    assert "P90_REAL_RECOVERY_NON_PROTECTION_OPEN_ORDER" in (
        P90_REAL_RECOVERY_SAFE_FAILURE_CODES
    )
    assert "P90_REAL_RECOVERY_NON_PROTECTION_OPEN_ORDER" not in (
        P90_REAL_RECOVERY_TRANSIENT_FAILURE_CODES
    )


def test_bounded_recovery_retries_transient_then_returns_proof() -> None:
    route = _route()
    outcomes: list[object] = [
        P90PrivateRecoveryBlocked(
            "P90_REAL_RECOVERY_ACCOUNT_GENERATION_UNVERIFIED"
        ),
        SimpleNamespace(trading_account_id=route.trading_account_id),
    ]
    timeouts: list[float] = []
    clock = [10.0]

    class Client:
        def __init__(self, **kwargs):
            timeouts.append(float(kwargs["timeout_seconds"]))

        def read_recovery_account(self):
            value = outcomes.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value

    proof = _read_recovery_account_bounded(
        route,
        monotonic=lambda: clock[0],
        sleeper=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        client_factory=Client,
    )
    assert proof.trading_account_id == route.trading_account_id
    assert len(timeouts) == 2
    assert all(0 < value <= 15.0 for value in timeouts)


def test_bounded_recovery_does_not_retry_structural_inventory_failure() -> None:
    route = _route()
    calls = 0

    class Client:
        def __init__(self, **_kwargs):
            pass

        def read_recovery_account(self):
            nonlocal calls
            calls += 1
            raise P90PrivateRecoveryBlocked(
                "P90_REAL_RECOVERY_NON_PROTECTION_OPEN_ORDER"
            )

    with pytest.raises(
        P90RealOpenRestartUnverified,
        match=(
            "P90_REAL_RESTART_MAIN_ACCOUNT_RECOVERY_BLOCKED:"
            "P90_REAL_RECOVERY_NON_PROTECTION_OPEN_ORDER"
        ),
    ):
        _read_recovery_account_bounded(route, client_factory=Client)
    assert calls == 1


def test_bounded_recovery_does_not_retry_structural_ipc_failure() -> None:
    route = _route()
    calls = 0

    class Client:
        def __init__(self, **_kwargs):
            pass

        def read_recovery_account(self):
            nonlocal calls
            calls += 1
            raise P90PrivateIpcUnverified("P90_PRIVATE_IPC_PEER_NOT_VERIFIED")

    with pytest.raises(
        P90RealOpenRestartUnverified,
        match="MAIN_ACCOUNT_RECOVERY_IPC_UNVERIFIED:.*PEER_NOT_VERIFIED",
    ):
        _read_recovery_account_bounded(route, client_factory=Client)
    assert calls == 1


def test_bounded_recovery_transport_budget_expires_with_last_safe_reason() -> None:
    route = _route()
    clock = [100.0]

    class Client:
        def __init__(self, **kwargs):
            self.timeout = float(kwargs["timeout_seconds"])

        def read_recovery_account(self):
            clock[0] += self.timeout
            raise P90PrivateIpcUnverified(
                "P90_PRIVATE_IPC_TRANSPORT_UNAVAILABLE"
            )

    with pytest.raises(
        P90RealOpenRestartUnverified,
        match=(
            "P90_REAL_RESTART_MAIN_ACCOUNT_RECOVERY_BUDGET_EXPIRED:"
            "P90_PRIVATE_IPC_TRANSPORT_UNAVAILABLE"
        ),
    ):
        _read_recovery_account_bounded(
            route,
            monotonic=lambda: clock[0],
            sleeper=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            client_factory=Client,
        )
    assert clock[0] <= 120.001
