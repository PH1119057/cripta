from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
CONSUMER = (ROOT / "operations/connectivity/universal_entry_consumer.py").read_text(
    encoding="utf-8"
)


def test_private_runtime_uses_exact_universal_position_binding() -> None:
    assert "load_universal_entry_lineage(" in PRIVATE
    assert "persist_universal_strategy_position(" in PRIVATE
    assert "Universal Entry fill lost durable request lineage" in PRIVATE
    assert "universal_lineage is not None" in PRIVATE


def test_private_runtime_releases_bound_capital_only_from_position_close_path() -> None:
    assert PRIVATE.count("release_position_capital_reservation(") == 2
    assert "entry_execution_request_id" in PRIVATE
    assert "close_link_status='UNRESOLVED_EXACT_LINK'" in PRIVATE
    assert "close_link_status='EXACT'" in PRIVATE


def test_universal_consumer_revalidates_claim_before_command_publish() -> None:
    validation_pos = CONSUMER.index(
        "admission_status, admission_detail = _admission_pre_dispatch_status"
    )
    publish_pos = CONSUMER.index("_publish_command(connection, prepared")
    assert validation_pos < publish_pos
    assert "EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN" in CONSUMER
    assert "EXCHANGE_POSITION_MODE_MISMATCH" in CONSUMER
    assert "_release_pre_exchange_admission(" in CONSUMER
    assert "_mark_admission_reconciliation_required(" in CONSUMER
    assert "PENDING_ENTRY_COMMAND" in CONSUMER
    assert "PENDING_ENTRY_ORDER" in CONSUMER
    assert "EXCHANGE_POSITION:" in CONSUMER
    assert "OWNED_POSITION:" in CONSUMER
