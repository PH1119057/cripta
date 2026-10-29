"""Independent MAIN/SUB private evidence cannot cross a REAL Exit boundary."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from test_p90l90_real_observation import _scope

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_exit import p90l90_real_private_router as module
from bybit_workbench.universal_exit.p90l90_real_private_ipc import (
    P90PrivateIpcUnverified,
)

MAIN = "ta-" + "1" * 32
SUB = "ta-" + "2" * 32
MAIN_CONNECTOR = "ac-main-proven"
SUB_CONNECTOR = "ac-sub-proven"


def _position_and_evidence(*, sub: bool = False):
    position, _plan, _state, cursor, evidence, _price = _scope(sub=sub)
    account = SUB if sub else MAIN
    position = replace(
        position,
        trading_account_id=account,
        exchange_position_key=exchange_position_key(account, position.symbol, 0),
    )
    evidence = replace(
        evidence,
        trading_account_id=account,
        connector_id=SUB_CONNECTOR if sub else MAIN_CONNECTOR,
        exchange_position_key=position.exchange_position_key,
    )
    return position, cursor, evidence


def _router(monkeypatch: pytest.MonkeyPatch, *, swap_result: bool = False):
    factory_calls: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(
            self, *, account: str, connector: str,
            timeout_seconds: float,
        ) -> None:
            assert 0 < timeout_seconds <= 10
            factory_calls.append((account, connector))
            self.account = account
            self.connector = connector

        def __call__(self, position, cursor):
            assert cursor.symbol == position.symbol
            _pos, _cursor, evidence = _position_and_evidence(
                sub=position.strategy_id == SUB_ID
            )
            if swap_result:
                return replace(evidence, trading_account_id="ta-" + "f" * 32)
            return evidence

        def for_closed_boundary(self, position, closed_at):
            assert closed_at >= position.fill_at
            return self(position, replace(
                _position_and_evidence(
                    sub=position.strategy_id == SUB_ID
                )[1],
                traded_at=closed_at,
            ))

    monkeypatch.setattr(module, "P90PrivateProofClient", FakeClient)
    router = module.P90RealPrivateEvidenceRouter(
        account_by_strategy={MAIN_ID: MAIN, SUB_ID: SUB},
        connector_by_strategy={
            MAIN_ID: MAIN_CONNECTOR,
            SUB_ID: SUB_CONNECTOR,
        },
    )
    return router, factory_calls


@pytest.mark.parametrize("sub", (False, True))
def test_real_native_and_closed_event_route_to_exact_account_and_connector(
    monkeypatch: pytest.MonkeyPatch, sub: bool,
) -> None:
    router, calls = _router(monkeypatch)
    position, cursor, evidence = _position_and_evidence(sub=sub)
    assert calls == [(MAIN, MAIN_CONNECTOR), (SUB, SUB_CONNECTOR)]
    assert router(position, cursor) == evidence
    assert router.for_closed_boundary(
        position, cursor.traded_at + timedelta(minutes=5),
    ) == evidence


def test_foreign_signed_response_never_crosses_account_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router, _calls = _router(monkeypatch, swap_result=True)
    position, cursor, _evidence = _position_and_evidence()
    with pytest.raises(P90PrivateIpcUnverified, match="ROUTED_EVIDENCE_SCOPE"):
        router(position, cursor)


@pytest.mark.parametrize("changes", (
    {"trading_account_id": SUB},
    {"strategy_version": "old"},
    {"identity_status": "LEGACY_UNKNOWN"},
    {"strategy_execution_binding_id": None},
    {"symbol": "BTCUSDT"},
    {"strategy_id": "fake-strategy"},
))
def test_invalid_strategy_binding_rejected_before_private_call(
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, object],
) -> None:
    router, _calls = _router(monkeypatch)
    position, cursor, _evidence = _position_and_evidence()
    changed = replace(position, **changes)
    with pytest.raises(P90PrivateIpcUnverified, match="POSITION_ACCOUNT_UNPROVEN"):
        router(changed, cursor)


@pytest.mark.parametrize(("accounts", "connectors"), (
    ({MAIN_ID: MAIN}, {MAIN_ID: MAIN_CONNECTOR, SUB_ID: SUB_CONNECTOR}),
    ({MAIN_ID: MAIN, SUB_ID: MAIN},
     {MAIN_ID: MAIN_CONNECTOR, SUB_ID: SUB_CONNECTOR}),
    ({MAIN_ID: MAIN, SUB_ID: SUB}, {MAIN_ID: MAIN_CONNECTOR}),
    ({MAIN_ID: MAIN, SUB_ID: SUB},
     {MAIN_ID: MAIN_CONNECTOR, SUB_ID: MAIN_CONNECTOR}),
))
def test_missing_or_colliding_main_sub_connectors_fail_on_construction(
    accounts: dict[str, str], connectors: dict[str, str],
) -> None:
    with pytest.raises(P90PrivateIpcUnverified, match="MAIN_SUB_BINDINGS"):
        module.P90RealPrivateEvidenceRouter(
            account_by_strategy=accounts,
            connector_by_strategy=connectors,
        )
