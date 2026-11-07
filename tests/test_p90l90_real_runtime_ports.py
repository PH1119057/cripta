"""REAL Exit runtime composition contracts; no DB mutation or Exchange calls."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_account_routes import (
    P90RealAccountRoute,
    P90RealAccountRoutes,
    P90RealAccountRoutesUnverified,
)
from bybit_workbench.universal_exit import p90l90_real_runtime_ports as runtime_mod
from bybit_workbench.universal_exit.p90l90_real_runtime_ports import (
    P90RealExitRuntimePorts,
    P90RealRuntimeCompositionUnverified,
    assemble_p90_real_exit_runtime,
)

MAIN = "ta-" + "1" * 32
SUB = "ta-" + "2" * 32
ACCOUNT_BY_STRATEGY = {MAIN_ID: MAIN, SUB_ID: SUB}
CONNECTOR_BY_STRATEGY = {
    MAIN_ID: "connector-p90-main", SUB_ID: "connector-p90-sub",
}


class _DB:
    def __init__(self) -> None:
        self.calls = 0

    @contextmanager
    def transaction(self):
        self.calls += 1
        raise AssertionError("constructor must not begin a transaction")
        yield

    def execute(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("constructor must not query or write PostgreSQL")


def _ports(db: _DB, **changes):
    kwargs = dict(
        source_commit="a" * 40,
        account_by_strategy=ACCOUNT_BY_STRATEGY,
        connector_by_strategy=CONNECTOR_BY_STRATEGY,
        independently_verified_native_coverage=lambda pos, cursor: (False, False),
        independently_verified_closed_history=lambda symbol, at: (),
        independently_verified_closed_continuity=lambda symbol, at: False,
        independently_verified_mutation_safety=lambda pos, ev, at: False,
    )
    kwargs.update(changes)
    return assemble_p90_real_exit_runtime(db, **kwargs)


def test_both_real_exit_event_types_share_exact_one_postgres_account_owner() -> None:
    db = _DB()
    ports = _ports(db)
    assert isinstance(ports, P90RealExitRuntimePorts)
    assert ports.on_native_trade._positions is ports.position_provider
    assert ports.on_closed_5m._positions is ports.position_provider
    assert ports.on_native_trade._mfe is ports.mfe_ledger
    assert ports.on_closed_5m._mfe is ports.mfe_ledger
    assert ports.on_native_trade._private is ports.private_evidence
    assert (
        ports.on_closed_5m._private
        == ports.private_evidence.for_closed_boundary
    )
    assert ports.on_native_trade._execution_port is ports.execution
    assert ports.on_closed_5m._execution_port is ports.execution
    assert ports.on_postfill._execution_port is ports.execution
    assert ports.on_postfill._positions is ports.position_provider
    assert ports.on_postfill._exit_store is ports.on_native_trade._exit_store
    assert ports.execution._db is db
    assert ports.mfe_ledger._db is db
    assert ports.position_provider._connection is db
    assert ports.private_evidence._accounts == ACCOUNT_BY_STRATEGY
    assert ports.private_evidence._connectors == CONNECTOR_BY_STRATEGY
    assert db.calls == 0


@pytest.mark.parametrize("row", [("completed",), ["completed"], {"state": "completed"}])
@pytest.mark.parametrize("pending_exit", [False, True])
def test_completed_entry_releases_exit_for_both_cursor_row_formats(
    monkeypatch, row, pending_exit,
):
    db = _DB()
    reads = []
    def execute(sql, params):
        reads.append((sql, params))
        return SimpleNamespace(fetchone=lambda: row)
    db.execute = execute
    monkeypatch.setattr(runtime_mod, "p90_real_exit_no_pending",
                        lambda *a, **kw: not pending_exit)
    ports = _ports(db)
    position = SimpleNamespace(entry_command_id="entry", trading_account_id=MAIN)
    assert ports.on_native_trade._pending(position) is pending_exit
    assert ports.on_closed_5m._pending(position) is pending_exit
    assert all(params == ("entry", MAIN) for _, params in reads)
    assert all("identity_status='PROVEN'" in sql for sql, _ in reads)


@pytest.mark.parametrize("row", [
    None, (), ("completed", "extra"), "completed", {}, {"state": "running"},
    ("queued",), ("running",), ("failed",), (None,),
])
def test_missing_malformed_or_unfinished_entry_keeps_exit_pending(monkeypatch, row):
    db = _DB()
    db.execute = lambda *a: SimpleNamespace(fetchone=lambda: row)
    monkeypatch.setattr(runtime_mod, "p90_real_exit_no_pending",
                        lambda *a, **kw: pytest.fail("unfinished Entry must remain blocked"))
    ports = _ports(db)
    position = SimpleNamespace(entry_command_id="entry", trading_account_id=MAIN)
    assert ports.on_native_trade._pending(position) is True
    assert ports.on_closed_5m._pending(position) is True


@pytest.mark.parametrize("short,sub", [(False, False), (True, True)])
def test_tuple_entry_completion_reaches_native_trailing_decision(monkeypatch, short, sub):
    from test_p90l90_real_observation import _scope

    from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
    from bybit_workbench.universal_exit.contracts import ExitActionKind, ExitEvaluationStatus

    position, plan, state, cursor, private, price = _scope(short=short, sub=sub)
    db = _DB()
    db.execute = lambda *a: SimpleNamespace(fetchone=lambda: ("completed",))
    monkeypatch.setattr(runtime_mod, "p90_real_exit_no_pending", lambda *a, **kw: True)
    ports = _ports(db)
    port = ports.on_native_trade
    port._positions = lambda _: ((position, plan),)
    port._coverage = lambda *a: (True, True)
    port._mfe = SimpleNamespace(on_trade=lambda **kw: state)
    port._private = lambda *a: private
    saved, commands = [], []
    port._exit_store = SimpleNamespace(record=lambda *a, **kw: saved.append(a[0]))
    port._execution_port = lambda *a: commands.append(a[0])
    fact = MarketFactEnvelope(
        fact_id="native-test", event_kind="PUBLIC_TRADE", symbol=position.symbol,
        event_at=cursor.traded_at, observed_at=cursor.traded_at,
        received_at=private.observed_at, source_refs=("bybit:publicTrade:test",),
        attributes=FrozenPolicy.from_mapping({"price": str(price)}),
    )
    result, = port(fact, cursor)
    assert result.status is ExitEvaluationStatus.DECISION_CREATED
    assert result.decision.action_kind is ExitActionKind.SET_TRAILING
    assert result.decision.requested_mutation.to_dict() == {
        "distance": "0.35", "active_price": "99.50" if short else "100.50",
        "tpsl_mode": "Full",
    }
    assert saved == commands == [result]


@pytest.mark.parametrize("missing", (
    "independently_verified_native_coverage",
    "independently_verified_closed_history",
    "independently_verified_closed_continuity",
    "independently_verified_mutation_safety",
))
def test_missing_independent_proof_blocks_before_db_or_exchange(
    missing: str,
) -> None:
    db = _DB()
    with pytest.raises(
        P90RealRuntimeCompositionUnverified,
        match="INDEPENDENT_SOURCE_AND_ACCOUNT_PORTS_REQUIRED",
    ):
        _ports(db, **{missing: None})
    assert db.calls == 0


@pytest.mark.parametrize("source", ("", "not-sha", "A" * 40, "a" * 39))
def test_source_commit_must_be_exact_lowercase_40_hex(source: str) -> None:
    db = _DB()
    with pytest.raises(P90RealRuntimeCompositionUnverified):
        _ports(db, source_commit=source)
    assert db.calls == 0


def test_cross_account_or_connector_alias_never_builds_real_runtime() -> None:
    db = _DB()
    for account, connector in (
        ({MAIN_ID: MAIN, SUB_ID: MAIN}, CONNECTOR_BY_STRATEGY),
        (ACCOUNT_BY_STRATEGY, {MAIN_ID: "same", SUB_ID: "same"}),
        ({MAIN_ID: MAIN}, CONNECTOR_BY_STRATEGY),
        (ACCOUNT_BY_STRATEGY, {MAIN_ID: "main-only"}),
        ({MAIN_ID: "ta-bogus", SUB_ID: SUB}, CONNECTOR_BY_STRATEGY),
    ):
        with pytest.raises((RuntimeError, ValueError)):
            _ports(
                db,
                account_by_strategy=account,
                connector_by_strategy=connector,
            )
    assert db.calls == 0


def test_bare_object_does_not_masquerade_as_shared_source_transaction() -> None:
    with pytest.raises(
        P90RealRuntimeCompositionUnverified,
        match="SHARED_POSTGRES_CONNECTION_REQUIRED",
    ):
        assemble_p90_real_exit_runtime(
            object(),
            source_commit="b" * 40,
            account_by_strategy=ACCOUNT_BY_STRATEGY,
            connector_by_strategy=CONNECTOR_BY_STRATEGY,
            independently_verified_native_coverage=lambda p, c: (False, False),
            independently_verified_closed_history=lambda s, at: (),
            independently_verified_closed_continuity=lambda s, at: False,
            independently_verified_mutation_safety=lambda p, e, at: False,
        )


def test_factory_consumes_exact_pg_roster_without_user_supplied_account_ids(
    monkeypatch,
) -> None:
    db = _DB()
    at = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
    def route(strategy, account, connector, kind, uid):
        return P90RealAccountRoute(
            strategy_id=strategy,
            activation_id="active-" + kind,
            strategy_config_fingerprint="exact-v3-" + kind,
            strategy_execution_binding_id="binding-" + kind,
            trading_account_id=account,
            account_connector_id=connector,
            account_kind=kind,
            registered_exchange_uid=uid,
        )
    roster = P90RealAccountRoutes(
        main=route(MAIN_ID, MAIN, "connector-main", "MAIN", "101"),
        sub=route(SUB_ID, SUB, "connector-sub", "SUB", "202"),
    )
    looked_up = []
    def resolver(connection, *, bundles, at):
        looked_up.append((connection, tuple(bundles), at))
        return roster
    monkeypatch.setattr(runtime_mod, "read_p90_real_account_routes", resolver)
    factory = runtime_mod.assemble_p90_real_exit_runtime_from_db_bundles
    args = dict(
        source_commit="a" * 40,
        independently_verified_native_coverage=lambda pos, cursor: (False, False),
        independently_verified_closed_history=lambda symbol, observed: (),
        independently_verified_closed_continuity=lambda symbol, observed: False,
        independently_verified_mutation_safety=lambda pos, ev, observed: False,
    )
    ports = factory(db, bundles=("MAIN", "SUB"), observed_at=at, **args)
    assert looked_up == [(db, ("MAIN", "SUB"), at)]
    assert ports.private_evidence._accounts == roster.account_by_strategy
    assert ports.private_evidence._connectors == roster.connector_by_strategy
    assert db.calls == 0

    def reject(*_a, **_k):
        raise P90RealAccountRoutesUnverified("NO_EXACT_ACTIVE_BINDINGS")
    monkeypatch.setattr(runtime_mod, "read_p90_real_account_routes", reject)
    with pytest.raises(P90RealAccountRoutesUnverified, match="NO_EXACT_ACTIVE_BINDINGS"):
        factory(db, bundles=("MAIN", "SUB"), observed_at=at, **args)
    assert db.calls == 0
