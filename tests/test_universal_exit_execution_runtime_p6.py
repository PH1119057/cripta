from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
CONSUMER = (ROOT / "operations/connectivity/universal_exit_consumer.py").read_text(encoding="utf-8")


def test_private_runtime_has_dedicated_typed_strategy_exit_path() -> None:
    start = PRIVATE.index("def _execute_universal_exit_command(")
    end = PRIVATE.index("\ndef execute_command(", start)
    block = PRIVATE[start:end]
    assert "validate_exit_mutation(" in block
    assert '"/v5/position/trading-stop"' in block
    assert '"/v5/order/create"' in block
    assert "reduceOnly" in block
    assert "closeOnTrigger" in block
    for forbidden in (
        "protection_plan(",
        "calculate_protection_plan(",
        "break_even",
        "current_stop",
        "distance_pct",
        'or "0.2"',
        "stop_loss_pct",
        "take_profit_pct",
    ):
        assert forbidden not in block


def test_private_runtime_revalidates_exact_position_before_exit_mutation() -> None:
    start = PRIVATE.index("def _universal_exit_position(")
    end = PRIVATE.index("\ndef _execute_universal_exit_command(", start)
    block = PRIVATE[start:end]
    for token in (
        "strategy_position_id",
        "account_ref",
        "exchange_position_key",
        "position_idx",
        "strategy_config_fingerprint",
        "exit_plan_fingerprint",
        "STRATEGY_EXIT_POSITION_OWNER_MISMATCH",
        "STRATEGY_EXIT_REQUEST_EXPIRED",
    ):
        assert token in block


def test_ambiguous_exchange_mutation_marks_exit_reconciliation_required() -> None:
    start = PRIVATE.index("def handle_exchange_mutation_barrier(")
    end = PRIVATE.index("\ndef quantize(", start)
    block = PRIVATE[start:end]
    assert "mark_ambiguous_exit_command(" in block
    assert "reconcile(connection, key, secret" in block
    assert "os._exit(75)" in block


def test_exit_consumer_gate_is_only_before_dispatch_not_request_materialization() -> None:
    materialize = CONSUMER.index("def materialize_once(")
    dispatch = CONSUMER.index("def dispatch_once(")
    run_once = CONSUMER.index("def run_once(")
    materialize_block = CONSUMER[materialize:dispatch]
    dispatch_block = CONSUMER[dispatch:run_once]
    assert "_execution_gate_enabled" not in materialize_block
    assert "persist_exit_execution_request(" in materialize_block
    assert "_execution_gate_enabled" in dispatch_block
    assert "publish_runtime_exit_command(" in dispatch_block


def test_exit_consumer_is_explicitly_disarmed_by_default() -> None:
    assert 'CRIPTA_UNIVERSAL_EXIT_MAINNET_CONSUMER", "DISABLED"' in CONSUMER
    assert 'CONSUMER_ARM != "ENABLED"' in CONSUMER
