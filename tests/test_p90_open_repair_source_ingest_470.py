"""P90 OPEN recovery failure must not kill independent public source ingest.

A read/append-only fallback is permitted only for the exact disarmed,
single-owner situation; it is NOT a substitute for Exchange protection or
Exit/MFE proof, and never establishes Observer READY.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from operations.monitoring.universal_entry_shadow import (
    _p90_expected_initial_cursor_wait,
    _p90_native_recovery_failure,
    _scoped_p90_ordinary_isolation_allowed,
    _scoped_p90_repair_ingest_allowed,
)

SOURCE = Path("operations/monitoring/universal_entry_shadow.py")


def test_disarmed_unambiguous_p90_open_repair_can_ingest_new_ws_facts() -> None:
    assert _scoped_p90_repair_ingest_allowed(
        degraded=True,
        mainnet_gate_enabled=False,
        selected_real_activations=frozenset(),
        paper_consumer_count=0,
    )


@pytest.mark.parametrize("changed", (
    {"degraded": False},
    {"mainnet_gate_enabled": True},
    {"selected_real_activations": frozenset({"p90-main"})},
    {"paper_consumer_count": 1},
))
def test_repair_source_only_refuses_financial_or_mixed_consumer_scope(
    changed: dict[str, object],
) -> None:
    args: dict[str, object] = {
        "degraded": True,
        "mainnet_gate_enabled": False,
        "selected_real_activations": frozenset(),
        "paper_consumer_count": 0,
    }
    args.update(changed)
    assert not _scoped_p90_repair_ingest_allowed(**args)  # type: ignore[arg-type]


def test_repair_ingest_is_after_journal_but_before_any_entry_exit_consumer() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    epoch = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_observer_epoch"
    )
    process = next(
        node for node in ast.walk(epoch)
        if isinstance(node, ast.FunctionDef) and node.name == "_process_fact_body"
    )
    body = ast.get_source_segment(source, process)
    assert body is not None
    assert body.index(
        "if p90_repair_ingest_only and not observer_split.ordinary:"
    ) < body.index(
        "refresh_inputs(fact.observed_at)"
    )
    # Native append belongs to the existing MarketSource source transaction,
    # never a second producer or a P90 source checkpoint spoof.
    dispatch = next(
        node for node in ast.walk(epoch)
        if isinstance(node, ast.FunctionDef) and node.name == "process_fact"
    )
    dispatch_body = ast.get_source_segment(source, dispatch)
    assert dispatch_body is not None
    assert "NativePublicTradeJournal(connection).append(" in dispatch_body
    assert "with connection.transaction():" in dispatch_body
    assert "dispatch_observer_fact_atomically(" in dispatch_body


def test_open_position_source_repair_cannot_be_reported_as_financial_ready() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    status = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_observer_status"
    )
    body = ast.get_source_segment(source, status)
    assert body is not None
    assert '"observer_ready": state in {"IDLE", "WARMUP", "RUNNING", "RELOADING"}' in body
    assert "SOURCE_REPAIR_ONLY" not in body

    epoch = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_observer_epoch"
    )
    runtime = ast.get_source_segment(source, epoch)
    assert runtime is not None
    assert '"SOURCE_REPAIR_ONLY"' in runtime
    assert "if p90_repair_ingest_only and not observer_split.ordinary" in runtime
    assert "P90_OPEN_EXIT_BLOCKED_MARKET_SOURCE_INGEST_ONLY" in runtime
    assert "active_bundles=active_source_consumers" in runtime
    assert "P90_OPEN_EXIT_BLOCKED_ORDINARY_CONSUMERS_ACTIVE" in runtime
    assert "tuple(observer_split.ordinary) if p90_repair_ingest_only else bundles" in runtime
    # Recovery exception classification is intentionally centralized in
    # _p90_native_recovery_failure(), not duplicated inside this epoch.
    # Old-epoch P90 failure must escape to the one outer durable fault
    # handler, which restarts a *fresh* source-only WS session.
    assert "preflight_restart_epoch_id = inspect_p90_real_open_restart_epoch(" in runtime
    assert "_record_observer_runtime_fault(" not in runtime
    assert "p90_real_live = False" in runtime


def test_only_exact_p90_recovery_failures_can_trigger_repair_source_restart() -> None:
    from bybit_workbench.universal_entry.p90l90_real_bounded_native_restart import (
        P90RealNativeRestartUnverified,
    )
    from bybit_workbench.universal_entry.p90l90_real_open_restart import (
        P90RealOpenRestartUnverified,
    )
    from bybit_workbench.universal_entry.p90l90_real_restart_recovery import (
        P90RealOpenSourceRecoveryUnverified,
    )
    from bybit_workbench.universal_entry.p90l90_real_source_compatibility import (
        P90RealSourceCompatibilityUnverified,
    )
    from bybit_workbench.universal_exit.p90l90_real_mfe import (
        P90RealMfeUnverified,
    )
    from operations.monitoring.p90l90_real_live_source_ports import (
        P90RealLiveSourceUnverified,
    )

    for error in (
        P90RealOpenRestartUnverified("old source"),
        P90RealOpenSourceRecoveryUnverified("old native gap"),
        P90RealNativeRestartUnverified("missing original seq"),
        P90RealSourceCompatibilityUnverified("identity"),
        P90RealMfeUnverified("missing current MFE"),
        P90RealLiveSourceUnverified("P90_REAL_LIVE_SOURCE_NATIVE_EVENT_STALE"),
        P90RealLiveSourceUnverified("P90_REAL_LIVE_SOURCE_NATIVE_INTERVAL_UNPROVEN"),
    ):
        assert _p90_native_recovery_failure(error)

    for error in (
        RuntimeError("Unknown Exchange mutation"),
        ValueError("source integrity conflict"),
        ConnectionError("public WS unavailable"),
    ):
        assert not _p90_native_recovery_failure(error)


def test_exact_initial_cursor_wait_is_expected_only_for_source_preparation() -> None:
    from bybit_workbench.universal_entry.p90l90_real_restart_recovery import (
        P90RealOpenSourceRecoveryUnverified,
    )
    expected = P90RealOpenSourceRecoveryUnverified(
        "P90_REAL_RESTART_SOURCE_LIVE_MIRROR_FIVE_SYMBOL_CURSORS_MISSING"
    )
    assert _p90_expected_initial_cursor_wait(expected)
    assert not _p90_expected_initial_cursor_wait(
        P90RealOpenSourceRecoveryUnverified(
            "P90_REAL_RESTART_SOURCE_LIVE_MIRROR_SESSION_UNVERIFIED"
        )
    )
    assert not _p90_expected_initial_cursor_wait(
        P90RealOpenSourceRecoveryUnverified(
            "P90_REAL_RESTART_SOURCE_DURABLE_SAME_SEQUENCE_IDS_EVICTED"
        )
    )
    assert not _p90_expected_initial_cursor_wait(
        RuntimeError(str(expected))
    )


def test_expected_timeout_enters_guarded_preparation_before_fault_record() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    runner = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_multi_strategy_observer"
    )
    body = ast.get_source_segment(source, runner)
    assert body is not None
    guard = body.index("_p90_expected_initial_cursor_wait(exc)")
    fault = body.index("fault_id = _record_observer_runtime_fault(", guard)
    assert guard < fault
    scope = body[body.rfind("if (", 0, guard):fault]
    for required in (
        "not force_p90_source_repair",
        "len(repair_split.p90_real) == 2",
        "gate == (False,)",
        "exposure[0] > 0",
        "_scoped_p90_repair_ingest_allowed(",
        "force_p90_source_repair = True",
        '"SOURCE_REPAIR_PREPARING"',
        "continue",
    ):
        assert required in scope
    assert "P90_OPEN_EXIT_BLOCKED_EXPECTED_NATIVE_CURSOR_WAIT" in scope
    # Unknown/corrupt sources still call the existing durable fault owner.
    assert "_p90_native_recovery_failure(exc)" in body[fault:]


def test_late_p90_recovery_error_restarts_only_pure_source_rail() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    runner = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_multi_strategy_observer"
    )
    body = ast.get_source_segment(source, runner)
    assert body is not None
    assert "source_repair_only=force_p90_source_repair" in body
    assert "if _p90_native_recovery_failure(exc):" in body
    assert "force_p90_source_repair = True" in body
    assert "SOURCE_REPAIR_PREPARING" in body
    assert "fault_id = _record_observer_runtime_fault(" in body
    epoch = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_observer_epoch"
    )
    text = ast.get_source_segment(source, epoch)
    assert text is not None
    assert "source_repair_only: bool = False" in text
    assert "p90_real_force_source_only" in text
    assert "P90_SCOPED_SOURCE_REPAIR_FINANCIAL_SCOPE_INVALID" in text
    assert "if source_repair_only:" in text
    assert "if p90_repair_ingest_only and not observer_split.ordinary:" in text
    assert "tuple(sorted(ordinary_symbols)) if p90_repair_ingest_only else symbols" in text
    assert "if not p90_repair_ingest_only or observer_split.ordinary:" in text
    assert "and not p90_repair_ingest_only" in text
    assert "for bundle in cast(Any, active_source_consumers)" in text


def test_scoped_p90_fault_keeps_unrelated_ordinary_consumer_ownership() -> None:
    # Ordinary strategy cardinality is intentionally NOT an exclusion gate.
    # Its EntryPlan/geometry/OI remains owned by the original Engine. Only
    # native P90 PAPER cannot be disconnected through this specific route.
    assert _scoped_p90_repair_ingest_allowed(
        degraded=True,
        mainnet_gate_enabled=False,
        selected_real_activations=frozenset(),
        paper_consumer_count=0,
    )
    source = SOURCE.read_text(encoding="utf-8")
    epoch = next(
        n for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and n.name == "_run_observer_epoch"
    )
    body = ast.get_source_segment(source, epoch)
    assert body is not None
    assert "if p90_repair_ingest_only and not observer_split.ordinary:" in body
    assert "engine.process(" in body
    assert "p90_consumer = runtime.consumer" in body  # normal REAL path only
    assert "if p90_real_source_only or p90_real_live:" in body
    assert '"SOURCE_REPAIR_ONLY"' in body
    assert "active_bundles=active_source_consumers" in body


def test_source_repair_oi_config_never_constructs_an_empty_symbol_set() -> None:
    """Evaluate the real OI argument for pure ingest and mixed consumers."""
    from bybit_workbench.universal_entry.oi30s_source import Oi30sConfig

    source = SOURCE.read_text(encoding="utf-8")
    epoch = next(
        n for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and n.name == "_run_observer_epoch"
    )
    config = next(
        n for n in ast.walk(epoch)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "oi_config" for t in n.targets)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "Oi30sConfig"
    )
    required = next(
        keyword.value for keyword in config.value.keywords
        if keyword.arg == "required_symbols"
    )
    expr = compile(ast.Expression(required), str(SOURCE), "eval")
    for repairing, ordinary, expected in (
        (False, set(), ("APTUSDT", "BTCUSDT")),
        (True, set(), ("APTUSDT", "BTCUSDT")),
        (True, {"BTCUSDT"}, ("BTCUSDT",)),
    ):
        result = eval(
            expr,
            {"tuple": tuple, "sorted": sorted},
            {
                "p90_repair_ingest_only": repairing,
                "ordinary_symbols": ordinary,
                "symbols": ("APTUSDT", "BTCUSDT"),
            },
        )
        assert result == expected
        assert Oi30sConfig(
            fact_source_id="BYBIT_PUBLIC_REST_CURRENT_OI_30S_V1",
            required_symbols=result,
        ).required_symbols == expected


@pytest.mark.parametrize("ordinary_count", (0, 1, 2))
def test_p90_scoped_repair_executes_independent_ordinary_fact_path(
    ordinary_count: int,
) -> None:
    """Execute the actual epoch dispatch body, never an Exchange/DB worker."""
    from contextlib import nullcontext
    from datetime import UTC, datetime
    from types import SimpleNamespace

    original = ast.parse(SOURCE.read_text(encoding="utf-8"))
    epoch = next(
        node for node in original.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_observer_epoch"
    )
    processor = next(
        node for node in ast.walk(epoch)
        if isinstance(node, ast.FunctionDef) and node.name == "_process_fact_body"
    )
    # Preserve the production function's nonlocal counters and every branch:
    # only supply inert boundary ports to execute this real dispatch function.
    harness = ast.parse(
        "def make_processor():\n"
        "    facts_received = 0\n"
        "    evaluations_count = 0\n"
        "    signals_count = 0\n"
        "    return _process_fact_body, lambda: "
        "(facts_received, evaluations_count, signals_count)\n"
    )
    wrapper = harness.body[0]
    assert isinstance(wrapper, ast.FunctionDef)
    wrapper.body.insert(-1, processor)
    ast.fix_missing_locations(harness)

    calls: list[tuple[object, dict[str, object]]] = []
    oi_seen: set[str] = set()
    stored: list[object] = []
    paper_orders: list[tuple[object, object]] = []
    ordinary_bundle = SimpleNamespace(
        entry_plan=SimpleNamespace(
            watch_policy=SimpleNamespace(
                to_dict=lambda: {"geometry": {"operator": "R1"}},
            ),
        ),
    )
    ordinary_evaluation = SimpleNamespace(
        signal=SimpleNamespace(
            entry_plan_fingerprint="synthetic-ordinary-plan",
            strategy_activation_id="synthetic-ordinary-activation",
        ),
        decision=None,
    )

    def evaluate(fact: object, **kwargs: object) -> tuple[object, ...]:
        calls.append((fact, kwargs))
        return (ordinary_evaluation,) if ordinary_count else ()

    environment: dict[str, object] = {
        "p90_real_source_only": False,
        "p90_repair_ingest_only": True,
        "observer_split": SimpleNamespace(
            ordinary=tuple(object() for _ in range(ordinary_count)),
        ),
        "deduper": SimpleNamespace(accept=lambda _fact_id: True),
        "oi_seen": oi_seen,
        "refresh_inputs": lambda _at: None,
        "global_context": None,
        "coin_contexts": {},
        "capacity": None,
        "real_execution_selected_for": frozenset(),
        "real_admission_ready_for": frozenset(),
        "multi_account_candidates": {},
        "connection": SimpleNamespace(),
        "nullcontext": nullcontext,
        "TechnicalReadiness": (
            lambda ready, at, reason: SimpleNamespace(
                ready=ready, observed_at=at, reason=reason,
            )
        ),
        "engine": SimpleNamespace(process=evaluate),
        "store": SimpleNamespace(
            record_evaluation=lambda evaluation, provenance: stored.append(evaluation),
        ),
        "provenance": "synthetic-only",
        "bundle_by_entry_plan": {"synthetic-ordinary-plan": ordinary_bundle},
        "_maybe_record_reverse_transition": lambda *_args: None,
        "paper": SimpleNamespace(
            create_order=lambda evaluation, bundle, now: paper_orders.append(
                (evaluation, bundle)
            ),
        ),
        "entry_admission_port": None,
        "execution_bindings": {},
        "exchange_position_key": lambda account, symbol, index: (
            f"{account}:{symbol}:{index}"
        ),
        "TRADING_ACCOUNT_ID": "synthetic-no-exchange",
        "symbols": ("BTCUSDT", "APTUSDT"),
        "sensor_ready": lambda _at: True,
        "p90_consumer": None,
        "p90l90_symbols": frozenset(("APTUSDT",)),
    }
    exec(compile(harness, str(SOURCE), "exec"), environment)
    factory = environment["make_processor"]
    assert callable(factory)
    dispatch, counters = factory()
    observed = SimpleNamespace(
        event_kind="OPEN_INTEREST",
        fact_id="synthetic-btc-oi-in-independent-observer",
        symbol="BTCUSDT",
        observed_at=datetime(2026, 11, 9, tzinfo=UTC),
    )
    dispatch(observed)
    assert counters() == (
        (1, 0, 0) if ordinary_count == 0 else (1, 1, 1)
    )
    assert oi_seen == {"BTCUSDT"}
    assert stored == ([] if ordinary_count == 0 else [ordinary_evaluation])
    assert paper_orders == (
        [] if ordinary_count == 0
        else [(ordinary_evaluation, ordinary_bundle)]
    )
    # Zero ordinary Plans = pure native-source repair, not even an
    # evaluation. One/multiple ordinary Plans still reach their real Engine
    # while the scoped P90 consumer is absent and has no financial power.
    assert len(calls) == (0 if ordinary_count == 0 else 1)
    if calls:
        fact, kwargs = calls[0]
        assert fact is observed
        assert kwargs["allow_new_signals"] is True
        assert kwargs["real_admission_required_for"] == frozenset()
        assert kwargs["real_admission_ready_for"] == frozenset()
        assert getattr(kwargs["technical_readiness"], "ready", None) is True


def test_p90_repair_selects_only_ordinary_active_consumer_plans() -> None:
    """Execute production active-consumer selector; P90 must remain blocked."""
    source = ast.parse(SOURCE.read_text(encoding="utf-8"))
    epoch = next(
        node for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_observer_epoch"
    )
    selector = next(
        node for node in ast.walk(epoch)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "active_source_consumers"
            for target in node.targets
        )
    )
    expression = compile(ast.Expression(selector.value), str(SOURCE), "eval")
    ordinary = object()
    blocked_p90 = object()
    split = type("ObserverSplit", (), {"ordinary": (ordinary,)})()
    bundles = (ordinary, blocked_p90)
    assert eval(
        expression,
        {"tuple": tuple},
        {
            "observer_split": split,
            "bundles": bundles,
            "p90_repair_ingest_only": True,
        },
    ) == (ordinary,)
    assert eval(
        expression,
        {"tuple": tuple},
        {
            "observer_split": split,
            "bundles": bundles,
            "p90_repair_ingest_only": False,
        },
    ) == bundles


def test_ordinary_isolation_after_p90_failure_has_no_financial_grant() -> None:
    # P90 selected REAL permissions do NOT become an ordinary Entry binding.
    # The isolated bundle set must contain only unselected SHADOW/PAPER Plans.
    good = {
        "p90_real_count": 2,
        "ordinary_count": 1,
        "paper_consumer_count": 0,
        "mainnet_gate_enabled": False,
        "selected_ordinary_activations": frozenset(),
    }
    assert _scoped_p90_ordinary_isolation_allowed(**good)
    for change in (
        {"p90_real_count": 1},
        {"p90_real_count": 0},
        {"ordinary_count": 0},
        {"paper_consumer_count": 1},
        {"mainnet_gate_enabled": True},
        {"selected_ordinary_activations": frozenset({"ordinary-real"})},
    ):
        scope = dict(good)
        scope.update(change)
        assert not _scoped_p90_ordinary_isolation_allowed(**scope)


def test_p90_fault_ordinary_only_route_never_includes_p90_consumer() -> None:
    """Guard the actual runner epoch selection and fail-closed P90 isolation."""
    source = SOURCE.read_text(encoding="utf-8")
    module = ast.parse(source)
    runner = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_multi_strategy_observer"
    )
    body = ast.get_source_segment(source, runner)
    assert body is not None
    for expected in (
        "force_p90_ordinary_isolation = False",
        "ordinary_isolation_bundles = isolated_split.ordinary",
        "selected_ordinary = _real_execution_selected_activation_ids(",
        "_scoped_p90_ordinary_isolation_allowed(",
        "source_repair_only=force_p90_source_repair,",
        "isolated_p90_block=force_p90_ordinary_isolation,",
        "P90_ORDINARY_ISOLATION_SCOPE_DRIFT",
        "P90_OPEN_EXIT_BLOCKED_ORDINARY_CONSUMERS_PREPARING",
        "P90_REAL_NATIVE_WAIT_FOR_EVIDENCE",
    ):
        assert expected in body
    assert body.index(
        "fault_id = _record_observer_runtime_fault("
    ) < body.index("force_p90_ordinary_isolation = True")
    assert body.index("ordinary_isolation_bundles = isolated_split.ordinary") < (
        body.index("outcome = _run_observer_epoch(")
    )
    assert "wait_blocked(connection, {" in body
    epoch = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_observer_epoch"
    )
    epoch_body = ast.get_source_segment(source, epoch)
    assert epoch_body is not None
    assert "P90_ORDINARY_ISOLATION_TOPOLOGY_INVALID" in epoch_body
    assert "if isolated_p90_block" in epoch_body
    assert "P90_OPEN_EXIT_BLOCKED_ORDINARY_CONSUMERS_ACTIVE" in epoch_body
    assert "if isolated_p90_block else" in epoch_body


def test_ordinary_isolation_refuses_unknown_or_open_global_gate_from_real_code() -> None:
    """Execute both runner gate expressions, not a copied scope predicate."""
    source = SOURCE.read_text(encoding="utf-8")
    runner = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_multi_strategy_observer"
    )
    scopes = [
        node for node in ast.walk(runner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_scoped_p90_ordinary_isolation_allowed"
    ]
    # Once on the next epoch's admission boundary, once after a typed fault.
    assert len(scopes) == 2
    for scope in scopes:
        expression = next(
            keyword.value for keyword in scope.keywords
            if keyword.arg == "mainnet_gate_enabled"
        )
        executable = compile(ast.Expression(expression), str(SOURCE), "eval")
        for gate_row, expected in (
            ((False,), False),
            ((0,), False),
            ((True,), True),
            (None, True),
            ((), True),
        ):
            assert eval(executable, {}, {"gate_now": gate_row}) is expected
