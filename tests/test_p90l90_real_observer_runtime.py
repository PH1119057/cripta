"""No-order wiring tests for exact REAL Observer to independent Exit owners."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry import p90l90_real_observer_runtime as module
from bybit_workbench.universal_entry.contracts import FrozenPolicy

AT = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)


def _args():
    return {
        "bundles": (object(), object()),
        "source_commit": "a" * 40,
        "source_epoch_id": "p90-real-epoch-" + "e" * 40,
        "provenance": FrozenPolicy.from_mapping({"source": "real-test"}),
        "observed_at": AT,
        "admitted_account": lambda symbol, now: ((), False),
        "verified_native_coverage": lambda position, cursor: (False, False),
        "verified_closed_history": lambda symbol, at: (),
        "verified_closed_continuity": lambda symbol, at: False,
        "verified_account_mutation_safety": lambda position, ev, now: False,
    }


def test_real_observer_routes_both_native_and_5m_to_exact_real_exit_ports(
    monkeypatch,
) -> None:
    class DB:
        def __init__(self):
            self.info = object()

        def execute(self, *_args, **_kwargs):
            raise AssertionError("wiring must not query DB")

        def cursor(self, *_args, **_kwargs):
            raise AssertionError("wiring must not fetch Exit rows")

        def transaction(self):
            raise AssertionError("wiring must not open transaction")

    db = DB()
    seen = []
    def native(*_args):
        return None

    def closed(*_args):
        return None

    ports = SimpleNamespace(on_native_trade=native, on_closed_5m=closed)

    def fake_exit(connection, **kw):
        seen.append(("exit", connection, kw))
        return ports

    def fake_consumer(connection, **kw):
        seen.append(("consumer", connection, kw))
        return SimpleNamespace(connection=connection, **kw)

    monkeypatch.setattr(
        module, "assemble_p90_real_exit_runtime_from_db_bundles", fake_exit
    )
    monkeypatch.setattr(module, "P90L90ObserverRealConsumer", fake_consumer)
    args = _args()
    shared_candle_ledger = object()
    args["shared_candle_ledger"] = shared_candle_ledger
    composed = module.assemble_p90_real_observer_runtime(db, **args)
    assert composed.consumer.shared_candle_ledger is shared_candle_ledger
    assert composed.exit_ports is ports
    assert composed.consumer.verified_exit_tick_port is native
    assert composed.consumer.verified_closed_candle_port is closed
    assert composed.consumer.admission_provider is args["admitted_account"]
    assert seen[0][0] == "exit" and seen[1][0] == "consumer"
    assert seen[0][1] is db and seen[1][1] is db
    assert seen[0][2]["exit_connection"].source_connection is db
    assert seen[0][2]["bundles"] is args["bundles"]
    assert seen[0][2]["source_commit"] == args["source_commit"]
    assert seen[0][2]["independently_verified_native_coverage"] is args[
        "verified_native_coverage"
    ]
    assert seen[0][2]["independently_verified_mutation_safety"] is args[
        "verified_account_mutation_safety"
    ]


@pytest.mark.parametrize("missing", (
    "admitted_account",
    "verified_native_coverage",
    "verified_closed_history",
    "verified_closed_continuity",
    "verified_account_mutation_safety",
))
def test_real_observer_requires_every_independent_port_before_any_side_effect(
    monkeypatch, missing: str,
) -> None:
    calls = []
    monkeypatch.setattr(
        module, "assemble_p90_real_exit_runtime_from_db_bundles",
        lambda *_args, **_kwargs: calls.append("EXIT"),
    )
    args = _args()
    args[missing] = None
    with pytest.raises(
        module.P90RealObserverWiringUnverified,
        match="ALL_INDEPENDENT_PORTS_REQUIRED",
    ):
        module.assemble_p90_real_observer_runtime(object(), **args)
    assert calls == []


def test_real_observer_does_not_accept_single_account_as_both_strategies() -> None:
    args = _args()
    args["bundles"] = (object(),)
    with pytest.raises(module.P90RealObserverWiringUnverified):
        module.assemble_p90_real_observer_runtime(object(), **args)
