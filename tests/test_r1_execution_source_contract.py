from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_private_runtime_supports_postonly_for_offset_entry() -> None:
    source = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert 'payload.get("entry_time_in_force") or "GTC"' in source
    assert 'bybit_time_in_force = "PostOnly"' in source
    assert 'order.update({"price": str(price), "timeInForce": bybit_time_in_force})' in source


def test_hard_exit_path_remains_immediate_market_reduce_only() -> None:
    source = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert '"orderType": "Market"' in source
    assert '"reduceOnly": True' in source


def test_private_runtime_dropin_exposes_safety_observer_module() -> None:
    source = (ROOT / "operations/systemd/cripta-private-runtime.service.d/10-pythonpath.conf").read_text(encoding="utf-8")
    assert ":/srv/cripta/runtime/current/research/server/connectivity" in source
    assert (ROOT / "research/server/connectivity/safety_observer.py").is_file()


def test_private_runtime_dropin_selects_universal_entry() -> None:
    source = (ROOT / "operations/systemd/cripta-private-runtime.service.d/10-pythonpath.conf").read_text(encoding="utf-8")
    assert "Environment=CRIPTA_ENTRY_COMMAND_SOURCE=UNIVERSAL_ENTRY" in source


def test_private_runtime_position_mode_freshness_window() -> None:
    source = (ROOT / "operations/systemd/cripta-private-runtime.service.d/10-pythonpath.conf").read_text(encoding="utf-8")
    assert "Environment=CRIPTA_POSITION_MODE_REFRESH_SECONDS=30" in source
    assert "Environment=CRIPTA_POSITION_MODE_FRESHNESS_SECONDS=90" in source


def test_r1_initial_entry_protection_uses_exact_opposite_inner_target() -> None:
    strategy = (ROOT / "src/bybit_workbench/universal_entry/r1_strategy.py").read_text(
        encoding="utf-8"
    )
    runtime = (ROOT / "operations/connectivity/private_runtime.py").read_text(
        encoding="utf-8"
    )
    assert '"take_profit_enabled": True' in strategy
    assert '"take_profit_reference_path": "fact.r1_opposite_inner_target"' in strategy
    assert "resolve_initial_protection_boundaries(" in runtime
    assert 'if target is not None:' in runtime
    assert '"takeProfit": str(target)' in runtime
    assert 'take_profit_pct=contract["take_profit_pct"]' not in runtime


def test_r1_initial_tp_is_price_based_not_fixed_percent() -> None:
    strategy = (ROOT / "src/bybit_workbench/universal_entry/r1_strategy.py").read_text(
        encoding="utf-8"
    )
    protection = strategy.split('"protection_policy": {', 1)[1].split(
        '"lifecycle_policy": {', 1
    )[0]
    assert '"take_profit_reference_path": "fact.r1_opposite_inner_target"' in protection
    assert '"take_profit_pct"' not in protection


def test_r1_version_install_migrates_superseded_monitoring_only_while_disarmed() -> None:
    source = (
        ROOT / "operations/implementation/install_r1_cards.py"
    ).read_text(encoding="utf-8")
    first_gate_check = source.index("DISARMED_INSTALL_INVARIANT: mainnet gate is open")
    first_card_write = source.index("entry_store.insert_strategy_card(card)")
    assert first_gate_check < first_card_write
    assert "MONITOR_SUPERSEDED" in source
    assert "entry_store.set_activation_enabled(" in source
    assert "enabled=False" in source
    assert "strategy_version=%s" in source
    assert "strategy_config_fingerprint=%s" in source


def test_r1_signal_validity_uses_exact_observer_history_window() -> None:
    bridge = (ROOT / "src/bybit_workbench/universal_entry/execution_bridge.py").read_text(
        encoding="utf-8"
    )
    runtime = (ROOT / "operations/connectivity/private_runtime.py").read_text(
        encoding="utf-8"
    )
    assert 'from .market_watch import _derived_history_limit' in bridge
    assert '"history_limit": (' in bridge
    assert 'entry_policy.get("watch_policy")' in bridge
    assert 'history_limit_raw = validity.get("history_limit")' in runtime
    assert 'signal_structure_low' in bridge and 'signal_structure_high' in bridge
    assert 'current_low != original_low or current_high != original_high' in runtime
    assert 'current_entry != original_entry' not in runtime
    assert 'R1 validity lacks full causal ATR history' in runtime


def test_strategy_cancel_intent_is_durable_before_bybit_cancel_and_ws_release() -> None:
    runtime = (ROOT / "operations/connectivity/private_runtime.py").read_text(
        encoding="utf-8"
    )
    cancel = runtime.split("def _cancel_entry_limit(", 1)[1].split(
        "def cancel_expired_entry_limits(", 1
    )[0]
    assert "INSERT INTO runtime.entry_cancel_intents" in cancel
    assert cancel.index("INSERT INTO runtime.entry_cancel_intents") < cancel.index(
        '"/v5/order/cancel"'
    )
    assert cancel.index("connection.commit()") < cancel.index('"/v5/order/cancel"')
    handler = runtime.split("def handle_private(", 1)[1].split(
        "def private_loop(", 1
    )[0]
    assert "FROM runtime.entry_cancel_intents" in handler
    assert handler.index("FROM runtime.entry_cancel_intents") < handler.index(
        "resolve_cancelled_entry_reservation_after_reconcile("
    )


def test_r1_cancel_intent_migration_is_registered_and_scoped() -> None:
    migration = (
        ROOT / "operations/sql/20261008_r1_entry_cancel_intents.sql"
    ).read_text(encoding="utf-8")
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS runtime.entry_cancel_intents" in migration
    assert "GRANT SELECT, INSERT ON runtime.entry_cancel_intents TO cripta" in migration
    assert "20261008_r1_entry_cancel_intents.sql" in installer


def test_counterfactual_capture_timestamp_is_not_earlier_than_decision() -> None:
    source = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(
        encoding="utf-8"
    )
    assert 'captured_at=max(datetime.now(UTC), evaluation.decision.decided_at)' in source
    assert 'captured_at=fact.observed_at' not in source
